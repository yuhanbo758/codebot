# Codebot

> 项目对话说明：选择“项目”后，每轮发送都会由 Codebot 在自身数据目录创建独立 Git 快照；聊天中的“撤销”会同时恢复该轮修改前的项目文件。未选择项目的普通对话不会创建版本快照。沙箱启用后，符合执行条件的 OpenCode 请求会使用独立沙箱工作目录。

> 成长候选说明：聊天识别到定时任务创建意图后，会先生成可编辑的成长候选，只有用户接受后才加入调度器。顶部按钮会显示待审数量并自动更新；记忆候选会过滤临时任务信息并合并重复或近义内容。

基于 OpenCode 的第三方能力工作台 - 所有聊天统一由 OpenCode 处理，Codebot 负责提供 MCP / Skills / 记忆 / 定时任务能力并展示结果

发布版本通过 GitHub Actions 在每次推送到 `main` 后自动递增并发布到 Releases。

## ✨ 特性

- 🤖 **OpenCode 主控**: 所有聊天消息统一交给 OpenCode 处理，支持多模型切换与原生工具流式事件展示
- 🔗 **Codebot 第三方化**: Codebot 自身以第三方 MCP 形式注册到 OpenCode，OpenCode 可直接调用 Codebot 的记忆、任务、技能与会话工具
- 🧭 **自主执行策略**: 默认优先自主决策与自动重试，减少把流程决策抛给用户
- 💾 **记忆系统**: SQLite + ChromaDB 持久化存储，支持上下文记忆和长期记忆
- 🧠 **自动记忆提取**: 从对话中自动识别并保存用户的习惯、偏好、个人信息等，无需手动触发
- 💡 **记忆提示**: 聊天输入时实时显示相关记忆提示，让 AI 回复更贴合用户背景
- 🔄 **连接状态刷新**: 聊天页顶部会显示 OpenCode / Bridge / MCP 代理状态，并支持手动刷新连接与模型列表
- ⏰ **定时任务**: 完整 Cron 表达式支持，AI 辅助创建任务
- 🔔 **通知系统**: 飞书/邮箱/应用内/系统桌面通知，用户可配置
- 📂 **记忆管理**: 归档查看、导出导入、自动清理、自动整理，支持按类别过滤
- 📝 **日志系统**: 详细执行日志，可配置保留期限
- 🌐 **局域网访问**: 支持通过 IP 地址远程访问
- 📱 **移动端适配**: 响应式设计，支持手机浏览器
- 🖥️ **跨平台**: Electron 桌面应用 + Web 应用
- 🛠️ **第三方技能系统**: `skills/` 中的 SKILL.md 会同步到 OpenCode 的技能目录，作为第三方技能直接被调用
- 🔌 **第三方 MCP 支持**: Codebot 会聚合并代理外部 MCP 服务器（尤其是魔搭 ModelScope MCP），再通过自身 MCP SSE 入口统一暴露给 OpenCode 调用
- 📚 **使用文档入口**: 设置页里的“文档”会直接读取本项目 `README.md`，作为 Codebot 的用户手册与上手入口
- 🏖️ **沙箱执行**: 工作目录隔离执行环境，AI 生成的代码在独立 `sandbox_workspace/` 目录中运行，无需 QEMU/Docker，开箱即用

## 🚀 快速开始

### 系统要求

- Python 3.11+
- Node.js 18+
- OpenCode CLI
  - `opencode serve` 建议监听 `http://127.0.0.1:11200`
### 安装

#### Windows

```bash
# 下载项目后，双击运行
scripts\install.bat
```

#### Linux/macOS

```bash
# 下载项目后，运行
chmod +x scripts/install.sh
./scripts/install.sh
```

### 启动

#### 先启动 OpenCode Serve（必需）

应用程序运行依赖 OpenCode CLI，需要开启 `opencode serve` 才能连接 OpenCode Server。

```bash
# Windows（打开 cmd 运行）
opencode serve

# 或指定端口与监听地址（推荐）
opencode serve --port 11200 --hostname 127.0.0.1
```

应用程序配置文件（设置 → 通用设置 → 配置文件 → `config.json`）里的 `server_url` 需要与 `opencode serve` 保持一致（端口/地址一致）。如果连不上，请优先检查该配置项。桌面端启动后端时会自动尝试拉起 OpenCode 服务，并统一优先使用 `127.0.0.1:11200`；`npm start` 开发模式也会跟正式版一样优先连到 `11200`，避免误起另一套 dev server。如需覆盖默认值，可设置环境变量 `CODEBOT_OPENCODE_PREFERRED_PORT` 与 `CODEBOT_OPENCODE_FALLBACK_PORT`。
聊天页模型刷新会优先调用 `opencode models`，与 OpenCode CLI 的最新模型列表保持一致；如果当前进程找不到 CLI，会回退到当前 `server_url` 指向的 OpenCode Server。若 Codebot 进程 PATH 与终端不同，可在 `config.json` 的 `opencode.cli_path` 填写 `opencode` 可执行文件或所在目录，也可以设置环境变量 `CODEBOT_OPENCODE_PATH`。Windows 桌面端还会额外自动探测 `%APPDATA%\npm`、Scoop shim、WinGet Links、Chocolatey bin 等常见 CLI 安装位置，避免“PowerShell 里能用、Codebot 刷新不到”的情况。Codebot 自己拉起 OpenCode Server 时，会将用户全局 `~/.config/opencode/opencode.json` 里的 `provider` 合并到 `data/opencode-config/opencode.json`，并通过 `OPENCODE_CONFIG_HOME=data/opencode-config` 启动 OpenCode；不要再同时覆写 `XDG_CONFIG_HOME`，否则会导致隔离配置中的新 provider（如 `volcengine`）不被当前 server 识别。刷新模型不会自动切换 OpenCode Server 地址，避免模型列表和实际聊天运行时不一致。若 CLI 已看到新 provider 但当前 `server_url` 尚未加载，模型会标记为“未加载”，这通常意味着当前 OpenCode Server 还是旧实例，需要重启该实例后再刷新。
启动后，Codebot 会自动把以下内容同步到 OpenCode：

- Codebot 自身的第三方 MCP 入口：`/api/mcp/codebot/sse`
- `skills/` 目录中的内置与生成技能

注意：外部 MCP 服务器不会再以独立条目直接写入 OpenCode 配置，而是由 Codebot 统一代理。也就是说：

- OpenCode 只需要连接 `codebot` 这一个第三方 MCP
- Codebot 内部再去访问你在“第三方 MCP”页面里配置的魔搭 MCP / 其他远程 MCP
- 对于魔搭 ModelScope MCP，Codebot 会沿用 Bearer 认证头透传请求，并把可用工具重新暴露给 OpenCode

##### 后台自动启动（Windows 任务计划程序）

打开任务计划程序：

- 搜索“任务计划程序”或按 Win + R → 输入 `taskschd.msc` → 回车

创建任务：

- 右侧点击“创建任务”

常规：

- 名称：OpenCode Serve
- 勾选“使用最高权限运行”
- “仅限当用户登录时运行”或“不管是否登录都运行”根据需要选择

触发器：

- 新建 → “在登录时”或“开机时”

操作：

- 新建 → 操作选择“启动程序”
- 程序/脚本填：`C:\Users\Administrator\Scripts\opencode.cmd`（根据你的实际路径；可打开 cmd 运行 `where opencode` 查找位置）
- 添加参数填：`serve --port 11200 --hostname 127.0.0.1`
- 起始位置填：`C:\Users\Administrator` 或你的工作目录

条件 / 设置：

- 可保持默认

保存任务后，每次开机或用户登录时，`opencode serve` 会自动启动。

#### 方式 1: 直接启动后端

```bash
# Windows
venv\Scripts\activate
python backend\main.py

# Linux/macOS
source venv/bin/activate
python backend/main.py
```

如果 OpenCode Server 未启动，应用会在启动阶段自动尝试拉起；若本机策略或环境限制导致拉起失败，再手动启动并检查 `config.json` 中的 `server_url`。

#### 方式 2: 使用 Electron 桌面应用

```bash
cd electron
npm install
npm start
```

