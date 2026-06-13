# Debug Session: hermes-cli-stuck

- Status: OPEN
- Date: 2026-06-13
- Scope: 开发版 `npm start` 下，聊天选择 Hermes 后长期停留在“已启动，正在处理”，没有任何输出。

## Symptoms

- 用户在聊天页选择 `Hermes` 后，界面显示 `Hermes CLI 已启动，正在处理...`
- 长时间没有新的终端输出
- 需要确认消息是否真的交给了 Hermes CLI，以及具体卡在启动、参数、子进程、stdout 读取还是 Hermes 自身执行阶段

## Hypotheses

1. Hermes CLI 子进程实际上没有成功启动，前端只收到了 Codebot 预先发送的“已启动”状态事件。
2. Hermes CLI 已启动，但参数或工作目录错误，导致进程卡住且没有任何 stdout/stderr 输出。
3. Hermes CLI 已启动并执行，但输出被缓冲、未刷到 stdout，Codebot 读取不到。
4. Hermes CLI 在启动后卡在某个 hook、网络调用或模型请求阶段，既不退出也不输出。
5. 开发版 `npm start` 的 Electron/后端链路没有把 Hermes 运行时日志正确转发到前端聊天流。

## Evidence Plan

- 读取开发启动脚本与 Hermes 启动链路，确认复现命令
- 在不改业务逻辑的前提下运行开发版并复现
- 添加最小化插桩，只记录 Hermes 子进程启动、命令行、PID、stdout/stderr 读取、退出码与流式转发状态
- 用运行证据排除假设，再决定是否修复

## Findings

- 开发版 `npm start` 的 Hermes 聊天链路确实会启动真实 `hermes.exe`，不是前端假装进入处理态。
- 旧实现使用的是顶层 `hermes -z/--oneshot`。Hermes 上游源码 `hermes_cli/main.py` 明确注释该路径为“single-shot mode, stdout = final response only, bypasses cli.py entirely”。
- 这与 Codebot 想要的“像正常 CLI 一样处理并持续映射真实终端输出”的目标不一致，也是用户指出“没有参考 opencode cli 正常处理方式”的关键偏差。
- 在 Codebot 注入的同一套 Hermes 环境中直接手工运行 `hermes.exe` 时，插件发现阶段会出现 `logging.handlers` 的 `OSError: [Errno 9] Bad file descriptor` 日志错误；但 CLI 进程并非完全未启动。
- 将 Codebot 的命令拼装从 `-z ... --cli` 改为 Hermes 官方 `--cli chat -q ...` 后，开发版 `npm start` 下重新验证：
  - `POST /api/hermes/chat` 返回 200，并拿到真实 CLI 输出；
  - `POST /api/chat/send_stream` 且 `target=hermes` 返回 `session.status` 与连续 `content_delta`，后端日志也显示新入口已生效。
- 随后继续对 Hermes stdout 做适配层清洗：
  - 使用增量 UTF-8 解码，避免多字节中文跨 chunk 时被截断成乱码；
  - 过滤 ANSI 控制序列、Rich 边框、`Resume this session` / `session_id` / `Session` 等终端辅助行；
  - 开发版 `npm start` 下再次验证 `target=hermes` 的流式输出已从带边框和恢复提示的终端文本，收敛为仅保留正文的 `Hi there! How can I help you today?`。
- 根据用户进一步反馈“8 分钟没有任何反应”，继续补齐 Hermes 过程可视化：
  - 后端现在会把清洗后的 stdout 行额外映射为 `session.trace` 事件；
  - 在长时间无正文输出时，按非阻塞方式周期性发出 `session.idle` 心跳；
  - 前端把 `session.status` / `session.idle` / `session.trace` / `session.retry` 作为可见事件渲染。
- 运行验证：临时将 `CODEBOT_HERMES_IDLE_NOTICE_SECONDS=2`、`CODEBOT_HERMES_IDLE_NOTICE_REPEAT_SECONDS=2` 后重启开发版，`POST /api/chat/send_stream` 的 Hermes 链路已按顺序返回：
  - `session.status`
  - 多条 `session.idle`
  - `content_delta`
  - `session.trace`
  - `done`
  证明即便 Hermes 长时间无正文输出，聊天窗口也会持续提示后台仍在处理。