开发模式下（从源码运行），Electron 默认使用 `venv\\Scripts\\python.exe`（若存在）启动 `backend\\main.py`，以确保后端代码变更立即生效；如需强制使用 `backend\\dist\\codebot-backend.exe`，可设置环境变量 `CODEBOT_BACKEND_MODE=exe`。
Electron 会优先使用应用内置的 `opencode` 可执行文件（`electron/vendor/opencode` 或打包后的 `resources/opencode`）自动拉起 `opencode serve`；若内置文件不可用或不可执行，会自动回退到系统 PATH 中的 `opencode`。桌面端会强制开启 OpenCode 自动拉起，并统一优先尝试 `127.0.0.1:11200`；如果系统里已经有 OpenCode 桌面端或 `opencode serve` 在运行，Codebot 会直接复用现有健康服务并跳过额外安装检查，避免启动阶段被 OpenCode 检测卡住。

### Windows 沙箱现状

- 沙箱已重构为**工作目录隔离**模式，移除 QEMU 依赖，开箱即用，无需安装任何额外软件
- AI 生成的代码默认在独立的 `data/sandbox_workspace/` 目录中执行；如设置了 `sandbox.workspace_dir`，则改用自定义目录，通过 `asyncio` 子进程运行，带超时控制
- 执行结果实时返回，支持 `stdout`/`stderr`/`exit_code` 完整输出
- 旧版 QEMU 相关端点（`/install-qemu`、`/start`、`/stop`）保留但返回本地模式说明，保持 API 向后兼容

### 访问

- **本地访问**: <http://127.0.0.1:15682>
- **局域网访问**: http\://<你的 IP>:15682
- **移动端**: 使用手机浏览器访问局域网地址

OpenCode Server 默认地址：<http://127.0.0.1:11200>（由 `opencode serve` 提供 HTTP API）
`server_url` 建议填写 OpenCode Server 的 HTTP 地址（例如 `http://127.0.0.1:11200`），后端会自动规范化为可访问的 HTTP 基础地址。连不上的话请优先检查 `config.json` 的 `server_url` 是否与 `opencode serve` 一致。
如果 OpenCode 未连接，不会在应用启动时弹窗打扰；当你在聊天中创建定时任务/保存记忆时，会优先使用本地规则解析并直接落库到“定时任务/记忆”，仅在解析失败且 OpenCode 可用时，才会通过 OpenCode HTTP API 请求获取结构化结果。意图识别阶段要求 OpenCode 输出结构化 JSON，若输出不符合格式会自动重试一次。后端会将 OpenCode Server 输出写入 `logs/opencode_server.*.log` 便于排查。
提醒类定时任务在 OpenCode 未连接时也会按计划执行并发送应用内通知；创建任务也不依赖 OpenCode（支持 `20:05` 与 `20：05`）。

### OpenAI 兼容模型网关

Codebot 提供 OpenAI 兼容接口：

- `GET /v1/models`
- `POST /v1/chat/completions`

端口取决于运行方式：正式安装版默认使用 `http://127.0.0.1:15682/v1`；在 `electron/` 目录执行 `npm start` 启动的开发版默认使用 `http://127.0.0.1:18080/v1`。开发版测试脚本不要继续写死正式版的 `15682` 端口。

使用前请先请求 `/v1/models`，从返回结果里的 `id` 选择当前真实可用的模型，不要直接写死一个本机未连接的模型名。

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:15682/v1",
    api_key="codebot",
)

models = client.models.list()
model_id = models.data[0].id  # 例如当前环境可用的 "github-copilot/gpt-4.1"

resp = client.chat.completions.create(
    model=model_id,
    messages=[
        {"role": "system", "content": "你是一个简洁的中文助手。"},
        {"role": "user", "content": "请用一句话说明 Python 的优点。"},
    ],
)

print(resp.choices[0].message.content)
```

也可以完全不安装 OpenAI SDK，直接使用 `requests` 调用：

```python
import requests

response = requests.post(
    "http://127.0.0.1:15682/v1/chat/completions",
    headers={"Authorization": "Bearer codebot"},
    json={
        "model": "codebot-default",
        "messages": [
            {
                "role": "user",
                # 同时兼容旧版字符串和新版 content-part 数组。
                "content": [{"type": "text", "text": "请介绍一下你自己。"}],
            }
        ],
        "stream": False,
    },
    timeout=300,
)
response.raise_for_status()
print(response.json()["choices"][0]["message"]["content"])
```

网关兼容 `messages[].content` 的字符串、`null` 和新版内容块数组格式，并接受 `developer`、`tool`、`tool_calls`、`max_completion_tokens`、`stream_options.include_usage` 等新版字段。Trae 等 IDE 将 `<system-reminder>` 与 `<user_input>` 包装在同一条消息中时，网关会自动分离内部上下文和真实问题，并在非流式、流式响应中阻止内部提示词泄漏。图片和音频内容块当前会转换成简短的文本引用后交给 OpenCode，文本块可正常透传。

如果你直接调用 HTTP 接口而不是 OpenAI SDK，`model` 字段可以省略或填写 `codebot-default`；此时后端会优先使用聊天页默认模型 `chat_default_model`，若未设置则依次回退到记忆自动整理模型 `memory.organize_model`、主模型 `primary_model`，再没有才交给 OpenCode 自行选择默认模型。

## 📁 项目结构

```
codebot/
├── backend/                    # Python 后端
│   ├── main.py                 # 入口：FastAPI 应用 + 生命周期管理
│   ├── config.py               # 全局配置模型 (Pydantic)
│   ├── requirements.txt        # Python 依赖
│   ├── core/                   # 核心业务逻辑
│   │   ├── opencode_ws.py      # OpenCode HTTP 客户端
│   │   ├── memory_manager.py   # SQLite + ChromaDB 记忆管理
│   │   ├── memory_extractor.py # 自动记忆提取（对话中识别习惯/偏好）
│   │   ├── memory_organizer.py # AI 驱动的每日记忆整理（合并/去重）
│   │   ├── scheduler.py        # Cron 定时任务调度器
│   │   ├── tool_dispatcher.py  # 技能发现与 MCP 协议适配（桥接辅助模块）
│   │   ├── lark_ws_bot.py      # 飞书 WebSocket 长连接机器人
│   │   └── sandbox/            # 沙箱隔离执行环境
│   │       ├── __init__.py     # 模块入口
│   │       └── manager.py      # 沙箱生命周期 & 工作目录隔离执行
│   ├── api/routes/             # REST API 路由
│   │   ├── chat.py             # OpenCode 会话 API（Codebot 只负责转发、展示与存储）
│   │   ├── memory.py           # 记忆 CRUD & 搜索 API
│   │   ├── scheduler.py        # 定时任务 API
│   │   ├── skills.py           # 第三方技能管理与同步 API
│   │   ├── mcp.py              # 外部 MCP 管理 + Codebot 自身 MCP SSE 入口
│   │   ├── config.py           # 应用配置 API
│   │   ├── notifications.py    # 通知 API
│   │   ├── lark.py             # 飞书 Webhook/事件 API
│   │   ├── sandbox.py          # 沙箱控制 API
│   │   └── logs.py             # 执行日志 API
│   ├── database/
│   │   └── init_db.py          # 数据库初始化 & 连接
│   └── services/
│       ├── notification.py     # 通知分发（应用内/桌面/飞书/邮箱）
│       └── lark_bot.py         # 飞书机器人（Webhook 模式）
├── frontend/                   # Web UI (Vue 3)
│   ├── src/
│   │   ├── views/              # 页面视图（Chat / Memory / Scheduler / Skills / MCP / Logs / Settings）
│   │   ├── components/         # 可复用组件
│   │   └── stores/             # Pinia 状态管理
│   └── dist/                   # 构建输出（由后端静态托管）
├── electron/                   # Electron 桌面应用
│   ├── main.js                 # 主进程：启动 Python 后端 & 打开窗口
│   ├── preload.js              # IPC 桥接（剪贴板 API）
│   └── package.json            # electron-builder 打包配置
├── skills/                     # 内置技能目录
│   ├── web_search/SKILL.md     # 网页搜索技能
│   ├── web_fetch/SKILL.md      # 网页抓取技能
│   ├── news/SKILL.md           # 新闻获取技能
│   ├── file_reader/SKILL.md    # 文件读取技能
│   ├── pdf/SKILL.md            # PDF 处理技能
│   ├── pptx/SKILL.md           # PowerPoint 技能
│   ├── docx/SKILL.md           # Word 文档技能
│   ├── xlsx/SKILL.md           # Excel 技能
│   ├── ai-company/SKILL.md     # AI 专家团队决策技能
│   ├── expert-agents/SKILL.md  # 14位专家人设技能
│   ├── code-review/SKILL.md    # 代码审查技能
│   ├── writing-plans/SKILL.md  # 写作计划技能
│   ├── subagent-driven-development/SKILL.md  # 子代理驱动开发技能
│   ├── arxiv-research/SKILL.md # 论文研究技能
│   ├── blogwatcher/SKILL.md    # 博客监控技能
│   ├── obsidian-notes/SKILL.md # Obsidian 笔记技能
│   ├── self-improving/SKILL.md # 自我改进技能
│   ├── systematic-debugging/SKILL.md  # 系统化调试技能
│   └── test-driven-development/SKILL.md  # 测试驱动开发技能
├── data/                       # 运行时数据目录（自动生成）
│   ├── config.json             # 用户配置（可通过 UI 编辑）
│   ├── conversations.db        # SQLite：对话/记忆/通知
│   ├── scheduled_tasks.db      # SQLite：定时任务 & 执行日志
│   ├── chroma/                 # ChromaDB 向量索引
│   ├── backups/                # 记忆备份 ZIP 文件
│   └── mcp_servers.json        # MCP 服务器配置
├── scripts/
│   ├── install.bat             # Windows 一键安装脚本
│   └── install.sh              # Linux/macOS 一键安装脚本
└── logs/                       # 服务器 & OpenCode 运行日志
```

## 📖 功能说明

### 1. OpenCode 会话

- 创建和管理多个对话，支持重命名、置顶、归档、删除
- 所有消息统一交给 OpenCode 处理，Codebot 不再充当主代理，只负责提供第三方能力和结果展示
- 查看历史对话记录；进入"聊天"自动打开最近对话
- 支持多对话并行处理，多个对话可同时发送并后台执行
- **多Agent群聊**: 左侧置顶“多Agent群聊”工作台，普通对话可加入为前端/后端/数据库/测试等成员 Agent，总任务会按串行/并行步骤分派到成员对话并汇总过程与结果
- **对话分享**: 生成局域网分享链接（`share_id`），可供同一局域网内他人通过浏览器只读查看
- 支持在聊天中创建定时任务（如"每天8点写一个故事并保存到D盘""每天8:10提醒我喝水"，可在定时任务中查看）
- 支持在聊天中保存记忆（如"帮我记住 广东揭阳普宁船埔 这个地址""10月2日是姐姐的生日"，可在记忆中查看）
- **意图分类**: 消息自动分类为"定时任务/保存记忆/普通对话"，避免误判
- **Agent 模式**: 支持 `plan`（结构化规划）和 `build`（直接执行）两种模式
- **对话级状态**: 每个普通对话会独立保存当前模式、模型、Hermes/Obsidian 目标和已选知识库；在 B 对话切换模型或处理目标，不会覆盖 A 对话原来的选择。新建对话仍会沿用最近主动选择的全局默认模型，创建后再形成自己的独立状态
- 消息一键复制（Electron 使用系统剪贴板）
- 支持文件附件上传；多模态模型支持图片分析
- 支持截图后直接在聊天输入框粘贴图片，图片会自动作为附件加入当前消息
- 流式响应显示
- 流式展示 OpenCode 步骤事件（如 `step-start` / `step-finish`）与回复增量
- 流式链路采用 `prompt_async + /global/event`，可实时看到工具调用与文本增量
- 前端按事件实时追加渲染（步骤/工具事件逐条显示，正文增量分块刷新，不等待最终完成）
- 为避免浏览器渲染被单次大量事件阻塞，前端会在流式消费中定期让出事件循环，并将文本增量按帧合并刷新
- 同一对话默认复用 OpenCode 会话，减少上下文丢失，提升连续追问一致性
- 切换到“技能/设置”等页面时，任务继续在后台执行，除非用户主动点击终止
- 从“技能/设置”等页面返回聊天页后，会自动恢复当前对话的“处理中/排队中”状态，并在任务结束后自动刷新最新消息
- 从其他页面返回聊天页后，会按当前对话回放后台执行期间的工具调用事件，并保持原有事件顺序与展示位置
- 自动沉淀技能仅在复杂任务场景触发，过滤寒暄类和系统提示词污染内容，避免生成无意义技能
- 回答入库与展示前会自动清洗 `system_policy`/`conversation_context` 等内部提示片段，避免污染最终回复与对话标题
- 聊天时间显示会将 SQLite 的 UTC 时间戳按本地时区换算，并正确显示“刚刚/分钟前/小时前”
- 聊天相对时间会自动刷新，避免时间长期停留在“刚刚”
- 用户手动上滑查看历史消息时，流式更新不再强制自动滚动到底部；仅在接近底部或主动发送消息时自动跟随
- 后台事件回放与数据库最终消息会做去重收敛，避免同一回复先流式出现后又重复插入一次
- 消息操作按钮（复制/撤销）统一固定在回复气泡右下角
- 应用与对话回复头像使用 logo.ico

#### 多Agent群聊功能怎么使用

聊天页左侧会固定显示“多Agent群聊”，它是系统级协作工作台，不能删除，只能清空消息内容。普通对话可以加入这个群聊，作为不同职责的 Agent 成员。

使用步骤：

1. 先创建或选择多个普通对话，并给它们选择相同项目目录。
2. 在普通对话左侧更多菜单中点击“加入多Agent群聊”。
3. 输入该对话的角色，例如“前端”“后端”“数据库”“测试”。
4. 打开置顶的“多Agent群聊”，输入总任务。
5. 系统会按角色、标题和任务关键词生成任务计划，并识别“然后、再、完成后、交给”等串行依赖。
6. 每个成员对话会看到自己被分配的任务和对应回复。
7. “多Agent群聊”会在执行期间显示任务分配流程、当前步骤、成员返回状态，并最终汇总成员回复、风险和协作状态。
8. 执行过程中点击“终止”会终止 hub 调度，并向当前参与的成员对话同步发送终止信号。

示例：

- A 对话加入为“前端”，负责 Vue 页面、交互和样式。
- B 对话加入为“后端”，负责 FastAPI 接口和业务逻辑。
- C 对话加入为“数据库”，负责 SQLite 表结构和迁移。
- D 对话加入为“测试”，负责构建、语法检查和回归验证。

当用户在“多Agent群聊”中说“实现用户管理功能，包括页面、接口、数据库和测试”时，系统会分别给 A/B/C/D 分派对应子任务，并在 hub 对话中汇总完成情况。

当用户说“让写诗 Agent 写一首诗，写完之后让鉴赏 Agent 点评”时，系统会先把写诗任务交给写作成员，再把写作结果作为上游产物交给鉴赏成员。

外部技能检索结果：已通过 `find-skills` 找到 `task-based-multiagent`，安全扫描结果为 Clean（无脚本、无高危权限）。项目内置 `skills/multi-agent-collaboration/SKILL.md` 基于其任务领取/状态回写思想改写，适配 Codebot 对话式多 Agent 协作。

#### 分享与归档

- 点击左侧对话更多菜单里的“分享”后，系统会生成 `/share/{share_id}` 只读页面，并复制基于局域网地址的链接，例如 `http://192.168.1.10:15682/share/xxxx`。
- 分享页面只展示对话内容，不提供继续发送消息、删除或修改能力。
- 点击“归档”后，对话仍保存在 `data/conversations.db` 中，`conversations.is_archived` 会被设置为 `1`，对应消息仍保存在 `messages` 表。
- 归档后的对话不会显示在聊天页左侧普通对话列表中，可在“日志”页的“已归档对话”标签中查看、搜索和恢复。

### 1.1 Codebot 作为第三方的工作方式

- OpenCode 是主聊天入口，负责模型选择、推理、工具决策与最终回答
- Codebot 通过 `/api/mcp/codebot/sse` 暴露第三方 MCP，向 OpenCode 提供记忆、任务、技能、会话等工具
- Codebot 会把“第三方 MCP”页面中启用的远程 MCP 工具代理成 `codebot_mcp__...` 形式的工具名，再暴露给 OpenCode
- Codebot 会把 `skills/` 目录中的技能同步到 OpenCode 技能目录，使其作为第三方技能被直接调用
- 聊天请求发往 OpenCode 时只附带必要的用户记忆上下文，不再由 Codebot 预判技能或代替 OpenCode 做二次工具编排
- `backend/core/tool_dispatcher.py` 已收敛为桥接辅助模块，仅负责技能发现与 MCP 协议适配，不再承担聊天主链路上的工具调度
- 前端聊天页只展示 OpenCode 的流式步骤与最终结果，并提示当前第三方桥接状态

### 1.2 Hermes / Obsidian / 文档入口

- **Hermes 模式**: 在聊天页点击 `Hermes` 后，当前消息默认会交给 Codebot 管理目录中的 Hermes Agent CLI 处理。设置页中的 `Hermes` 标签位于“通用设置”右侧，提供“一键安装 / 一键修复 / 一键更新”，默认把 Hermes 安装到 Codebot 根目录下的 `hermes-agent/`。Codebot 只作为薄适配层：写入共享配置（模型网关、记忆库、定时任务库、技能目录和 Obsidian 路径），启动 Hermes CLI 子进程，把终端输出持续追加到当前聊天气泡；当 CLI 明确要求确认、继续、密码、密钥或输入时，会在聊天页显示交互面板，并把用户回答写回 Hermes stdin。若 CLI 长时间暂时没有正文输出，Codebot 会在聊天窗口中持续映射 Hermes 运行状态：包括 `session.status` 启动状态、`session.trace` 运行轨迹，以及非阻塞的 `session.idle` 后台心跳，明确告诉用户当前仍在处理、已静默多久、以及最后一条可见输出，避免出现“后台到底卡住还是正在处理”的黑箱感。为了更接近 OpenCode CLI 的可读性，Codebot 现在还会把明显像“加载 skill / 调用 tool / 扫描资源 / 运行步骤”的 Hermes 行优先归类成可见的“工具调用”事件，其余普通说明继续归类为“运行轨迹”，让中间过程和最终正文更清楚地区分。针对开发态排查中发现的“启动后长时间 0 输出但同消息重试可成功”的间歇性卡死，Codebot 现在会在 Hermes 启动后连续 45 秒仍无任何 stdout 时自动重试 1 次，仅针对尚未产生任何输出的启动阶段，不影响已经开始正常输出的长任务。重构后的 Hermes 接入不再导入 Hermes 内部 Agent 类，也不再依赖自定义 runner；聊天执行入口也已从顶层 `hermes -z/--oneshot` 切换为 Hermes 官方 `--cli chat -q` 单次查询路径，避免继续走 `oneshot` 那条“只输出最终结果并绕过 `cli.py`”的旁路。Hermes 的 skill 共享模型现在统一收敛为“目录共享”而不是“执行委派”：Codebot 会把 **Hermes 运行时 `HERMES_HOME/skills`**、**Hermes 仓库内 `skills/` 与 `optional-skills/`**、**用户手动配置的 Hermes Skill 目录**、**Codebot 内置/自动生成 skills 目录**、以及 **OpenCode CLI skill roots** 一起并入 Hermes 的有效 `skills.external_dirs` / 共享上下文。也就是说，仓库 bundled/optional skill 现在默认就会被导入；如果用户不需要其中某个目录，可在设置页把它加入自动共享排除列表。设置页中的“Hermes Skill 目录”除了手动目录外，还会展示自动共享目录，并支持把其中任意目录加入排除列表；技能页对同源同 slug 的 Hermes 只读 skill 也会自动折叠去重，避免开发版/正式版或多套 Hermes 安装同时暴露同名 skill 时出现多条重复记录。对于“显式点名且来源于 OpenCode 共享目录”的 skill，Codebot 现在会先检测 Hermes 兼容分流：运行时证据表明某些 OpenCode-shared skill 会在 Hermes 子进程中长期静默卡住，因此这类请求会透明切换到 OpenCode 原生执行链，并在聊天中显示 `session.compat` 说明，避免暗箱等待。模型方面，Hermes 不要求用户单独配置模型：聊天主模型跟随当前聊天模型，后台辅助模型跟随“记忆 → 配置 → 自动整理 → 整理使用模型”，因此 Codebot 能调用的 OpenCode 模型，Hermes 也会共用同一套 `/v1` 网关。为避免开发版 Windows 环境下的中文乱码和终端样式污染，Codebot 现在会对 Hermes stdout 使用增量 UTF-8 解码，并过滤 ANSI 控制序列、Rich 边框、`Resume this session` / `session_id` / `Session` 等终端辅助行，只把更接近正文的内容映射到聊天消息。Hermes 执行超时按“空闲无输出/无交互”计算；终止按钮会结束当前 Hermes 进程。Hermes 模式下沉淀或创建的定时任务会记录 `executor=hermes`，后续到点触发时继续由 Hermes CLI 执行；OpenCode 模式创建的任务则记录 `executor=opencode`
- **Obsidian 模式**: 设置页中的 `Obsidian` 标签支持配置默认 Vault 路径与多个知识库路径；聊天页通过 `#` 可多选知识库。Codebot 会把这些 Markdown 知识库当成原生 Obsidian wiki 结构直接处理，优先引导调用 `obsidian-cli` 与相关 Obsidian skill 去完成检索、模板调用、读取、写入、移动与 wiki-link 安全操作，不会把知识库先转成向量库。桌面正式版在 `Obsidian` 目标下只会选择 Codebot/Hermes 本地可用的 Obsidian skill，不再回退到 Vault 内部的 OpenCode skill 路径；发送给 OpenCode 的 session workspace 会固定到 Codebot 可写数据目录，而不是 Vault 目录。整库扫描 Markdown 时会跳过 `.opencode`、`.obsidian` 等工具目录并忽略坏目录，因此即使 `<vault>/.opencode/skills/agents` 不存在，也不应再因此导致发送失败
- **Hermes + Obsidian 双选**: 聊天页同时点亮 `Hermes` 和 `Obsidian` 时，前端会发送组合目标 `hermes_obsidian`，后端会同时走 Hermes CLI 执行链和 Obsidian Markdown 上下文构建链。此模式会固定加载 Hermes 原生 Obsidian skill `note-taking/obsidian`，并把 Hermes `skills.external_dirs` 收窄到能解析该 skill 的根目录，避免正式版因多个 Obsidian skill 同名或目录重复而静默卡住
- **Hermes 错误处理**: Hermes CLI 异常退出、返回空内容、空闲超时，或连续 180 秒没有任何可显示输出时，聊天流会立即返回明确错误，包含退出码和最后输出片段；不会再只持续显示 `session.idle` 心跳让用户猜后台是否已经报错
- **首响优化**: 聊天发送后，Codebot 只会在消息明显像“创建定时任务/提醒/闹钟”时才调用额外的意图分类；普通 Hermes / OpenCode / Obsidian 对话现在直接进入对应执行链，减少发送后前十几秒无响应的情况
- **技能搜索**: 聊天输入框中的 `@` 会搜索全部技能来源，包括 Codebot 内置/自动生成、OpenCode、Hermes Agent 与 OpenClaw，支持描述、单词和多词搜索
- **Hermes 来源细分**: 技能页中 `Hermes Agent` 来源现在会继续细分显示 `运行时`、`官方仓库`、`手动目录` 标签，并支持按这三类快速过滤，方便判断该 skill 是来自当前 `HERMES_HOME/skills`、Hermes 仓库自带目录，还是用户在设置页手动追加的目录
- **技能调用修复**: 聊天页从 `@` 面板插入 skill 时，前端会写入 `使用技能 @[skill.id] 技能名` 标记；后端现在已兼容解析这类格式，并会在 Hermes 目标下正确恢复 `selected_skill -> skill slug -> --skills` 调用链，避免“已选 Hermes 但实际上没有把 skill 传给 CLI”导致长时间静默
- **Hermes 共享修复**: 当用户在聊天里显式选中 Hermes skill 时，Codebot 现在会把 Hermes `skills.external_dirs` 从“全量共享根目录”收窄为“能解析所选 skill 的根目录集合”；未显式选中 skill 时，仍保持默认全量共享。这样可以避免 Hermes 在调用单个 skill 时继续扫描整包共享 roots，减少“CLI 已启动但长时间静默”的情况
- **命令搜索**: 聊天输入框中的 `/` 会搜索 OpenCode CLI 命令，并支持按描述、单词和多词进行匹配
- **文档入口**: 设置页里的“文档”会直接渲染本 README，文档右上角可刷新，适合在改动配置、功能或使用方式后重新查看
- **使用顺序**: 建议先看“快速开始”和“访问”，再看这里的使用说明；真正上手时，优先用聊天页底部的 `项目`、`生成技能`、`Hermes`、`Obsidian` 按钮切换处理目标

### 2. 记忆系统

- **上下文记忆**: 自动保存对话历史
- **长期记忆**: 保存用户习惯、偏好、事实信息（用于之后对话检索问答）
- **自动提取**: 每次对话后，后台自动进行规则+AI双通道提取，识别重要信息并保存，无需依赖“记住”关键词
- **记忆类别**: `habit`（习惯）、`preference`（偏好）、`profile`（个人信息）、`note`（笔记）、`contact`（联系人）、`address`（地址）
- **聊天记忆分类补充**: 聊天中手动“记住”时会优先识别偏好（喜欢/偏好/风格/工具等）和习惯（通常/经常/常用等），再识别联系方式和地址，最后才判断个人信息，避免使用偏好被误归为个人信息或联系人
- **AI 记忆提取增强**: AI 自动提取记忆时，系统提示词包含严格的分类边界定义和示例，确保偏好/习惯不被错误归类
- **记忆提示**: 聊天输入时自动检索相关记忆并在输入框上方显示提示气泡，AI 回复时也会注明"根据我的记忆"
- **记忆搜索**: 语义搜索相关记忆
- **记忆归档**: 自动或手动归档旧记忆，支持按类别过滤查看
- **事实同步**: 打开活跃记忆列表时，会将生日类事实记忆自动补齐到长期记忆，避免“有事实但列表为空”
- **删除联动**: 删除带 `memory_key`/`fact_key` 的长期记忆时会同步归档对应事实，防止生日等条目被自动补回
- **存储诊断**: 提供 `/api/memory/storage-status`，可直接查看当前数据库路径与表计数
- **一键自检**: 活跃记忆页支持“一键自检”，会检测数据库路径、关键表计数和接口读链路状态
- **备份恢复**: 导出记忆为 ZIP 文件（保存至 `data/backups/`），或上传备份文件恢复（导入前自动备份当前数据）
- **记忆整理**: 每日在配置时间点（默认 03:00）自动用 AI 对活跃记忆进行优化，也可在"活跃记忆"页手动触发
- **聊天整理联动**: 自动整理时会扫描新增聊天记录，从聊天中补充记忆，并尝试沉淀相关定时任务与可复用技能；扫描窗口使用 SQLite 可比较时间格式并带消息 ID 兜底，避免因时间字符串格式差异漏扫
- **自动清理**: 可选同时清理超期的已归档长期记忆，活跃记忆永不被自动删除
- **页面入口**: /memory/active、/memory/search、/memory/archived、/memory/backup、/memory/config

### 3. 定时任务

- **Cron 表达式**: 完整的 Cron 语法支持
- **智能时间解析**: 自动从用户消息中区分"时间部分"和"任务内容"。例如"5分钟后，写首春天的诗保存到D盘"会解析为：调度时间=5分钟后，任务=写首春天的诗保存到D盘
- **意图识别**: 本地规则优先分流"定时任务/记忆/普通对话"，避免"保存到D盘"这类任务被误判为记忆
- **判断依据**: 同时满足"触发词（提醒/通知/闹钟等）+ 时间或日期线索（每天/周几/几点/10月20日等）"时优先判为定时任务
- **生日提醒特例**: 对"记住我的生日，10月20日，我生日时提醒我"这类复合句，会同时保存生日记忆并创建每年生日提醒任务
- **AI 辅助**: 自然语言生成 Cron 表达式（OpenCode 不可用时降级为本地规则解析）
- **通知渠道**: 飞书/邮箱/应用内通知
- **候选通知**: 定时任务页顶部的“开启通知”控制任务候选提醒；开启后，聊天或自动整理把定时任务加入“成长候选”时会发送应用内/桌面操作提醒，便于及时打开“成长候选”确认、编辑或接受
- **执行日志**: 详细的任务执行记录
- **执行器归属**: 定时任务会持久化 `executor` 字段。聊天中选择 Hermes 后沉淀的任务、成长候选接受后的任务和手动编辑为 Hermes 的任务，到点执行时会调用 Hermes CLI；选择 OpenCode 或未指定时走 OpenCode
- **执行模型**: 聊天中创建定时任务时，会把当时选择的主模型保存为任务的 `execution_model`；任务执行前会检查该模型是否仍在当前可用模型列表中，如果模型过时或供应商不再提供，会自动回退到“记忆 → 自动整理 → 整理使用模型”。在“定时任务”编辑窗口中可以为任务重新选择可用模型
- **调度边界**: 聊天中的定时任务创建意图由 AI 结构化分类器判断；只有判断为“创建/添加/设置 Codebot 定时任务、提醒或闹钟”时，Codebot 才会写入内置定时任务系统或成长候选。普通排错、日志分析和文件处理会继续交给 OpenCode/Hermes CLI，不会被误创建为任务；Codebot 也不会让 CLI 立即创建 PowerShell 后台作业、Windows `schtasks`、cron/systemd/launchd 等系统级定时器
- **提醒任务**: 带 `__REMINDER__` 标志的纯提醒任务不依赖 Hermes/OpenCode 也能按计划触发通知；AI 类任务（生成内容/写文件等）按任务执行器要求对应运行时可用
- **像聊天一样执行**: 定时任务到达执行时间时，系统会按任务执行器像对应聊天入口一样处理任务内容，充分利用 AI 的代码生成与文件写入能力
- **一次性任务**: 未强调重复性的任务（如"5分钟后"、"明天"）自动标记为一次性，执行完成后不再重复触发

示例：

- 每天早上 9 点生成日报：`0 9 * * *`
- 每周一上午 10 点开周会：`0 10 * * 1`
- 每小时检查邮箱：`0 * * * *`
- 5分钟后写首春天的诗并以 md 格式保存到 D:\wenjian\临时文件夹（一次性，5分钟后执行写诗任务）

### 4. 模型管理

- **主模型**: 处理常规文本任务
- **多模态模型**: 处理图片识别任务
- **自由切换**: UI 界面随时切换模型
- **刷新模型**: 聊天页刷新按钮会优先读取 OpenCode CLI 的 `opencode models` 输出；CLI 不可用时回退到当前 `server_url` 的 `/provider` 列表。若桌面端找不到 CLI，可配置 `opencode.cli_path` 或 `CODEBOT_OPENCODE_PATH`；Windows 下还会自动扫描 `%APPDATA%\npm`、Scoop、WinGet、Chocolatey 等常见安装目录。Codebot 自己启动 OpenCode Server 时会同步全局 `provider` 配置到隔离的 `data/opencode-config/opencode.json`，并仅通过 `OPENCODE_CONFIG_HOME` 指向该目录。刷新不会自动切换 OpenCode Server 地址；如果 CLI 列表已有新 provider 但当前 server 尚未加载，UI 会标记为“未加载”，发送前也会提示需要重启当前 OpenCode Server。

### 5. 技能系统

- **内置技能**: `web_search`（网页搜索）、`web_fetch`（抓取网页）、`news`（新闻获取）、`file_reader`（文件读取）、`pdf`（PDF 处理）、`docx`（Word 文档）、`pptx`（PowerPoint，含缩略图脚本）、`xlsx`（Excel，含重算脚本）、`ai-company`（AI 专家团队决策）、`expert-agents`（14位专家人设）、`code-review`（代码审查）、`writing-plans`（写作计划）、`subagent-driven-development`（子代理驱动开发）、`arxiv-research`（论文研究）、`blogwatcher`（博客监控）、`obsidian-notes`（Obsidian 笔记）、`self-improving`（自我改进）、`systematic-debugging`（系统化调试）、`test-driven-development`（测试驱动开发）
- **技能定义**: Markdown 文件（`SKILL.md`）带 YAML front-matter（`name`、`description`），自动匹配用户提示
- **自动调度**: `tool_dispatcher.py` 通过关键词 + 语义匹配，将 `SKILL.md` 内容注入到对应请求的提示词中
- **低干扰注入**: 仅在高相关度下启用技能上下文，降低无关技能误触发
- **内部上下文隔离**: 技能与 MCP 上下文仅用于内部推理，不向用户直接展示“技能参考”等标签
- **自动沉淀技能**: 对高复用的已完成任务，自动生成可复用技能元数据，便于后续任务快速命中
- **对话生成技能**: 聊天页点击“生成技能”后，后端会先调用 `find-skills` 搜索并评估现有 skill；当最佳结果与需求差异小于 40%（即相似度至少 60%）时，Codebot 会基于该 skill 改造并保存；否则会继续调用 `skill-creator` 创建新 skill。最终产物统一迁移或写入 `skills/auto_*`，在技能页中归类为“自动生成”
- **OpenCode 本地技能**: 自动读取 `~/.agents/skills`，可在技能页卸载
- **自定义目录技能**: 支持配置多个外部文件夹路径，自动扫描其中包含 `SKILL.md` 的子目录并加载为只读技能，方便复用其他工具的技能文件
- **PDF 解析依赖**: 聊天附件中的 PDF 文本提取优先使用 `pdfplumber`，不可用时回退到 `pypdf`；当前代码与 `backend/requirements.txt`、PyInstaller 打包配置已统一使用 `pypdf`，不再依赖旧的 `PyPDF2`
- **技能市场**: 从 GitHub 安装技能（开发中）

### 6. MCP 服务器管理

- 支持 **stdio** 和 **SSE** 两种传输模式的 MCP 服务器配置
- SSE 模式的 MCP 工具由 `tool_dispatcher.py` 自动调用：当用户提示词匹配到工具描述时，后端自动发起工具调用并将结果注入上下文
- 完整 CRUD 管理界面（`/mcp` 页面）及 REST API（`/api/mcp`）

### 7. 沙箱执行

- 工作目录隔离执行环境，AI 生成的代码在独立 `data/sandbox/workspace/` 目录中运行
- **无需安装额外软件**：移除 QEMU/Docker 依赖，开箱即用
- 基于 `asyncio.create_subprocess_shell` 执行命令，支持超时控制
- 完整输出捕获：`stdout`、`stderr`、`exit_code`
- 执行模式：`local`（工作目录隔离）
- 可配置执行超时（秒，默认 300）

## 🧩 常见问题

### OpenCode 连接失败（HTTP 200）

如果你在自行测试 WebSocket 时看到 `server rejected WebSocket connection: HTTP 200`，说明 OpenCode Server 在该端口提供的是 HTTP 服务而非 WebSocket。
当前版本 Codebot 使用 OpenCode 的 HTTP API（如 `/global/health`、`/session`）进行交互，即使你把 `server_url` 写成 WebSocket 形式，后端也会自动转换为对应的 HTTP 基础地址。

### 启动时报 WinError 216（OpenCode 可执行文件不兼容）

这通常表示内置的 `opencode.exe` 与当前系统架构不兼容。当前版本会自动尝试下一个候选命令（系统 PATH 中的 `opencode/opencode-ai`），无需手动修改代码。

- 建议先确认系统中可直接执行：`opencode --version`
- 若命令不存在，可重新安装 OpenCode CLI（例如 `npm i -g opencode-ai`）

### 刷新页面后白屏或 404

如果直接访问或刷新 `/memory/active`、`/skills` 等前端路由地址，请确保通过后端服务地址访问（如 `http://127.0.0.1:15682`）。
当前版本后端已支持 SPA History 路由回退，刷新不会再丢失页面。

### 启动时报 sqlite3.OperationalError: Cannot add a column with non-constant default

这是 SQLite 的限制：`ALTER TABLE ... ADD COLUMN` 不能使用 `CURRENT_TIMESTAMP` 这类“非固定常量”的默认值。
当前版本已在迁移逻辑中兼容旧库：新增 `updated_at` 列时不设置默认值，并对历史数据进行回填；升级后再次启动即可恢复正常。

### 启动时报 PermissionError / WinError 32: chroma.sqlite3 被占用

这通常是因为上一次的后端/Electron 进程未完全退出，导致 `data/chroma/chroma.sqlite3` 被占用。

- 先确保所有 Codebot 相关进程已退出（关闭终端、退出 Electron）
- 重新启动后端
- 如仍失败，可手动将 `data/chroma` 重命名为 `data/chroma_backup_manual` 后再启动（向量索引会自动重建）

### 启动时报 WinError 10048: 端口 15682 被占用

这表示你在同一台机器上启动了多个 Codebot 后端实例（或其它程序占用了配置端口）。

- 用 PowerShell 查找占用进程：`netstat -ano | findstr :15682`
- 结束占用进程：`taskkill /PID <PID> /F`
- 或修改 `data/config.json` 里的 `network.port`，换一个端口后再启动

## ⚙️ 配置说明

### 记忆配置

在“记忆”页面的“配置”标签（`/memory/config`）可以配置：

- **自动清理**: 启用后自动删除旧数据
- **保留天数**: 数据保留时间（0=永久）
- **清理已归档记忆**: 开启后，自动清理时会同时删除超期的已归档长期记忆（活跃记忆永不受影响）
- **自动归档**: 启用后自动归档旧对话
- **归档天数**: 多少天后的对话被归档
- **每日自动整理**: 启用后，每天在指定时间自动触发 AI 整理
- **整理聊天记录**: 启用后，自动整理会额外扫描新增聊天记录并联动补记忆/任务/技能
- **整理时间**: 选择每日整理时间（默认 03:00）
- **上次整理时间**: 显示最近一次整理完成时间，支持"立即整理"手动触发
- **整理使用模型**: 选择执行 AI 整理时使用的模型（默认使用当前聊天模型；可指定其他模型以规避主模型的 token 消耗）

### 记忆整理

记忆整理通过 AI 对长期记忆进行批量优化：

- **合并重复**：相似或互相包含的条目合并为一条
- **补全描述**：过于简短的内容根据语义适当补全
- **标准化格式**：统一表达方式，去除冗词
- **修正矛盾**：同类别中互相矛盾的条目，保留较新的，旧的归档
- **聊天补全**：额外扫描新增聊天消息，补提取高价值信息到长期记忆
- **任务/技能联动**：当聊天内容具备任务或技能线索时，自动触发任务创建与技能沉淀流程
- **运行反馈**：整理完成后会更新“上次整理时间”，后端日志会记录扫描消息数、新增记忆数、创建任务数和沉淀技能数；如果没有新增高价值内容，对应计数可能为 0。

无 OpenCode 连接时降级为规则模式（仅去重完全相同的条目）。每批最多处理 30 条，避免超出模型上下文窗口。活跃记忆永远不会被整理删除，只会被归档或更新内容。可在记忆配置中指定专用整理模型，与日常聊天模型解耦，便于灵活控制成本。

### 沙箱配置

在设置页面沙箱标签页可以配置：

- **启用沙箱**: 开启后 AI 生成的代码在工作目录隔离环境中执行
- **执行超时**: 单次命令执行超时（秒，默认 300）
- 无需安装 QEMU 或 Docker，所有执行均在本机  目录中进行
- 切换启用沙箱后，点击冒烟测试立即验证执行环境是否正常

### MCP 服务器配置

在 MCP 页面（`/mcp`）管理外部工具服务器：

- **stdio 模式**: 通过标准输入/输出与本地进程通信
- **SSE 模式**: 通过 HTTP Server-Sent Events 与远程服务通信
- 添加后，AI 会在处理相关请求时自动调用匹配的 MCP 工具

在设置页面"技能目录"标签页可以管理自定义技能文件夹：

- **添加目录**: 输入包含技能子文件夹的目录路径，每个子文件夹须包含 `SKILL.md` 文件
- **多目录支持**: 可添加多个不同路径，所有目录中的技能会统一显示在技能列表中
- **只读技能**: 自定义目录中的技能为只读，不可在 UI 中删除或修改，需在文件系统中直接管理
- **来源标识**: 技能列表中以"外部目录"标签区分自定义目录技能

### 通知配置

- **应用内通知**: 默认启用，在右上角查看，配置修改后即时生效
- **系统桌面通知**: 推送至操作系统通知中心（Windows/macOS/Linux），需在设置中启用
- **飞书通知**: 配置 Webhook URL
- **邮箱通知**: 配置 SMTP 服务器，任务通知会按“全局开关+任务渠道”共同判定
- **邮箱测试**: 设置页“邮箱配置”支持一键发送测试邮件，快速验证 SMTP 配置是否生效
- **地址兼容**: 自动将国际化域名转为 Punycode；若邮箱本地部分含中文且 SMTP 不支持 SMTPUTF8，会返回明确错误提示

### 飞书对话机器人

- 无公网地址推荐使用“长连接”订阅方式，只要机器能访问公网即可，无需公网 IP/域名/内网穿透：<https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration-case>
- 在飞书开放平台创建企业自建应用，启用机器人能力，并在“事件与回调”里选择“使用长连接接收事件”
- 设置页面启用飞书机器人，接入方式选择“长连接”，填写 App ID / App Secret
- 如果日志出现 `lark_oapi` 缺少 `EventDispatcherHandler` / `ws` 等提示，说明当前依赖版本不支持长连接，请改用 Webhook 模式
- Webhook 回调模式需要公网可访问的回调地址 `/api/lark/events`，且当前版本不支持加密回调

### 日志配置

- 任务日志保留天数：默认 30 天（0=永久）
- 聊天日志保留天数：默认 30 天（0=永久）
- 系统日志保留天数：默认 7 天
- 日志级别：支持 `DEBUG` / `INFO` / `WARNING` / `ERROR`
- 可手动清理旧日志

### 开源前隐私检查

- `APP_TOKEN` 不再内置默认值，需在 `.env` 中显式配置随机高强度令牌
- `.env.example` 仅保留占位符，避免误用示例值作为生产密钥
- `backend/dist_build/`、`backend/dist/`、`backend/build_tmp2/` 与 `security-scan-*.json` 已加入 `.gitignore`，避免将打包产物和扫描报告误提交
- `data/*.json` 已加入 `.gitignore`，防止含 API token 的 `mcp_servers.json` 等运行时数据文件被误提交
- `data/mcp_servers.json` 已从 git 追踪中移除（`git rm --cached`），文件内容仅保留在本地
- 本地开源清理建议先删除 `backend/dist_build/`、`security-scan-*.json`、`security-report*.json` 再提交
- 推荐执行：`python C:\Users\yuhan\.agents\skills\security\scripts\scan_secrets.py . --severity medium --extensions .py,.js,.vue,.json,.env,.toml,.yaml,.yml,.md`
- 飞书 Webhook 日志默认不再记录消息正文，仅记录 `chat_id` 和文本长度

## 🔧 开发指南

### 后端开发

```bash
# 激活虚拟环境
source venv/bin/activate  # Linux/macOS
venv\Scripts\activate     # Windows

# 启动开发服务器
python backend/main.py
```

### 前端开发

```bash
cd frontend

# 开发模式
npm run dev

# 构建生产版本
npm run build
```

### Electron 开发

```bash
cd electron

# 启动应用
npm start

# 打包应用
npm run build
```

说明：`npm start` 会先立即打开 Electron 窗口并显示加载页，再启动后端与 OpenCode 检查。

补充说明：

- Electron 启动时会先检查 `http://127.0.0.1:15682/api/health`，若后端已在运行则复用现有实例，不会重复拉起后端进程。
- 若检测到已有后端运行但 `opencode_connected=false`，Electron 会自动重启后端以重新拉起 `opencode serve`。
- 开发模式下 `npm start` 始终使用 `venv\Scripts\python.exe backend\main.py` 启动后端，确保运行的是当前源码。
- 开发模式若本机已启动 `frontend` 的 Vite dev server（默认 `http://127.0.0.1:3000`），Electron 会优先连接该前端开发服务；未启动时才会自动构建 `frontend/dist` 后再继续加载应用。
- 开发模式默认使用 `18080`，若检测到该端口为非源码后端（`runtime_source != source`），会先终止该进程再拉起源码后端，避免看不到最新流式改动。
- 文档页 `/docs` 通过 `/api/docs/readme` 读取项目根目录 README；桌面端会显式传递 `CODEBOT_DOCS_SOURCE`，避免开发态或打包态下文档 404。
- 桌面端打包默认读取 `backend/dist_build/codebot-backend` 作为后端资源目录。
- Windows 下建议使用根目录 `build.bat` 执行完整打包流程（后端 PyInstaller + 前端构建 + Electron 安装包）。
- Windows 打包产物默认输出到 `electron/dist/electron_new/`。
- Windows 产物会同时生成安装版（`Codebot-Setup-1.0.0.exe`）和免安装版（`Codebot-1.0.0.exe`，portable）。
- GitHub Releases 公共更新会在桌面端先解析 Release API 中的真实安装包资产名，再执行下载；即使资产名里的空格、点号或连字符存在差异，也不会再因为 404 导致下载失败。
- 项目已在 `.trae/rules/release-update-compat.md` 固化 GitHub Release 更新兼容规则，后续智能体处理发版、补传资产或自动更新问题时应优先遵循该规则。
- `build.bat` 会自动清理 `electron/dist/electron_new/win-unpacked`，避免旧桌面资源残留导致打包混淆。
- `build.bat` 使用 `python -m pip` 安装依赖并关闭 pip 版本检查，兼容 Conda/venv 场景。
- `build.bat` 使用 `python -m PyInstaller` 执行后端封装，避免 `pyinstaller.exe` 路径缺失导致构建失败。
- `build.bat` 在前端/桌面依赖安装失败时会直接给出错误并退出，便于快速定位打包问题。
- 若 `electron-builder` 提示 `win-unpacked\\resources\\app.asar` 被占用，请先关闭已运行的 `Codebot.exe` 后再打包。
- 免安装分发请复制整个 `electron/dist/electron_new/win-unpacked/` 目录，不要只拷贝 `Codebot.exe`，否则会缺少 `resources` 下的后端与 `opencode` 运行文件导致无法启动。

## 📝 API 文档

### 聊天 API

- `POST /api/chat/conversations` - 创建对话
- `GET /api/chat/conversations` - 获取对话列表
- `GET /api/chat/conversations/{id}` - 获取对话详情
- `POST /api/chat/conversations/{id}/messages` - 发送消息
- `POST /api/chat/send_stream` - 流式发送消息（NDJSON：步骤事件 + 回复增量）

### 配置 API

- `GET /api/config/file-info` - 获取当前生效配置文件路径
- `POST /api/config/load-from-path` - 从指定 `config.json` 路径导入并应用配置

### 记忆 API

- `GET /api/memory/memories` - 获取记忆列表（支持 `category` 参数按类别过滤）
- `GET /api/memory/memories/archived` - 获取归档记忆列表
- `GET /api/memory/storage-status` - 获取记忆存储诊断信息
- `POST /api/memory/memories` - 创建记忆
- `GET /api/memory/memories/search` - 搜索记忆
- `POST /api/memory/memories/{memory_id}/archive` - 归档记忆
- `POST /api/memory/memories/{memory_id}/restore` - 恢复归档记忆
- `DELETE /api/memory/memories/{memory_id}` - 删除记忆
- `GET /api/memory/hints` - 根据当前输入返回相关记忆提示（供前端实时展示）
- `POST /api/memory/export` - 导出记忆为 ZIP 文件（保存至服务端 `data/backups/`）
- `POST /api/memory/import` - 上传 ZIP 备份文件并恢复（multipart/form-data）
- `POST /api/memory/cleanup` - 手动触发清理（支持 `cleanup_archived_memories` 参数）
- `GET /api/memory/config` - 获取记忆配置
- `PUT /api/memory/config` - 更新记忆配置
- `POST /api/memory/organize` - 手动触发记忆整理（后台异步执行）
- `GET /api/memory/organize/status` - 查询整理任务状态及上次运行时间

### 定时任务 API

- `GET /api/scheduler/tasks` - 获取任务列表
- `POST /api/scheduler/tasks` - 创建任务，支持 `executor` 为 `opencode` 或 `hermes`，支持 `execution_model` 指定任务优先执行模型
- `GET /api/scheduler/tasks/archived` - 获取已归档任务
- `GET /api/scheduler/tasks/{task_id}` - 获取任务详情
- `PUT /api/scheduler/tasks/{id}` - 更新任务
- `DELETE /api/scheduler/tasks/{id}` - 删除任务
- `POST /api/scheduler/tasks/{task_id}/run` - 立即执行任务
- `POST /api/scheduler/tasks/{task_id}/archive` - 归档任务
- `POST /api/scheduler/ai-generate` - AI 生成 Cron 表达式

### 技能 API

- `GET /api/skills` - 获取技能列表
- `POST /api/skills` - 创建技能
- `PATCH /api/skills/{skill_id}` - 更新技能元数据
- `DELETE /api/skills/{skill_id}` - 删除技能
- `GET /api/skills/{skill_id}/content` - 读取 SKILL.md 内容
- `PUT /api/skills/{skill_id}/content` - 更新 SKILL.md 内容
- `POST /api/skills/generate` - 先经 `find-skills` 检索，再按需改造或调用 `skill-creator` 生成技能
- `POST /api/skills/sync-to-opencode` - 高级导出接口，默认禁用；需设置 `CODEBOT_ALLOW_OPENCODE_SKILL_EXPORT=1`
- `POST /api/skills/{skill_id}/sync-to-opencode` - 高级导出单个 Codebot 技能，默认禁用
- `GET /api/skills/opencode-sync-status` - 获取历史导出状态和遗留 `codebot-*` 技能信息

### MCP API

- `GET /api/mcp` - 获取 MCP 服务器列表
- `POST /api/mcp` - 添加 MCP 服务器
- `GET /api/mcp/{server_id}` - 获取单个 MCP 服务器
- `PATCH /api/mcp/{server_id}` - 更新 MCP 服务器配置
- `DELETE /api/mcp/{server_id}` - 删除 MCP 服务器
- `POST /api/mcp/{server_id}/test` - 测试 MCP 服务器连接
- `POST /api/mcp/{server_id}/toggle` - 启用或禁用 MCP 服务器
- `POST /api/mcp/batch-delete` - 批量删除 MCP 服务器
- `GET /api/mcp/modelscope/services` - 获取可导入的 ModelScope MCP 服务
- `POST /api/mcp/modelscope/import` - 导入 ModelScope MCP 服务
- `GET /api/mcp/opencode/sync-status` - 获取 OpenCode MCP 同步状态
- `POST /api/mcp/opencode/sync` - 将 Codebot bridge 同步到 OpenCode 配置
- `GET /api/mcp/codebot/status` - 获取 Codebot bridge 状态
- `GET /api/mcp/codebot/opencode-entry` - 获取写入 OpenCode 的 bridge 配置
- `POST /api/mcp/codebot/register` - 注册 Codebot bridge
- `GET /api/mcp/codebot/sse` - Codebot 自身 MCP SSE 入口
- `POST /api/mcp/codebot/messages` - Codebot 自身 MCP 消息入口

### 通知 API

- `GET /api/notifications` - 获取通知列表
- `GET /api/notifications/unread-count` - 获取未读数量
- `PUT /api/notifications/{id}/read` - 标记通知已读
- `PUT /api/notifications/read-all` - 全部标记已读
- `GET /api/notifications/config` - 获取通知配置
- `PUT /api/notifications/config` - 更新通知配置
- `POST /api/notifications/test-email` - 发送测试邮件

### 沙箱 API

- `GET /api/sandbox/status` - 获取沙箱状态
- `POST /api/sandbox/prepare` - 初始化沙箱工作目录
- `GET /api/sandbox/config` - 获取沙箱配置
- `PATCH /api/sandbox/config` - 更新沙箱配置
- `POST /api/sandbox/start` - 兼容接口，确保本地隔离模式已就绪
- `POST /api/sandbox/stop` - 兼容接口，停止本地模式沙箱状态
- `POST /api/sandbox/test` - 执行沙箱冒烟测试
- `POST /api/sandbox/install-qemu` - 兼容接口，本地模式下返回说明

### 日志 API

- `GET /api/logs/task-logs` - 获取任务日志列表
- `DELETE /api/logs/task-logs/{log_id}` - 删除单条任务日志
- `POST /api/logs/task-logs/batch-delete` - 批量删除任务日志
- `GET /api/logs/chat-logs` - 获取聊天日志列表
- `GET /api/logs/chat-logs/{log_id}` - 获取聊天日志详情
- `DELETE /api/logs/chat-logs/{log_id}` - 删除单条聊天日志
- `POST /api/logs/chat-logs/batch-delete` - 批量删除聊天日志
- `POST /api/logs/chat-logs/cleanup` - 清理过期聊天日志
- `GET /api/logs/config` - 获取日志配置
- `PUT /api/logs/config` - 更新日志配置
- `POST /api/logs/cleanup` - 清理过期任务日志

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

MIT License

## 🙏 致谢

- [OpenCode](https://github.com/anomalyco/opencode) - 开源 AI 编码助手
- [OpenClaw](https://github.com/openclaw/openclaw) - 个人 AI 助手
- [Vue 3](https://vuejs.org/) - 渐进式 JavaScript 框架
- [Element Plus](https://element-plus.org/) - Vue 3 组件库
- [FastAPI](https://fastapi.tiangolo.com/) - 现代 Python Web 框架
- [ChromaDB](https://www.trychroma.com/) - 向量数据库

***

**Codebot** - 你的个人 AI 助手 🦞

## 👨‍💻 作者信息

**余汉波** - 编程爱好者-量化交易和效率工具开发

- **GitHub**: [@yuhanbo758](https://github.com/yuhanbo758)
- **Email**: <yuhanbo@sanrenjz.com>
- **Website**: [三人聚智](https://www.sanrenjz.com)

## 🌐 相关链接

- 🏠 [项目主页](https://www.sanrenjz.com)
- 📚 [在线文档](https://docs.sanrenjz.com)（财经、代码和库文档等）
- 🛒 [插件商店](https://shop.sanrenjz.com)（个人开发的所有程序，包括开源和不开源）

## 联系我们

[联系我们 - 三人聚智-余汉波](https://www.sanrenjz.com/contact_us/)

python 程序管理工具下载：[sanrenjz - 三人聚智-余汉波](https://www.sanrenjz.com/sanrenjz/)

效率工具程序管理下载：[sanrenjz-tools - 三人聚智-余汉波](https://www.sanrenjz.com/sanrenjz-tools/)

智能codebot下载：[sanrenjz-codebot - 三人聚智-余汉波](https://www.sanrenjz.com/sanrenjz-codebot/)

![三码合一](https://gdsx.sanrenjz.com/image/sanrenjz_yuhanbolh_yuhanbo758.png?imageSlim&t=1ab9b82c-e220-8022-beff-e265a194292a)

![余汉波打赏码](https://gdsx.sanrenjz.com/image/%E6%89%93%E8%B5%8F%E7%A0%81%E5%90%88%E4%B8%80.png?imageSlim)

<br />

***

**⭐ 如果这个项目对您有帮助，请给它一个 Star！**
