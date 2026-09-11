# AGENTS.md

> 本文件由 OpenCode 维护，帮助 AI 和开发者快速理解 Codebot 项目的结构、规范和当前状态。
> 请将此文件提交到版本控制，并在每次重要架构、依赖或配置变更后更新。

---

## 项目概述

**项目名称：** Codebot

**项目描述：** Codebot 是以 OpenCode 为默认主链、可选官方 Codex Agent Harness 与 Rakazo 受控试运行执行器的第三方能力工作台。Codebot 负责 MCP 聚合、Skills、记忆、调度、多 Agent 编排、通知、沙箱、OpenAI 兼容网关以及 Electron/Web/VS Code UI。

**当前阶段：** 已有完整应用结构，包含 Python 后端、Vue 前端、Electron 桌面应用、内置技能和发布流水线。

---

## 技术栈

| 类别 | 技术/工具 |
|------|----------|
| 后端语言 | Python 3.11+ |
| 后端框架 | FastAPI, Uvicorn |
| 后端配置 | Pydantic v2, pydantic-settings, python-dotenv |
| HTTP/异步 | httpx, asyncio, aiofiles |
| 数据存储 | SQLite, ChromaDB |
| 向量/检索 | ChromaDB, numpy, onnxruntime |
| 调度 | croniter |
| 通知 | 应用内通知、桌面通知、飞书、邮件、lark-oapi、aiosmtplib |
| 日志 | loguru |
| 安全相关 | HMAC 签名会话、一次性局域网配对码、APP_TOKEN 配置 |
| 前端 | Vue 3, Vite 8, Vue Router 4, Pinia, Element Plus |
| 前端请求 | axios, fetch |
| Markdown/代码展示 | markdown-it, highlight.js |
| 桌面端 | Electron 43, electron-builder, electron-updater |
| 打包 | PyInstaller, electron-builder |
| CI/CD | GitHub Actions release workflow |
| Agent 运行时 | OpenCode CLI / Server；`openai-codex==0.147.0` SDK 与随包 Codex runtime |

---

## 目录结构

```text
codebot/
├── backend/              # Python FastAPI 后端源码与后端打包配置
│   ├── main.py           # 后端入口、生命周期管理、路由挂载、静态资源服务
│   ├── config.py         # Pydantic 配置模型、运行时路径、config.json 加载保存
│   ├── requirements.txt  # Python 依赖
│   ├── api/routes/       # REST/SSE/OpenAI 兼容 API 路由
│   ├── core/             # OpenCode、记忆、技能、调度、沙箱等核心业务模块
│   ├── database/         # SQLite 初始化和数据库连接
│   ├── services/         # 通知、飞书等服务层
│   └── utils/            # 安装、启动 OpenCode 等工具逻辑
├── frontend/             # Vue 3 + Vite 前端应用
│   ├── src/main.js       # 前端入口，注册 Pinia、Router、Element Plus
│   ├── src/App.vue       # 应用外壳、导航和全局交互
│   ├── src/router/       # 页面路由与懒加载
│   ├── src/views/        # Chat、Memory、Scheduler、Skills、MCP、Logs、Settings 等页面
│   ├── src/components/   # 设置页和记忆页等可复用组件
│   └── vite.config.js    # dev server 代理、别名、构建分包
├── electron/             # Electron 主进程、预加载脚本、桌面端打包配置
│   ├── main.js           # 主窗口、后端进程、更新、技能下载、账号安全存储等
│   ├── preload.js        # 渲染进程安全桥接
│   └── package.json      # Electron 依赖和 electron-builder 配置
├── vscode-extension/     # VS Code 侧栏扩展，复用 Codebot 模型、Agent、Skill、知识库与流式聊天 API
├── skills/               # Codebot 内置技能，目录内以 SKILL.md 为技能入口
├── scripts/              # 安装脚本 install.bat / install.sh
├── data/                 # 运行时数据目录，数据库、Chroma、配置、备份等，不应提交敏感数据
├── logs/                 # 运行日志，不应提交
├── .github/workflows/    # GitHub Actions 发布流水线
├── README.md             # 用户文档与功能说明
├── PROJECT_SUMMARY.md    # 项目完成总结和历史说明
├── build.bat             # Windows 一键构建后端、前端、Electron 安装包
├── run.bat               # 快速测试启动脚本
├── start.bat/.sh         # 启动后端脚本
└── AGENTS.md             # 本文件，AI 项目上下文
```

### 运行时与产物目录

- `data/`、`logs/`、`venv/`、`frontend/dist/`、`electron/dist/`、`electron/node_modules/`、`backend/dist/`、`backend/dist_build/`、`backend/build_tmp*/` 是运行时、依赖或构建产物目录。
- 默认不要编辑或格式化产物目录中的文件，除非任务明确要求处理打包输出。
- `.env`、`data/config.json`、数据库文件、日志、备份和用户技能目录可能包含本机配置或敏感信息，不要提交。

---

## 核心模块说明

| 模块 | 路径 | 职责 |
|------|------|------|
| 应用入口 | `backend/main.py` | 创建 FastAPI 应用，初始化数据库、OpenCode 客户端、记忆、通知、沙箱、调度器、飞书机器人，并挂载 API 路由和静态前端资源。 |
| 配置系统 | `backend/config.py` | 定义 `AppConfig`、`Settings` 及各业务配置模型，负责 `data/config.json` 和环境变量加载。 |
| OpenCode 客户端 | `backend/core/opencode_ws.py` | 通过 OpenCode HTTP API 创建 session、发送 prompt、流式读取响应、管理对话队列和模型参数；模型刷新优先使用 `opencode models` 对齐 CLI 视角，HTTP `/provider` 作为兜底，并在发送前校验当前 server 是否已加载所选模型。 |
| Codex 运行时 | `backend/core/codex_runtime.py`, `backend/api/routes/codex.py` | 通过官方 `openai-codex` SDK 管理单个 App Server、多对话持久 thread/turn、审批、问题、中断、回滚、账号、模型、Skills 与事件到 Codebot NDJSON 的映射。 |
| 聊天 API | `backend/api/routes/chat.py` | 对话 CRUD、消息发送、流式输出、附件读取、多Agent群聊调度、分享只读接口、标题生成、技能生成、聊天队列和记忆提取入口。 |
| 记忆系统 | `backend/core/memory_manager.py`, `backend/api/routes/memory.py` | 管理对话、消息、长期记忆、归档、搜索、备份和 ChromaDB 向量检索。 |
| 自动记忆整理 | `backend/core/memory_extractor.py`, `backend/core/memory_organizer.py` | 从聊天中提取背景信息，并按计划进行合并、去重和整理。 |
| 定时任务 | `backend/core/scheduler.py`, `backend/api/routes/scheduler.py` | Cron 任务创建、运行、归档、执行日志和通知联动；按 OpenCode/Codex 分流，非提醒 AI 任务按次注入无人值守执行契约，Codex 任务固定非交互 `deny_all` 和工作区沙箱，并在模型不可用时回退到记忆整理模型。 |
| Codex 模型桥 | `backend/core/codex_model_bridge.py` | 通过显式协议适配器注册表将 Codex Responses 请求翻译为 OpenCode provider 的 OpenAI-compatible Chat Completions 或 Anthropic Messages，并把正文/工具调用还原为 Responses output item；新增协议不会侵入运行时主流程，也不复制 Agent 循环。 |
| 共享模型路由 | `backend/core/model_route_registry.py` | 以 OpenCode `/provider` 为唯一模型清单，生成不含凭据的稳定路由指纹，并为 Codex/Rakazo 显式区分 Responses、Chat Completions、Anthropic 及未适配协议；Rakazo 只消费当前 Server 已连接、已启用的模型，并在宿主侧复用 API Key 或 OpenCode 内置 OpenAI OAuth 同一连接，不展示未连接 Provider 的静态全目录。 |
| Rakazo 运行时 | `backend/core/rakazo_runtime.py`, `backend/api/routes/rakazo.py`, `integrations/rakazo*` | Python 运行时适配层管理项目唯一 Bot/Thread/Computer/主会话、oRPC/事件、模型实测门禁、项目级 MCP、权限与日志脱敏及 Docker/契约握手；四项项目权限均可即时持久化，命令在 dedicated Computer 内执行并按账号级审批规则保守合并，外部网络由每 Bot 精确加盐的 Docker `--internal`/普通 bridge 控制。对话只固定执行器和项目身份，当前模型可在空闲时从聊天顶部切换，每轮保留真实路由快照。远端 Bot 丢失时仅在错误或官方列表复核明确不存在后自愈，保留原 Codebot 会话、权限、路由和 Computer home；确定性项目 MCP slug 会精确清理失败重试孤儿。删除 Rakazo 主会话先清理 Bot/Thread/Memory、项目 MCP、专属 Computer/网络/home，再删除本地映射与对话，始终保留宿主项目文件、Docker Desktop 和共享运行时。固定实验版在 Bot 创建、换模和已有会话发送前通过上游官方 `models.credentials/models.connect` 幂等建立或迁移本地 Provider 记录，保存版本化的 Codebot 私网桥协议键并与 Adapter 入口校验一致；Adapter 再换成随机进程采样令牌，始终不复用 OpenCode 密钥。TypeScript 侧车代理真实上游模型流，并在 api/worker 网络命名空间内提供 localhost 项目 MCP 回环。Rakazo 默认禁用，用户未主动打开安装向导时不探测 Docker、WSL、Compose 或模型目录。稳定通道失败关闭；实验通道只允许兼容清单固定的官方提交、Compose 摘要和镜像摘要，使用 DPAPI 运行时密钥、持久 Corepack 缓存及启动后镜像身份校验。稳定契约端到端通过前不宣称为完整 TypeScript Runtime Adapter，也不修改 Rakazo 上游源码。 |
| Docker 与 Rakazo 桌面编排 | `electron/docker-installer.js`, `electron/rakazo-startup.js`, `electron/main.js` | Windows 桌面端把安装与启动拆成独立 IPC：只有未安装状态可下载 Docker Desktop、限制官方域名、验证 Docker Inc Authenticode 签名、准备 WSL 2 并使用用户所选程序/镜像/虚拟磁盘目录；已安装待启动状态只执行现有 `Docker Desktop.exe`，不进入安装确认、下载、验签或 WSL 安装链。首次安装/授权完成后，一键启动按 Docker、OpenCode 可用模型、Rakazo 运行时、既有 `safeStorage` 授权顺序统一恢复；`auto_start` 在桌面主界面显示后执行同一编排，但不静默安装系统组件或创建账号。通过注册表与自选路径识别已有安装，确认退出码 3 为“版本已最新”后跳过重装；固定路径和日志命中且两组目录内容均通过运行端点白名单预检时，同时隔离 `Docker\run` 与 `docker-secrets-engine` 两组纯运行 socket 目录并重试，旧目录保留可回退，绝不删除 Docker 数据。渲染进程不能传入命令、下载地址或凭据。 |
| 提示词优化 | `backend/core/prompt_optimizer.py` | 用可预测复杂度信号为复杂 Agent 请求和 AI 定时任务按需生成精简执行契约；保留用户/任务原文，不增加额外模型调用。 |
| 技能系统 | `backend/core/skill_registry.py`, `backend/core/skill_generator.py`, `backend/core/tool_dispatcher.py`, `backend/api/routes/skills.py` | 发现内置、自动生成、外部兼容、OpenClaw 兼容和 OpenCode/AgentSkills 技能，读写 Codebot 可写 `SKILL.md`，从对话素材提炼真正的技能正文，并在聊天中注入相关技能上下文；内置多Agent协作调度技能。 |
| MCP 聚合 | `backend/api/routes/mcp.py` | 管理第三方 MCP；保留 OpenCode SSE，并以带 Bearer Token 的标准 Streamable HTTP MCP 向 Codex 暴露记忆、调度、Skills 和第三方工具代理。 |
| 模型网关 | `backend/api/routes/gateway.py` | 提供 OpenAI 兼容 `/v1/models` 和 `/v1/chat/completions` 接口。 |
| 沙箱 | `backend/core/sandbox/manager.py`, `backend/api/routes/sandbox.py` | 支持显式可选的 Windows Sandbox 强隔离后端、可信本地模式、超时控制和失败关闭；默认不选择、不探测或启动 Windows Sandbox。 |
| 通知 | `backend/services/notification.py`, `backend/api/routes/notifications.py` | 应用内、桌面、飞书、邮件通知统一管理。 |
| 日志 | `backend/api/routes/logs.py`, `frontend/src/views/Logs.vue` | 任务日志、聊天日志、已归档对话查看/恢复和日志清理 API/UI。 |
| 成长沉淀 | `backend/core/growth.py`, `backend/api/routes/growth.py` | 从对话沉淀可接受/拒绝的记忆、定时任务和技能候选；任务候选会保留 cron、自然语言时间、通知设置、执行器和执行模型；接受后按类型分别写入记忆、调度器或 Codebot 自动生成技能目录，技能候选会先生成真正的 `SKILL.md` 工作流而不是直接保存聊天记录。 |
| 前端应用 | `frontend/src/` | Vue 页面、组件、Pinia store、Element Plus UI 和 API 调用。 |
| Electron 桌面端 | `electron/main.js`, `electron/preload.js` | 启动/连接后端、加载前端、自动更新、技能下载、内置浏览器、账号 token 安全存储。 |
| VS Code 扩展 | `vscode-extension/extension.js` | 在 VS Code 侧栏复用 Codebot/OpenCode/Codex 对话、原生模型、reasoning effort、Agent 模式、Skill 与 Obsidian，并把当前工作区作为项目目录。 |

---

## API 与路由结构

后端在 `backend/main.py` 中挂载主要路由：

| 前缀 | 路由文件 | 说明 |
|------|----------|------|
| `/api/chat` | `backend/api/routes/chat.py` | 聊天、对话、流式响应、文件读取、模型列表 |
| `/api/memory` | `backend/api/routes/memory.py` | 记忆 CRUD、搜索、归档、备份、整理 |
| `/api/scheduler` | `backend/api/routes/scheduler.py` | 定时任务和 Cron 辅助生成 |
| `/api/skills` | `backend/api/routes/skills.py` | 技能列表、创建、编辑、删除、同步 |
| `/api/mcp` | `backend/api/routes/mcp.py` | MCP 服务管理、ModelScope 导入、OpenCode 同步 |
| `/api/config` | `backend/api/routes/config.py` | 应用配置读写、文件路径配置 |
| `/api/notifications` | `backend/api/routes/notifications.py` | 通知列表、未读数和通知配置 |
| `/api/logs` | `backend/api/routes/logs.py` | 任务日志、聊天日志、清理策略 |
| `/api/lark` | `backend/api/routes/lark.py` | 飞书机器人配置与状态 |
| `/api/sandbox` | `backend/api/routes/sandbox.py` | 沙箱状态和配置 |
| `/api/growth` | `backend/api/routes/growth.py` | 成长候选管理 |
| `/api/codex` | `backend/api/routes/codex.py` | Codex App Server 状态、重启、模型、账号与登录登出 |
| `/api/rakazo` | `backend/api/routes/rakazo.py` | Rakazo 状态、项目主会话、模型兼容性、权限、Computer、日志和官方稳定 Release 只读检查；当前不提供更新写操作 |
| `/api/internal/model-sampling/v1` | `backend/api/routes/rakazo.py` | 带随机进程令牌的 Rakazo 单次模型采样接口；支持 Chat/Responses/Anthropic 原生上游 SSE 与取消传播，禁止进入 OpenCode Session/Agent 链 |
| `/v1` | `backend/api/routes/gateway.py` | OpenAI 兼容模型网关 |

---

## 开发与运行命令

### 后端开发

```bash
venv\Scripts\activate
python backend\main.py
```

Linux/macOS：

```bash
source venv/bin/activate
python backend/main.py
```

### 前端开发

```bash
cd frontend
npm install
npm run dev
```

前端开发服务器默认 `http://localhost:3000`，`/api` 和 `/logo.ico` 代理到 `http://localhost:18080`。

### Electron 开发

```bash
cd electron
npm install
npm start
```

Electron 开发模式默认使用仓库中的 Python 后端源码启动 `backend/main.py`；如需强制使用后端 exe，可设置 `CODEBOT_BACKEND_MODE=exe`。

### 构建

```bash
build.bat
```

构建流程包括创建/复用 venv、安装 Python 依赖、PyInstaller 打包后端、Vite 构建前端、electron-builder 打包桌面应用。

### 发布

- `.github/workflows/release.yml` 在推送 `main` 或手动触发时运行。
- 发布流会自动将应用版本按 minor 规则递增（如 `1.1.0 -> 1.2.0`、`1.9.0 -> 2.0.0`），同步到后端、前端和 Electron 的版本文件后提交回仓库。
- CI 使用 Node 22 和 Python 3.11，在 Windows、macOS、Linux 三个平台分别构建前端、PyInstaller 后端和 Electron 安装包，并统一发布到 GitHub Releases。

---

## 配置与数据

- 环境变量示例在 `.env.example`。
- 本地敏感环境变量应放在 `.env`，不要提交。
- 运行时配置默认保存在 `data/config.json`，由 `backend/config.py` 的 `load_config()` / `save_config()` 管理。
- 聊天回复语言由 `general.language` 控制，跟随“设置 → 通用设置 → 语言”，未配置时默认 `zh-CN`。
- 默认 OpenCode Server 地址是 `http://127.0.0.1:11200`。
- `opencode.cli_path` 可配置 OpenCode CLI 可执行文件或所在目录；为空时依次尝试 `CODEBOT_OPENCODE_PATH`、打包目录和系统 PATH；Windows 桌面端还会额外扫描 `%APPDATA%\npm`、Scoop shim、WinGet Links 和 Chocolatey bin 等常见安装目录。Codebot 自己拉起 OpenCode Server 时，会先把用户全局 `~/.config/opencode/opencode.json` 中的 `provider` 合并到 `data/opencode-config/opencode.json`，并通过 `OPENCODE_CONFIG_HOME=data/opencode-config` 启动 OpenCode；不要同时覆写 `XDG_CONFIG_HOME`，否则新 provider 可能不会被当前 server 加载。
- Web 后端默认监听 `0.0.0.0:15682`；Electron 源码开发模式默认使用 `18080`。
- Electron 会为打包环境注入 `CODEBOT_DATA_DIR` 和 `CODEBOT_RESOURCES_DIR`，使数据目录可写并定位内置资源。
- 数据库文件位于 `data/conversations.db`、`data/scheduled_tasks.db` 等路径。
- ChromaDB 默认目录是 `data/chroma/`。
- MCP 配置文件默认是 `data/mcp_servers.json`。
- Rakazo 映射、探测与审计记录保存在 `data/rakazo.db`；会话令牌不写入配置或明文文件。桌面一键授权的随机本机账号与会话由 Electron `safeStorage` 加密保存并通过带随机桌面桥令牌的内部接口恢复；非桌面自管部署仍可从 `CODEBOT_RAKAZO_SESSION_TOKEN` 注入。

---

## 编码规范与模式

### Python 后端

- 使用 `async`/`await` 实现 OpenCode 调用、调度、通知和 API 中的异步流程。
- API 数据结构优先使用 Pydantic `BaseModel` 定义请求/响应模型。
- 路由文件使用 `APIRouter()`，由 `backend/main.py` 统一挂载并注入跨模块依赖。
- 运行期单例依赖通常在 `main.py` 的 lifespan 中初始化，再赋值给对应 router 模块变量。
- 日志统一使用 `loguru.logger`。
- 路径处理优先使用 `pathlib.Path`，跨平台路径不要硬编码分隔符。
- 数据库访问当前以 `sqlite3` 同步连接为主，表结构集中在 `backend/database/init_db.py` 初始化。
- 配置变更应同步更新 `AppConfig` 子模型、配置 API 和前端设置页。

### 前端

- 使用 Vue 3 单文件组件和 Composition API 风格。
- 路由在 `frontend/src/router/index.js` 中懒加载页面组件。
- UI 组件使用 Element Plus，入口在 `frontend/src/main.js` 注册中文 locale 和图标。
- 跨页面状态使用 Pinia，例如 `frontend/src/stores/notification.js`。
- API 调用多使用相对路径 `/api/...`，开发环境由 Vite 代理到后端。
- 修改前端页面时保持移动端适配和现有 Element Plus 视觉风格。

### Electron

- 主进程使用 CommonJS，集中在 `electron/main.js`。
- 涉及文件系统、下载、账号 token、更新、子进程和系统 API 的逻辑应留在主进程或 preload 暴露的受控接口中。
- 不要把敏感 token 直接暴露给渲染进程；已有账号 token 使用 `safeStorage` 和用户数据目录文件保存。

### Skills

- 每个技能目录以 `SKILL.md` 为入口。
- `backend/core/skill_registry.py` 会解析 Markdown front matter，并区分 `auto_generated`、`builtin`、`external`、`openclaw`、`opencode` 来源。
- Codebot 可写来源是自动生成技能和内置技能；外部兼容目录、OpenClaw 兼容目录与 OpenCode/AgentSkills 技能目录默认视为只读来源。
- 用户在聊天中要求创建、生成、保存或沉淀 skill 时，仍应按用户要求调用 `find-skills`、`skill-creator` 等技能完成搜索、评估、下载、创建和修改流程；如 OpenCode 工具链临时写入 `~/.agents/skills`，任务结束后应迁移为 Codebot `auto_generated` 自动生成技能。

---

## 重要依赖和外部服务

- OpenCode CLI 必须可用，推荐运行 `opencode serve --port 11200 --hostname 127.0.0.1`。
- Codex 目标默认使用 `openai-codex==0.147.0` 随包 runtime；仅在用户选择 `runtime_source=custom` 时读取 `codex_bin`，不要求系统 PATH 另装 Codex。
- Codebot 会尝试自动安装或启动 OpenCode，相关逻辑在 `backend/utils/installer.py` 和 Electron 主进程中。
- 第三方 MCP 服务通过 Codebot 聚合后暴露给 OpenCode，OpenCode 侧通常只需要连接 Codebot 的 MCP 入口。
- ModelScope MCP 需要相关 API Key，配置属于用户本地数据，不应写入仓库。
- 飞书、邮件和对象存储发布凭据均应通过环境变量、GitHub Secrets 或本地配置提供。

---

## 测试与验证建议

当前仓库未发现独立测试框架配置。修改时按影响范围选择最小验证：

- 后端语法/启动验证：`python backend\main.py`，观察 `/api/health` 和启动日志。
- 前端构建验证：在 `frontend/` 运行 `npm run build`。
- Electron 打包相关验证：运行 `build.bat` 或在 `electron/` 运行 `npm start` 做开发验证。
- API 变更验证：检查对应前端页面请求路径和后端路由前缀是否一致。
- 配置变更验证：确认 `data/config.json` 兼容默认值，避免破坏已存在用户配置。

---

## AI 协作注意事项

- 优先修改源码目录，避免编辑 `dist`、`node_modules`、`venv`、`__pycache__`、数据库、日志和构建产物。
- 不要提交 `.env`、本地数据库、日志、备份、账号 token 或用户私有配置。
- 如果修改新增依赖，同步更新 `backend/requirements.txt` 或相应 `package.json`，并记录在本文件变更日志。
- 如果新增后端配置项，同步检查 `backend/config.py`、配置 API、前端设置组件和 README 是否需要更新。
- 设置页里的“文档”通过 `/api/docs/readme` 直接读取项目根目录 `README.md`；凡是影响用户使用路径、设置项或聊天入口的变更，都要同步更新 README、设置文档页和 AGENTS 的变更日志。
- 如果新增路由，同步确认 `backend/main.py` 挂载、前端调用路径和 CORS/静态资源行为。
- 如果新增核心功能模块，更新本文件的核心模块说明和变更日志。
- 修改 OpenCode 流式展示逻辑时，先参考 OpenCode 上游 CLI/TUI 的 tool display 规则；Codebot 不应把 `read`、`glob`、`grep`、`list`、`webfetch`、`skill` 等工具的结果正文默认摊开。
- 项目级智能体规则放在 `.trae/rules/`；涉及 GitHub Release 自动更新、资产命名和补传策略时，优先遵循 `.trae/rules/release-update-compat.md`。
- 保持最小正确变更，不要重写无关模块或格式化整仓。

---

## 重要决策记录（ADR）

### ADR-001：项目初始化文档化

- **时间：** 2026-05-01
- **决策：** 为已有 Codebot 项目生成 `AGENTS.md`，记录真实技术栈、模块结构、运行命令和 AI 协作规范。
- **背景：** 项目已有较多后端、前端、Electron、技能和运行时目录，新会话需要快速区分源码、配置和产物。
- **结果：** 后续 AI 处理功能、Bug、重构或配置变更时，应参考并维护本文件。

---

## 已知问题与注意事项

- `PROJECT_SUMMARY.md` 中部分历史端口描述可能与当前 README 和配置默认值不一致，当前以 `backend/config.py` 和 README 中的 `http://127.0.0.1:11200` OpenCode Server 配置为准。
- 仓库中存在运行时数据、构建产物和依赖目录的本地文件，处理 Git 变更时需谨慎区分源码变更与产物。
- 当前未发现统一测试命令或测试目录，变更验证以启动、构建和针对性 API/UI 检查为主。

---

## 变更日志

| 日期 | 变更内容 | 影响范围 |
|------|----------|---------|
| 2026-09-12 | 修复 v5.4.0 安装后主进程启动时报 `Cannot find module './rakazo-startup'`：`electron/package.json` 的显式 `build.files` 漏掉了 `main.js` 新增的同级依赖，导致开发态和测试正常但发布包 `app.asar` 中没有该文件。现补入 `rakazo-startup.js`，并增加递归检查主进程本地 CommonJS 依赖闭包的打包清单测试，防止后续新增模块再次漏包；Electron 25 项测试、生产依赖审计和 Windows x64 目录打包通过，生成的 `app.asar` 已核验包含五个主进程清单文件 | electron/package.json, electron/tests/package-files.test.cjs, README.md, AGENTS.md |
| 2026-09-11 | 修复 Release 流水线连续故障：确认 Codebot 仅使用进程内 Chroma `PersistentClient` 后，对当前无修复版本且不适用本地嵌入式执行面的两个公告做精确豁免并新增源码边界门禁；Electron 锁文件中的 `@xmldom/xmldom`、`fast-uri`、`js-yaml` 高危漏洞升级到修复版本。CI 从 Node 20 升到 Electron 43 要求的 Node 22，`setup-python` 升到 v7。Windows PyInstaller 不再 `collect_all('onnxruntime')`，避免隔离导入未使用的 `onnxruntime.quantization` 时以 `3221225477` 崩溃，只保留 Chroma 默认嵌入所需的核心推理入口与官方 hook 原生库；Windows 步骤现在会保留 PyInstaller 原始退出码。本地 Python 审计为其余漏洞 0 项、Electron `npm audit` 为 0，Electron 24 项测试通过；Windows clean PyInstaller 构建及 Codex/ONNX 核心 bundle 校验通过，产物不含 quantization | .github/workflows/release.yml, scripts/verify_chromadb_security_exception.py, backend/codebot-backend.spec, electron/package-lock.json, README.md, security-review.md, AGENTS.md |
| 2026-09-01 | 修复 Rakazo 项目权限、失效映射与删除生命周期：命令执行改为 dedicated Computer 内真实执行，并按上游账号级 `approvalRules` 对多个项目保守合并；外部网络由每 Bot 精确加盐 Docker 网络的 `--internal`/普通 bridge 实际控制，切换时保留 home。打开既有主会话会复核上游 Bot，确认已丢失后在项目锁内恢复 Bot/Thread/Computer、项目 MCP、原会话、权限和模型路由；按确定性 MCP slug 清理失败重试孤儿，解决 `bots.get Internal server error` 和后续唯一约束故障。聊天顶部恢复 OpenCode 当前 105 个已启用模型的选择与切换。对话菜单新增“删除并清理项目”，实测删除临时项目后其 Bot/Thread/Memory、MCP、Computer 容器、私有网络、home 和映射均消失，同时宿主项目目录、Docker Desktop 与 8 个共享 Compose 服务保持不变；真实会话已在 Computer 内通过 shell 请求 `https://example.com` 并返回 `CODEBOT_COMMAND_NETWORK_OK 200`。Rakazo 专项 47 项、后端全量 84 项测试与桌面 UI 实测通过，仍保持受控试运行而非生产可用声明 | backend/core/rakazo_runtime.py, backend/api/routes/rakazo.py, backend/api/routes/chat.py, backend/api/routes/config.py, backend/tests/test_rakazo_integration.py, frontend/src/views/Chat.vue, frontend/src/components/RakazoSettings.vue, README.md, AGENTS.md |
| 2026-09-01 | 修复 Rakazo 设置保存与启动编排：设置页不再把服务端只读字段整包回传，改为字段白名单；“随 Codebot 启动”和新项目读取默认值切换后即时保存，失败会回滚界面。新增桌面四项一键启动，统一恢复现有 Docker Desktop、OpenCode 当前可用模型、Rakazo 运行时和 `safeStorage` 本机授权；自动启动复用相同编排，但不会静默安装 Docker 或创建账号。新增已有项目权限列表与逐项目读写即时保存，命令/外网继续按真实合同失败关闭。实机停止 Rakazo 后重启 Codebot，确认 `auto_start=true` 保持、运行时与授权自动恢复；桌面重复点击一键启动返回四项均就绪；权限接口完成写入开启和恢复原值往返。Electron 24 项、Rakazo 后端 44 项和前端生产构建通过 | electron/rakazo-startup.js, electron/main.js, electron/preload.js, electron/tests/rakazo-startup.test.cjs, backend/core/rakazo_runtime.py, backend/api/routes/rakazo.py, backend/tests/test_rakazo_integration.py, frontend/src/components/RakazoSettings.vue, README.md, AGENTS.md |
| 2026-09-01 | 修复 Rakazo 设置页把“启动 Docker”错误路由到安装器的问题：前端、preload 和 Electron 主进程分别提供安装/启动动作，安装 IPC 在弹窗前复核真实安装状态，启动 IPC 只定位并运行现有 Docker Desktop，不要求存储目录、不下载或执行安装包。实机从 `D:\RJ\docker\DockerDesktop\app` 点击启动，未出现安装确认，最终界面显示 Engine 已就绪，`docker version` 返回 Client/Server 29.7.2、Docker Desktop 4.88.1 和 WSL2 Linux Engine。复验同时确认旧恢复逻辑会让 `sailor-ingest.sock` 与 `engine.sock` 交替崩溃；现改为两组目录先共同完成白名单预检，再同时改名隔离并保留可回退副本，未知文件时两组都不移动。Electron 18 项测试、Node 语法检查和前端生产构建通过 | electron/docker-installer.js, electron/main.js, electron/preload.js, electron/tests/docker-installer.test.cjs, frontend/src/components/RakazoSettings.vue, README.md, AGENTS.md |
| 2026-08-31 | 将 Rakazo 明确收敛为完全可选能力：默认禁用且不自动启动，用户未主动打开安装向导时，设置页只读取配置，不探测/下载/安装 Docker，不启用 WSL，不启动 Compose，也不加载模型目录；用户主动进入向导后仍需再次点击“一键安装 Docker”才产生系统变更。修复真实对话发送 401：上游任务实际优先使用 `models.connect` 保存的 `userModelCredential` 作为 Adapter Bearer，旧实现的随机占位值因此与 Adapter 校验不一致。现改为版本化的 Codebot 私网桥协议键，Adapter 入口验证后再换成随机进程采样令牌；已有会话在发送前通过官方合同原位迁移旧记录，不复制 OpenCode 密钥，也不删除 Bot/Thread/Computer。实机以 `opencode-go/glm-5.3-flash` 返回精确验收文本 `RAKAZO_AUTH_FIXED_20260831`，未再出现 401；后端全量 80 项、Electron 14 项、Adapter 4 项和两端生产构建均通过 | backend/core/rakazo_runtime.py, integrations/rakazo/docker-compose.codebot.override.yml, integrations/rakazo-adapter/src/index.test.ts, backend/tests/test_rakazo_integration.py, frontend/src/components/RakazoSettings.vue, README.md, AGENTS.md |
| 2026-08-31 | 修复真实创建 Rakazo 主会话时 `bots.update` 返回 400：锁定上游固定提交后确认 `local` Provider 虽已注册，但路由仍要求用户先有 `userModelCredential`。创建 Bot 和切换模型现通过官方 `models.credentials/models.connect` 幂等补齐 Provider 记录，不复制 OpenCode/API/OAuth 凭据；后续实测确认该记录会被上游任务作为 Adapter Bearer 使用，具体 401 根因与版本化私网桥协议键修复见上方更新。oRPC 错误解析补齐嵌套真实消息。对话不再永久绑定首次模型：新建窗口只展示初始模型，Rakazo 聊天顶部提供与 OpenCode/Codex 一致的模型选择器，空闲时先更新远端 Bot 并核对精确身份，再更新映射，历史轮次路由不改写。实机创建项目主会话、Bot/Thread/Computer 成功并完成 `glm-5.3-flash` 与 `glm-5.3` 双向切换 | backend/core/rakazo_runtime.py, backend/api/routes/rakazo.py, backend/tests/test_rakazo_integration.py, frontend/src/views/Chat.vue, README.md, AGENTS.md |
| 2026-08-31 | 按用户最终模型口径把 Rakazo 从“只显示已手工验证 API 路由”改为“镜像 OpenCode 当前可选连接”：聊天新建、交接和模型切换都展示当前 connected 模型，首次选择自动执行八项真实探测；Compose/Pi 预装可安全代理目录，内部采样端点仍只放行已验证路由。`opencode-go` 直接复用 OpenCode 已保存的服务端连接；逐模型严格服从 `@ai-sdk/openai`、`@ai-sdk/openai-compatible`、`@ai-sdk/anthropic` 声明，不再因同一地址存在 Responses 模型而猜协议。实测 `deepseek-v4-pro` 与 `glm-5.3` 均通过八项门禁及容器实链。OpenCode 内置 OpenAI OAuth 由宿主按上游同一 Codex Responses 合同代理，过期时经 OpenCode 本机 Auth API 写回同一连接，token 不进入容器、响应、日志、数据库或路由指纹；本机旧 OAuth 刷新凭据返回 401 时明确要求在 OpenCode 重登，不静默换模。修复推理模型 64 token 用尽返回合法 `response.incomplete` 却被误报为缺少 `response.completed`，并在 Codebot 重启时只对原本运行的受管 adapter 执行幂等收敛，使新随机令牌自动生效而不拉起用户已停止的容器。Codex App Server 不注入需动态轮换的 OpenCode OAuth 路由，继续使用自身官方账号通道 | backend/core/model_route_registry.py, backend/core/codex_model_bridge.py, backend/core/codex_runtime.py, backend/core/rakazo_runtime.py, backend/api/routes/rakazo.py, backend/main.py, backend/tests/, frontend/src/views/Chat.vue, frontend/src/components/RakazoSettings.vue, README.md, AGENTS.md |
| 2026-08-31 | 按用户使用口径收窄 Rakazo 模型目录：不再把 OpenCode `/provider` 中所有未连接 Provider 的静态模型列入兼容性页，只嵌入当前 OpenCode Server `connected` Provider 下实际启用的模型；聊天选择器在此集合上继续只显示通过 Rakazo 真实文本、流式和工具门禁的路由。接口补充 `openCodeEnabledTotal/sourceScope`，设置页明确显示“OpenCode 当前已启用”数量 | backend/core/rakazo_runtime.py, backend/api/routes/rakazo.py, backend/tests/test_rakazo_integration.py, frontend/src/components/RakazoSettings.vue, frontend/src/views/Chat.vue, README.md, AGENTS.md |
| 2026-08-31 | 将 Rakazo 从“稳定版缺合同所以完全不可启动”修正为双通道门禁：稳定 `v0.1.0-beta` 继续失败关闭；用户主动启用时可安装兼容清单锁定的官方 `main@a4ebad0` 实验运行时，校验原始 Compose SHA256、应用/Computer 不可变镜像摘要、OCI revision 和运行容器镜像 ID，不跟踪漂移的 `main/edge`。运行密钥由 Windows DPAPI 保存，临时 `.env` 命令后删除；Docker CLI 可从自选安装目录和注册表发现。修复 Chat/Responses/Anthropic 嵌套异步生成器取消时的并发关闭；Compose 增加 GHCR 瞬时错误有限重试、共享网络命名空间 host 映射和持久 Corepack 预热缓存。实机验证 Docker WSL2 全部服务健康、`deepseek/deepseek-v4-flash` 八项模型门禁、容器内非流式精确响应、35 个 SSE 事件及强制重建 36.3 秒恢复；仍标记非生产就绪，本机建号与 Bot/Thread/Computer 真实流程等待用户明确授权 | backend/config.py, backend/api/routes/config.py, backend/core/codex_model_bridge.py, backend/core/rakazo_runtime.py, backend/api/routes/rakazo.py, backend/tests/test_rakazo_integration.py, frontend/src/components/RakazoSettings.vue, integrations/rakazo*, .gitignore, README.md, AGENTS.md |
| 2026-08-31 | 修复 Docker Desktop 自选安装目录被误判为未安装并反复执行安装器的问题：状态检测同时读取用户所选目录、卸载注册表和安装目录内 CLI，退出码 3 仅在复核“现有版本已是最新”后转为启动；修复首次异常退出留下 Windows AF_UNIX 重解析点后 Engine 连续崩溃的问题，仅在本轮后端日志精确命中 `Docker\run\sailor-ingest.sock` 或 `docker-secrets-engine\engine.sock`、目录内容完全属于运行端点白名单时，按安装路径关闭崩溃进程并把运行目录改名隔离，旧目录保留可回退，最多按两类各恢复一次。实机验证 Docker Desktop 4.88.1 位于自选 D 盘、WSL 2 数据仍在自选目录，`docker version` Client/Server 29.7.2、`docker info` 为 WSL2 Linux Engine；无需卸载，也不在 Ubuntu 中重复安装 Docker Engine。设置页区分“未安装 / 已安装待启动 / Engine 已就绪” | electron/docker-installer.js, electron/main.js, electron/preload.js, electron/tests/, frontend/src/components/RakazoSettings.vue, README.md, AGENTS.md |
| 2026-08-31 | 补齐 Rakazo 预览链中 Codebot 可控的运行门禁：Windows 桌面端新增 Docker Desktop 一键安装、官方域名限制、Docker Inc 签名验证、WSL 2 准备、安装进度和用户自选 Docker/镜像/WSL 大文件目录；NSIS 改为可选择 Codebot 程序目录的向导安装。真实安装复测发现 `powershell.exe -Command` 后的路径不会进入 `$args`，且开发环境继承的 PowerShell 7 `PSModulePath` 会阻断 Windows 签名模块加载；现改为进程环境安全传递路径、固定系统 Windows PowerShell、重建模块路径并统一 UTF-8 输出，24 小时内有效缓存重新验签后直接复用。Rakazo 授权改为 Electron safeStorage 加密的一键本机账号并支持后端重启恢复；采样桥实现 Chat Completions、Responses、Anthropic 原生 SSE、工具增量与取消传播；api/worker 通过共享网络命名空间的 localhost sidecar 使用每项目令牌 MCP。设置页改为四步安装向导。该阶段因官方稳定 Release 仍缺本地模型与已发布 Compose 而保持失败关闭，随后由同日更晚的固定实验通道在不放开稳定门禁的前提下提供可运行路径 | electron/docker-installer.js, electron/main.js, electron/preload.js, electron/package.json, electron/tests/, backend/core/codex_model_bridge.py, backend/core/rakazo_runtime.py, backend/api/routes/rakazo.py, frontend/src/components/RakazoSettings.vue, integrations/rakazo*, README.md, AGENTS.md |
| 2026-08-31 | 新增 Rakazo 第三原生执行器的初版生产门禁预览链：项目唯一主会话与 Bot/Thread/Computer 映射、现有聊天流/状态抽屉/设置页、OpenCode 共享模型路由和四态探测、无 OpenCode Agent 嵌套的单次纯采样桥、项目级令牌 MCP、路径与权限失败关闭、契约版本握手、脱敏审计及不可安装更新清单；执行器菜单新增用户审阅确认的一次性脱敏交接摘要，源会话不改写；针对 6855 模型目录增加单次探测记录读取和服务端分页。当时 TypeScript 侧车仅代理模型采样，尚缺上游原生流式与可接受的私网项目 MCP 端点，因此模型验证失败关闭；这些 Codebot 侧缺口由同日后一条变更补齐。Codex 继续随 Codebot 版本升级，不增加热更新 | backend/core/model_route_registry.py, backend/core/rakazo_runtime.py, backend/core/codex_model_bridge.py, backend/api/routes/rakazo.py, backend/api/routes/chat.py, backend/config.py, backend/core/memory_manager.py, frontend/src/views/Chat.vue, frontend/src/components/RakazoSettings.vue, integrations/rakazo*, electron/package.json, README.md, AGENTS.md |
| 2026-08-22 | 扩展 Codex Harness 的 OpenCode 模型兼容层：Responses/同地址双协议模型直连，OpenAI-compatible Chat Completions 与 Anthropic Messages 通过带随机进程令牌的本机 Responses 中间层桥接；转换器改为显式协议适配器注册表并在状态页展示协议覆盖，第三方密钥不进入 Codex 子进程；新增权威 `model.route` 解决 Harness/上游模型身份混淆，并在空闲刷新模型时自动加载新增 provider。实测官方 `deepseek/deepseek-v4-flash` 经完整 Codebot → Codex App Server → 协议桥链返回指定结果并成功调用 Codex PowerShell 工具；Agent 复杂任务按需追加目标/验收契约，AI 定时任务按次追加无人值守/重试/证据契约，纯提醒保持零模型调用 | backend/core/codex_runtime.py, backend/core/codex_model_bridge.py, backend/core/prompt_optimizer.py, backend/api/routes/codex.py, backend/api/routes/chat.py, backend/core/scheduler.py, backend/tests/, frontend/src/views/Chat.vue, frontend/src/components/CodexSettings.vue, vscode-extension/, README.md, AGENTS.md |
| 2026-08-22 | 以官方 `openai-codex==0.147.0` SDK/App Server 全面替换活跃 Hermes 执行链：新增持久 thread/turn、回滚、中断、审批、账号/模型/Skills、Streamable HTTP MCP、配置/任务/成长候选/前端/VS Code 迁移和 bundled runtime 打包验收；保留 OpenCode 默认链及历史变更记录 | backend/core/codex_runtime.py, backend/api/routes/codex.py, backend/api/routes/chat.py, backend/api/routes/mcp.py, backend/config.py, frontend/, vscode-extension/, backend/codebot-backend.spec, .github/workflows/release.yml, README.md, AGENTS.md |
| 2026-08-09 | 修复 Release 依赖安全门禁发现的真实 Python CVE：升级 FastAPI/Starlette、Pydantic、python-multipart、python-dotenv 和 httpx，移除源码未使用的 aiosmtplib、python-jose、passlib/bcrypt 及 PyInstaller hidden imports；CI 改为审计项目 requirements，避免把 pip-audit 自身环境混入运行时报告 | backend/requirements.txt, backend/codebot-backend.spec, .github/workflows/release.yml, security-review.md, AGENTS.md |
| 2026-08-09 | 将 Windows Sandbox 调整为显式可选隔离后端：默认 `none`，不探测、不安装、不启动；仅在用户主动选择后检查运行时，未配置或不可用时高风险执行失败关闭；设置页、API、回归测试和安全文档同步更新 | backend/config.py, backend/core/sandbox/manager.py, backend/api/routes/sandbox.py, backend/tests/, frontend/src/components/SandboxSettings.vue, README.md, security-review.md |
| 2026-08-09 | 完成 Codebot 本体全项目效率与安全审计：修复 Electron 任意来源技能下载/归档穿越、外链协议与网页权限边界，收紧浏览器 API Origin、文件路径白名单、附件与聊天队列上限，统一后台协程回收和配置密钥脱敏；明确原“沙箱”为非 VM/容器的受控工作区并清理子进程常见凭据；升级 Vite/Electron 构建链并使两端 npm audit 归零，新增安全审查报告与回归测试 | backend/main.py, backend/api/routes/, backend/core/sandbox/, backend/utils/, backend/tests/, frontend/, electron/, README.md, security-review.md |
| 2026-08-09 | 重构 Codebot 本体定时任务可靠性：5 秒检查、到期先认领、并发槽、错过执行合并/审计、超时、有限重试、防重入、启动中断恢复和运行状态；AI 时间无法识别时不再默认每天 09:00。多Agent群聊从普通对话列表移到搜索框上方的默认折叠入口，同步骤真正并行并限制依赖上下文长度 | backend/core/scheduler.py, backend/api/routes/scheduler.py, backend/api/routes/chat.py, backend/tests/, frontend/src/views/Scheduler.vue, frontend/src/views/Chat.vue, README.md |
| 2026-08-08 | 修复 VS Code 聊天把 OpenCode `session.error` 隐藏后以空白 `done` 结束、流建立失败时输入区永久锁定，以及扩展重载后重复发送的问题；新增运行态恢复、工具/状态/空闲进度、停止、逐条/整段复制和基于 Codebot 独立 Git 快照的对话撤销；后端将无正文的终止性会话错误提升为真实流错误 | backend/api/routes/chat.py, vscode-extension/, README.md, AGENTS.md |
| 2026-08-08 | 用户实测 Shift 拖入 WebviewView 仍不可用后，删除不可达的 Webview DOM 拖拽链路；新增原生 `TreeDragAndDropController` 文件拖放区，通过 `text/uri-list` 接收资源管理器文件并加入聊天上下文，无需任何修饰键；保留文件按钮和资源管理器右键入口 | vscode-extension/, README.md |
| 2026-08-08 | 根据 VS Code 1.132 Workbench 真实代码修正文件拖放边界：宿主在普通拖动时禁用 Webview iframe 的 pointer events，仅按住 Shift 才放行；保留 Shift 拖入并恢复底部“文件”原生选择器，同时增加资源管理器右键“将文件加入对话”，避免继续把宿主拦截误判为 MIME 解析问题 | vscode-extension/, README.md |
| 2026-08-08 | 修复 VS Code 资源管理器文件拖入聊天框后静默无响应：补充 `CodeFiles`、`ResourceURLs`、`application/vnd.code.uri-list` 和标准 URI 列表解析，并为无法识别/读取的拖拽显示错误；按用户要求删除“会话设置”折叠入口，模式、目标、模型固定显示在输入框下方、“终端选区”左侧 | vscode-extension/, README.md |
| 2026-08-08 | 将 VS Code 扩展“会话设置”移到输入框下方；编辑器选区停止变化后通过 CodeLens 就地显示“加入 Codebot 对话”，文件改为工作区拖拽加入；由于稳定 VS Code API 不提供终端选区变化事件，仅保留底部“终端选区”和终端右键兜底 | vscode-extension/, README.md |
| 2026-08-08 | 重构 VS Code 扩展为固定底部输入区和独立滚动消息区；会话设置默认折叠，知识库改为 `#` 按需弹出；增加历史续聊、Editor 模式、编辑器/终端选区、拖拽文件、`/ @ #` 快速插入，并统一使用 Codebot 原生图标；交互改为 Enter 发送、Ctrl+Enter 换行 | vscode-extension/, backend/api/routes/chat.py, README.md |
| 2026-08-08 | 更新 Hermes Agent 到 v0.20.0 并适配 `hermes chat -q`、动态配置版本、浅克隆/安全更新和终端污染清洗；修复 Agent 模式每轮注入完整人设与无效模式值问题；聊天栏新增 VS Code 按钮；新增 VS Code Chat 扩展及 Release VSIX 构建；修复 ChromaDB 0.4.22 与新版 PostHog 的遥测调用不兼容，并更新前端/Electron 生产依赖锁文件 | backend/api/routes/chat.py, backend/api/routes/hermes.py, backend/core/memory_manager.py, backend/requirements.txt, frontend/src/views/Chat.vue, electron/main.js, electron/preload.js, vscode-extension/, skills/, .github/workflows/release.yml, build.bat, README.md |
| 2026-07-17 | 完成 `npm start` 开发版真实运行验证，确认开发版源码后端监听 18080、OpenCode 正常连接，OpenAI SDK 与 requests 均可调用新版内容块；README 补充正式版 15682 与开发版 18080 的网关端口区别 | README.md, AGENTS.md |
| 2026-07-17 | 修复新版 OpenAI 兼容客户端将 Trae 内部提示词暴露给用户的问题：分离 user 消息中的 system-reminder 与 user_input，并对非流式和跨分片流式输出增加内部提醒防泄漏过滤 | backend/api/routes/gateway.py, README.md, AGENTS.md |
| 2026-07-17 | 升级 OpenAI 兼容模型网关：兼容新版 `messages[].content` 内容块数组、developer/tool/tool_calls 消息、新版附加请求字段及流式 `include_usage`，并补充无需 OpenAI SDK 的 requests 调用示例 | backend/api/routes/gateway.py, README.md, AGENTS.md |
| 2026-07-11 | 为选中项目的对话增加每轮独立 Git 快照，撤销消息时同步恢复项目文件；OpenCode 定时任务先进入成长候选并在用户接受后创建，顶部待审数量和已打开列表自动刷新；记忆候选收紧长期价值门槛、单轮数量及近义去重；将沙箱工作区接入 OpenCode 聊天执行链；清理调度器协程和过期聊天运行态缓存；聊天页并行加载详情和消息 | backend/core/project_versioning.py, backend/api/routes/chat.py, backend/core/growth.py, backend/core/memory_extractor.py, backend/core/sandbox/manager.py, backend/core/scheduler.py, frontend/src/App.vue, frontend/src/views/Chat.vue, README.md, AGENTS.md |
| 2026-06-14 | 修复聊天页同时选中 Hermes 与 Obsidian 时仍静默卡住的问题：前端现在把双选状态发送为组合目标 `hermes_obsidian`，后端同时进入 Hermes CLI 执行链和 Obsidian Markdown 上下文构建链，并固定加载 Hermes 原生 `note-taking/obsidian` skill；Hermes 选中 skill 时 `skills.external_dirs` 现在收窄到可解析该 skill 的根目录而不是 leaf 目录或全量 roots，避免正式版多套 Obsidian skill 同名/重复目录导致 Unknown skill、歧义或长时间静默；同时 Hermes CLI 异常退出、空响应、空闲超时或连续 180 秒无可见输出时会返回明确错误而不是无限 `session.idle` | backend/api/routes/chat.py, backend/api/routes/hermes.py, frontend/src/views/Chat.vue, README.md, AGENTS.md |
| 2026-06-14 | 修复正式版只点击 Obsidian、未用 `#` 选择具体知识库时仍报 `[WinError 3] ... <vault>/.opencode/skills/agents` 的问题：根因是 Obsidian 整库 Markdown 搜索默认扫描整个 Vault，未排除 `.opencode`，遇到 OpenCode/Obsidian Vault 内缺失或损坏的工具目录会在 `Path.glob("**/*.md")` 阶段抛错；现将 `_search_markdown_notes()` 改为 `os.walk(..., onerror=...)`，显式跳过 `.opencode`、`.obsidian` 等工具/依赖目录并忽略坏目录 | backend/api/routes/chat.py, README.md, AGENTS.md |
| 2026-06-14 | 修复正式版选中 Obsidian 后发送消息失败的问题：聊天执行层现在会把 OpenCode session workspace 明确传给 `backend/core/opencode_ws.py`，普通项目使用用户选择的 `project_dir`，而 `target=obsidian` 时固定到 Codebot 可写数据目录，避免正式版把 Obsidian Vault 当作 OpenCode 工作区并访问不存在的 `<vault>/.opencode/skills/agents`；同时 OpenCode client 按 conversation 记录 workspace，目录变化时自动新建 session，终止任务时清理 workspace 缓存 | backend/api/routes/chat.py, backend/core/opencode_ws.py, README.md, AGENTS.md |
| 2026-06-14 | 继续按运行时证据修复 Hermes 显式 skill 调用回归：`backend/api/routes/hermes.py` 的 `_configured_skill_dirs(selected_skills)` 之前虽然接收了 `selected_skills`，但实际始终返回全量共享根目录，导致用户显式选中单个 Hermes skill 时，运行态 `skills.external_dirs` 仍会挂整包共享 roots 并触发 Hermes 全量扫描；现已改为在显式选中 skill 时，把 `external_dirs` 收窄到所选 skill 的精确目录集合，仅在无法解析目标 skill 时才回退到旧的全量共享模式 | backend/api/routes/hermes.py, README.md, AGENTS.md |
| 2026-06-14 | 修复聊天页 `@` 选中 skill 后 Hermes 实际未收到 skill 参数的回归问题：`frontend/src/views/Chat.vue` 一直写入 `使用技能 @[skill.id] 技能名`，但 `backend/api/routes/chat.py` 的 `_extract_requested_skill()` 之前只匹配 `使用技能[skill_id]`，导致 `selected_skill` 为空、Hermes 分支不会追加 `--skills`；现已兼容 `@[...]` 标记并在清洗消息时移除整段调用标记 | backend/api/routes/chat.py, README.md, AGENTS.md |
| 2026-06-14 | 按用户要求回调 Hermes 自动导入语义：`backend/core/skill_registry.py` 再次把仓库内 `hermes-agent/skills` 与 `optional-skills` 恢复为默认自动导入的原生 Hermes skill 根，但继续支持通过 `excluded_auto_skill_dirs` 排除；同时 `frontend/src/views/Skills.vue` 新增按 `运行时 / 官方仓库 / 手动目录` 的来源细分过滤，README 与设置页说明同步修正 | backend/core/skill_registry.py, backend/api/routes/hermes.py, frontend/src/components/HermesSettings.vue, frontend/src/views/Skills.vue, README.md, AGENTS.md |
| 2026-06-14 | 为技能页补充 Hermes 来源细分标签：`backend/core/skill_registry.py` 现为 Hermes 技能输出 `source_detail/source_detail_label`，按路径区分 `运行时 / 官方仓库 / 手动目录`；`frontend/src/views/Skills.vue` 在“来源”列追加二级标签，方便排查 skill 来自 `HERMES_HOME/skills`、仓库 bundled/optional 目录，还是用户手动添加目录 | backend/core/skill_registry.py, frontend/src/views/Skills.vue, README.md, AGENTS.md |
| 2026-06-14 | 按 Hermes 官方 skills 文档收敛自动导入目录：`backend/core/skill_registry.py` 不再把仓库内 `hermes-agent/skills` 和 `optional-skills` 视为已启用的原生活动 skill 根，而是仅自动识别 Codebot 实际运行时的 `HERMES_HOME/skills`；`backend/api/routes/hermes.py` 与设置页说明同步更新，减少 bundled/optional 与 home 已安装 skill 的重复扫描和重复展示 | backend/core/skill_registry.py, backend/api/routes/hermes.py, frontend/src/components/HermesSettings.vue, README.md, AGENTS.md |
| 2026-06-14 | 继续修复 Hermes / Obsidian 两条实际故障：`backend/core/skill_registry.py` 的最终去重从仅按路径改为对只读来源额外按 `source + slug` 折叠，避免同名 Hermes skill 因多根目录重复展示；`backend/api/routes/chat.py` 中 `target=obsidian` 现在禁止回退到 Vault 内 OpenCode skill，并在选中本地 Obsidian skill 后立即注入其 `SKILL.md`，避免正式版继续访问不存在的 `<vault>/.opencode/skills/agents`；同时清理了文件中误混入的 `“Obsidian”` 脏文本 | backend/core/skill_registry.py, backend/api/routes/chat.py, README.md, AGENTS.md |
| 2026-06-14 | 修复聊天附件 PDF 兜底解析依赖名不一致：`backend/api/routes/chat.py` 的延迟导入从旧的 `PyPDF2` 改为与 `backend/requirements.txt` 和打包配置一致的 `pypdf`，并同步修正文案提示 | backend/api/routes/chat.py, README.md, AGENTS.md |
| 2026-06-14 | 修复三类聊天接入问题：Hermes 设置新增 `excluded_auto_skill_dirs`，自动共享 skill 目录支持排除以避免开发版/正式版重复导入；Obsidian 目标自动优先选择本地内置/兼容 Obsidian skill，避免正式版误依赖不存在的 `<vault>/.opencode/skills/agents`；聊天发送前仅在消息明显像定时任务时才触发额外 AI 分类，减少 Hermes/OpenCode/Obsidian 首响延迟 | backend/config.py, backend/api/routes/config.py, backend/core/skill_registry.py, backend/api/routes/hermes.py, backend/api/routes/chat.py, frontend/src/components/HermesSettings.vue, README.md, AGENTS.md |
| 2026-06-13 | 基于运行时证据修复 Hermes 共享 skill 暗箱卡死：确认显式点名的 OpenCode 共享 skill（如 `gs-data-manager`）在 Hermes 子进程中会长时间静默不退出，因此 `backend/api/routes/chat.py` 现在在 Hermes 目标下会识别 `source=opencode` 的显式 skill，并透明兼容分流到 OpenCode 原生执行链；聊天流先发出 `session.compat` 说明，再继续展示 OpenCode 的真实 tool/step 事件，避免重复 `session.idle` 黑箱等待 | backend/api/routes/chat.py, README.md, AGENTS.md, debug-hermes-skill-stuck.md |
| 2026-06-13 | 继续补齐 Hermes 与 OpenCode CLI 的 skill 共享实现：`backend/api/routes/hermes.py` 现在会把 Hermes Agent 自身默认可发现的 skill 目录（安装目录、`HERMES_HOME` 等）也并入共享目录集合，而不再只共享“用户手填 Hermes 目录 + Codebot skills + OpenCode skill roots”；Hermes 设置页说明与 README 同步修正 | backend/api/routes/hermes.py, frontend/src/components/HermesSettings.vue, README.md, AGENTS.md |
| 2026-06-13 | 对齐用户关于 Hermes / Obsidian / 生成技能的要求：保留设置页中“通用设置”右侧的 Hermes / Obsidian 标签；`/api/skills/generate` 现改为优先调用 `find-skills` 检索并按“差异 < 40% / 相似度 >= 60%”决定是改造现有 skill 还是走 `skill-creator` 创建新 skill，最终统一落到 Codebot `auto_generated` 目录；同时在 Obsidian 目标下主动注入 Obsidian skill 上下文并强化 wiki-link / frontmatter / 模板兼容说明 | backend/api/routes/skills.py, backend/api/routes/chat.py, frontend/src/views/Chat.vue, frontend/src/components/ObsidianSettings.vue, README.md, AGENTS.md |
| 2026-06-13 | 按用户纠偏重构 Hermes 共享 skill 模型：撤销“显式 OpenCode skill 委派回 OpenCode 原生链”和按需单 skill 挂载思路；`backend/api/routes/chat.py` 现在在 Hermes 模式下始终由 Hermes CLI 自己执行，请求里显式点名的 skill 仅作为 `--skills` 提示；`backend/api/routes/hermes.py` 改为默认合并“用户配置的 Hermes Skill 目录 + Codebot 内置/自动生成 skills 根目录 + OpenCode skill roots”为 Hermes `skills.external_dirs`；设置页同步展示自动共享的只读目录和最终生效目录，聊天页移除 `session.delegate` 显示 | backend/api/routes/chat.py, backend/api/routes/hermes.py, frontend/src/components/HermesSettings.vue, frontend/src/views/Chat.vue, README.md, AGENTS.md |
| 2026-06-13 | 修复“用户选 Hermes 但任务显式点名 OpenCode 共享 skill”仍被无关前置分类卡住的问题：`backend/api/routes/chat.py` 现在在流式与非流式执行链中，都会优先识别 `source=opencode` 的显式 skill 请求，并直接从 Hermes 分支委派到 OpenCode 原生执行链；聊天运行态会写入 `session.delegate`，前端显示“执行委派”，不再继续让 Hermes 兼容式模拟 OpenCode skill 加载 | backend/api/routes/chat.py, frontend/src/views/Chat.vue, README.md, AGENTS.md |
| 2026-06-13 | 修复 Hermes 调 OpenCode 共享 skill 仍卡在全量扫描的问题：`backend/api/routes/chat.py` 现在可识别 `调用 xxx skill` / `调用 xxx 技能` / `/skill xxx` 等显式技能口令，并在发送给 Hermes 前剥离口令文本；`backend/api/routes/hermes.py` 将 OpenCode 共享技能改为按需挂载，默认不再把整包 `~/.agents/skills` 挂进 `skills.external_dirs`，而是在本轮明确点名 skill 时只挂该 skill 自身目录。开发版 `npm start` 下已验证 `financial-article-scorer` 请求只挂 2 个目录（Codebot 内置技能根目录 + 目标 skill 目录），且聊天流会显示 skill 已加载与执行轨迹 | backend/api/routes/chat.py, backend/api/routes/hermes.py, README.md, AGENTS.md |
| 2026-06-13 | 继续细化 Hermes 过程映射：清洗后的 stdout 行现在会按内容分类为 `tool_event/tool-call`（如加载/调用 skill、工具步骤）或 `session.trace`（普通运行说明），前端事件气泡同步区分“工具调用 / 运行轨迹 / 会话状态”，开发版 `npm start` 下已验证 `已加载 ... skill` 这类内容会单独显示为工具步骤 | backend/api/routes/hermes.py, frontend/src/views/Chat.vue, README.md, AGENTS.md |
| 2026-06-13 | 继续补齐 Hermes 过程可视化：`backend/api/routes/hermes.py` 现在会把清洗后的 stdout 行同步映射为 `session.trace` 事件，并在长时间无正文输出时定期发出非阻塞 `session.idle` 心跳；`frontend/src/views/Chat.vue` 同步把 `session.status` / `session.idle` / `session.trace` / `session.retry` 作为可见事件渲染，开发版 `npm start` 下已验证聊天流会持续提示“正在后台处理/已静默多久/最后输出什么” | backend/api/routes/hermes.py, frontend/src/views/Chat.vue, README.md, AGENTS.md |
| 2026-06-13 | 继续按运行时证据修正 Hermes CLI 接入：确认 Codebot 之前走的是顶层 `hermes -z/--oneshot`，而该路径在 Hermes 上游源码中明确“只输出最终结果并绕过 `cli.py`”；现已改为走 Hermes 官方 `--cli chat -q` 单次查询入口，开发版 `npm start` 下重新验证 `/api/hermes/chat` 与 `target=hermes` 的 `/api/chat/send_stream` 均能拿到真实 CLI `content_delta` | backend/api/routes/hermes.py, README.md, AGENTS.md |
| 2026-06-13 | 继续收敛 Hermes 聊天展示：`backend/api/routes/hermes.py` 现在对 Hermes stdout 使用增量 UTF-8 解码，避免多字节中文在流式分块时被截断成乱码；同时过滤 ANSI 控制序列、Rich 边框、`Resume this session` / `session_id` / `Session` 等终端辅助行，开发版 `npm start` 下重新验证 `target=hermes` 流式事件只保留正文 `Hi there! How can I help you today?` | backend/api/routes/hermes.py, README.md, AGENTS.md |
| 2026-06-13 | 运行时调试确认开发版 `npm start` 下 Hermes 聊天请求确实会进入真实 `hermes.exe` 子进程；同时发现 Hermes 存在“启动后长期 0 stdout、同消息重试可成功”的间歇性卡死，因此为聊天流新增仅针对“尚未产生任何输出”的 90 秒自动重试 1 次机制，并保留原有人工输入与空闲超时处理 | backend/api/routes/hermes.py, README.md, AGENTS.md |
| 2026-06-13 | 调整 Hermes 聊天空闲提示策略：默认关闭“45s/90s 无输出”的伪交互问题面板，仅在 Hermes CLI 真正输出确认、密码、密钥或输入提示时才要求用户回答；静默执行阶段继续在后台等待真实终端输出 | backend/api/routes/hermes.py, README.md, AGENTS.md |
| 2026-06-13 | 重构 Hermes 接入为薄 CLI 适配器：删除失败的内部 runner/事件桥方案，保留一键安装/修复/更新、共享模型/记忆/定时任务/技能/Obsidian 配置、真实终端流式输出、stdin 人机交互、终止按钮和定时任务 executor 分流；当 Hermes CLI 长时间无输出时会主动在聊天中弹出“继续等待/发送输入/终止任务”面板，避免暗箱卡死到超时 | backend/api/routes/hermes.py, backend/api/routes/chat.py, frontend/src/views/Chat.vue, README.md, AGENTS.md |
| 2026-06-12 | 继续修复 `npm start` 开发态下 Hermes 终端等待后卡死的问题：Hermes 聊天入口不再绕过真实 CLI 导入内部 Agent，而是启动 `hermes -z` 子进程并打开 stdin/stdout 管道；Codebot 会把终端输出追加到聊天气泡，检测到 y/n、确认、密码、密钥或中文“是否/确认/输入”等 CLI 提示时转成 `question.asked` 面板，用户回复后写回 Hermes stdin | backend/api/routes/hermes.py, frontend/src/views/Chat.vue, README.md, AGENTS.md |
| 2026-06-12 | 清理旧 Hermes 人工判断方案：不再依赖自定义内部事件文件或 Hermes 内部 Agent 导入，聊天页统一通过真实 CLI stdout/stderr、显式终端提示检测和 `/question/reply` 写回 stdin 来完成展示与交互 | backend/api/routes/hermes.py, frontend/src/views/Chat.vue, README.md, AGENTS.md |
| 2026-06-12 | 修复聊天页状态串号：普通对话会独立保存模式、模型、Hermes/Obsidian 目标和已选知识库，新对话切换模型不会覆盖旧对话；同时 Hermes 聊天流式分支改为启动真实 `hermes -z` 子进程，像 OpenCode CLI 一样把 stdout/stderr 映射到当前聊天气泡，并在检测到 y/n、确认、密码、密钥或中文“是否/确认/输入”等终端人工提示时，转成聊天页问题选择面板，用户回复后写回 Hermes CLI stdin，避免 Hermes 执行过程成为暗箱或静默等待人工判断直到超时 | frontend/src/views/Chat.vue, backend/api/routes/chat.py, backend/api/routes/hermes.py, README.md, AGENTS.md |
| 2026-06-12 | 参考 Obsidian `obsidian-yuhanbo-opencode` 插件的运行逻辑后，定位到 Codebot 启动 OpenCode Server 的真正问题是配置环境变量组合错误：开发态端口已统一回 `11200`，并修正为仅通过 `OPENCODE_CONFIG_HOME=data/opencode-config` 启动；继续保留全局 `provider` 向隔离 `opencode.json` 的同步。实测按此方式新启动的 server 已能加载 `volcengine` | backend/utils/installer.py, electron/main.js, README.md, AGENTS.md |
| 2026-06-12 | 修复 `npm start` 开发版仍拿不到新 provider 的问题：Electron 开发态的 OpenCode 端口重新统一回 `11200`；Codebot 启动自管 OpenCode Server 前，会把用户全局 `opencode.json` 里的 `provider` 同步到 `data/opencode-config/opencode.json`，避免隔离配置下 `volcengine` 等新 provider 只出现在 `opencode models` 中却无法在当前 server 加载 | electron/main.js, backend/utils/installer.py, README.md, AGENTS.md |
| 2026-06-12 | 继续修复聊天页模型刷新在 Windows 桌面端拿不到新 provider 的问题：`backend/utils/installer.py` 现在即使进程 PATH 与 PowerShell 不一致，也会主动扫描 `%APPDATA%\\npm`、Scoop、WinGet、Chocolatey 等常见目录查找 `opencode/opencode-ai` CLI，避免 `opencode models` 在终端能看到 `volcengine` 但 Codebot 刷新不到；未修改 OpenCode Server `11200` 端口 | backend/utils/installer.py, README.md, AGENTS.md |
| 2026-06-12 | 修复聊天页模型刷新拿不到 OpenCode 最新 provider 的问题：`/api/chat/models` 现在优先调用 `opencode models` 获取 CLI 视角模型；CLI 不可用时回退到当前 `server_url` 的 `/provider` 列表，刷新不会自动切换 OpenCode Server 地址；CLI 已发现但当前 server 尚未加载的模型会标记为未加载并禁止误选，发送前也会校验当前 server 是否支持所选模型；新增 `opencode.cli_path` 以解决 Codebot 进程 PATH 与终端/Obsidian 不一致时找不到 CLI 的问题；同时过滤 Windows 下伪装成 `.exe` 的 shell wrapper，补充 `.cmd/.bat/.ps1` 命令发现 | backend/config.py, backend/core/opencode_ws.py, backend/api/routes/chat.py, backend/utils/installer.py, frontend/src/views/Chat.vue, README.md, AGENTS.md |
| 2026-06-12 | 将正式版 Codebot 后端默认端口从 8080 改为 15682，避免与常见本地服务冲突；Electron 正式版启动后端时注入 `CODEBOT_BACKEND_PORT=15682`，Hermes 共享的 OpenAI 兼容网关地址同步跟随该端口，源码开发模式继续使用 18080；读取旧 `data/config.json` 时会把老默认 `network.port=8080` 自动迁移到 15682；同时修复打包版 Hermes 准备运行时误把 `codebot-backend.exe` 当 Python 执行导致二次启动后端并撞端口的问题 | electron/main.js, backend/config.py, backend/api/routes/hermes.py, backend/api/routes/gateway.py, frontend/src/views/Docs.vue, scripts/install.bat, scripts/install.sh, .env.example, README.md, AGENTS.md |
| 2026-06-11 | 收敛聊天创建定时任务的执行边界：改为使用 AI 结构化分类器判断用户是否真的要创建 Codebot 定时任务；只有分类为创建/添加/设置定时任务、提醒或闹钟时才写入内置调度器或成长候选，普通排错、日志分析和文件处理继续交给 OpenCode/Hermes CLI。定时任务正文会清洗“创建一次性定时任务”等元指令，避免到点执行时再次创建任务 | backend/api/routes/chat.py, backend/core/memory_organizer.py, README.md, AGENTS.md |
| 2026-06-11 | 修复 Hermes 模式聊天提交后无反馈的问题：Hermes 流式分支会先显示 CLI 正在处理，聊天发送状态进入“处理中”，终止操作会杀掉当前 Hermes CLI 进程；定时任务页新增“开启通知”开关，任务进入成长候选时可推送操作提醒 | backend/api/routes/hermes.py, backend/api/routes/chat.py, backend/core/memory_organizer.py, backend/config.py, backend/api/routes/config.py, frontend/src/views/Scheduler.vue, README.md, AGENTS.md |
| 2026-06-11 | 为定时任务新增执行模型：聊天提交定时任务时保存当时主模型为 `execution_model`，任务执行前校验模型是否仍可用，不可用时回退到“记忆 → 自动整理 → 整理使用模型”；定时任务页和成长候选编辑支持重新选择模型，MCP/API 创建任务也可传入执行模型 | backend/core/scheduler.py, backend/api/routes/scheduler.py, backend/api/routes/chat.py, backend/api/routes/growth.py, backend/api/routes/mcp.py, backend/core/memory_organizer.py, backend/database/init_db.py, frontend/src/views/Scheduler.vue, frontend/src/App.vue, README.md, AGENTS.md |
| 2026-06-11 | 修复 Hermes 与 OpenCode 定时任务执行器混用问题：任务表新增并迁移 `executor` 字段，聊天后处理、成长候选、调度器 API、MCP 创建任务和前端任务页都保留/展示/编辑执行器；Hermes 模式提交的 AI 类定时任务到点后调用 Hermes CLI，OpenCode 任务继续走 OpenCode；同时补充“存放到下载文件夹”类输出保存识别 | backend/core/scheduler.py, backend/api/routes/scheduler.py, backend/api/routes/chat.py, backend/api/routes/growth.py, backend/api/routes/mcp.py, backend/core/growth.py, backend/database/init_db.py, frontend/src/views/Scheduler.vue, frontend/src/App.vue, README.md, AGENTS.md |
| 2026-06-11 | 修复 Hermes/Codebot 共享定时任务候选落地：聊天后处理生成任务候选时会同步解析 cron、保留自然语言时间、通知渠道和一次性标记；接受旧任务候选时会从候选证据兜底解析 cron，重复候选会合并更完整 payload，避免“该任务候选缺少明确 cron”阻塞 | backend/api/routes/chat.py, backend/api/routes/growth.py, backend/core/growth.py, README.md, AGENTS.md |
| 2026-06-11 | 补充设置页“文档”入口的用户上手说明：README 现在作为 Codebot 的用户手册，明确 Hermes / Obsidian / `@` / `#` / `/` 的使用方式，并与设置页“文档”标签保持同步 | README.md, AGENTS.md |
| 2026-06-10 | 将 Hermes 对话接入从 Hermes Gateway 改为 Hermes CLI oneshot：聊天页选择 Hermes 后由 Codebot 调用 `hermes -z` 并显示最终回复，不再启动/停止 Hermes Gateway、展示 8765 地址或依赖 Gateway service；Hermes 设置页保留安装、修复、更新、共享配置和 CLI 运行环境状态 | backend/api/routes/hermes.py, backend/main.py, backend/config.py, backend/api/routes/config.py, frontend/src/components/HermesSettings.vue, AGENTS.md |
| 2026-06-10 | 增强 Hermes 一键安装/修复：Codebot 会在 Hermes 安装目录创建专用 `.venv`，自动安装并校验 `openai`、`aiohttp`、`fastapi`、`uvicorn` 等运行依赖；写入 Codebot 管理的 `HERMES_HOME/config.yaml` 与 `.env`，让 Hermes 主模型跟随聊天模型，后台辅助模型跟随记忆整理模型，并通过 Codebot `/v1` 网关调用模型 | backend/api/routes/hermes.py, backend/api/routes/gateway.py, AGENTS.md |
| 2026-06-10 | 修正 Hermes 模式分流语义：聊天选择 Hermes 后改为交给 Hermes 独立处理，而不是继续由 OpenCode 伪装处理；Windows 下 Hermes 命令会按 `.cmd/.bat/.ps1/.py/.js` 类型包装启动，避免 WinError 193；该路径后续已收敛为 CLI oneshot，不再使用 Hermes Gateway | backend/api/routes/hermes.py, backend/api/routes/config.py, backend/config.py, frontend/src/components/HermesSettings.vue, AGENTS.md |
| 2026-05-13 | 修复结构化 question 显示只展示详情、不展示交互面板的问题；结构化事件现在直接渲染选择面板，并可从旧详情文本兜底解析选项 | frontend/src/views/Chat.vue, AGENTS.md |
| 2026-05-13 | 修复聊天中图片附件被拼成 base64 文本灌入对话的问题；图片附件现在仅显示元信息，工具/事件详情中的图片 content 也会脱敏 | backend/api/routes/chat.py, AGENTS.md |
| 2026-05-11 | 成长候选中的任务类型编辑改为结构化表单（任务名/Cron/执行内容/通知渠道/一次性），并修复 Electron 开发态与正式版共用 userData/sessionData 导致的缓存/GPUCache 创建失败问题 | backend/api/routes/growth.py, frontend/src/App.vue, electron/main.js, AGENTS.md |
| 2026-05-11 | 新增“成长候选决策”通用开关；开启后聊天与自动整理生成的记忆/定时任务/技能先进入成长候选，成长候选与活跃记忆均支持编辑；会员更新检查改为对象存储优先、缺失时回退 GitHub Releases | backend/config.py, backend/api/routes/config.py, backend/api/routes/chat.py, backend/api/routes/growth.py, backend/api/routes/memory.py, backend/core/growth.py, backend/core/memory_extractor.py, backend/core/memory_organizer.py, frontend/src/components/GeneralSettings.vue, frontend/src/components/ActiveMemories.vue, frontend/src/App.vue, electron/main.js, AGENTS.md |
| 2026-05-10 | 参考 OpenCode 上游 question dock 改造聊天中的 question 交互：支持多问题/多选/自定义回答的选择面板、统一提交/取消，并修复 answers 保序避免空数组被过滤导致答案错位 | backend/api/routes/chat.py, frontend/src/views/Chat.vue, AGENTS.md |
| 2026-05-10 | 修复过期 `question.asked` 交互残留会拦截普通发送、空 reasoning 片段触发 `list index out of range`、旧 `copilot/GPT-41` 模型 ID 继续提交给 OpenCode 等发送失败问题；仅在对话运行中拦截回答，失败时回退普通发送，并透出 OpenCode `prompt_async` 真实错误 | backend/api/routes/chat.py, backend/core/opencode_ws.py, frontend/src/views/Chat.vue, AGENTS.md |
| 2026-05-10 | 参考 OpenCode 桌面端对 question/permission 的状态化处理，收到 replied/rejected/local_reply 后清理待交互按钮；修复运行中再次发送时流式分支只提示排队但未真正入队、以及排队消息可能重复保存的问题 | backend/api/routes/chat.py, frontend/src/views/Chat.vue, AGENTS.md |
| 2026-05-10 | 修复 OpenCode `question` 工具等待用户选择时 Codebot 无法互动导致任务卡住的问题；接入 `/question/:id/reply`/`reject` 并支持聊天按钮或输入框回答，同时让紧凑模式真实影响聊天布局 | backend/core/opencode_ws.py, backend/api/routes/chat.py, backend/config.py, backend/api/routes/config.py, frontend/src/components/GeneralSettings.vue, frontend/src/views/Chat.vue, AGENTS.md |
| 2026-05-10 | 按 OpenCode 上游 CLI 工具展示规则收敛聊天输出：CLI 模式不再展开 Read/Skill/WebFetch 等工具结果正文；结构化模式只突出工具调用并默认折叠详情 | backend/api/routes/chat.py, frontend/src/views/Chat.vue, AGENTS.md |
| 2026-05-10 | 新增“OpenCode 显示”设置开关；开启后 Codebot 聊天按 OpenCode CLI/桌面端风格直接显示待办、工具调用、权限选择和最终回复，聊天仅作为 OpenCode 输出界面 | backend/config.py, backend/api/routes/config.py, backend/core/opencode_ws.py, backend/api/routes/chat.py, backend/services/notification.py, frontend/src/components/GeneralSettings.vue, frontend/src/views/Chat.vue, AGENTS.md |
| 2026-05-09 | 修复中文后直接跟 `skill` 的创建意图漏识别，增加从 OpenCode 回复路径反查迁移的兜底，并迁移误生成的 `word-to-md` 技能到 Codebot 自动生成目录 | backend/api/routes/chat.py, backend/core/skill_registry.py, skills/auto_word_to_md/SKILL.md, skills/auto_word_to_md/scripts/convert.py, AGENTS.md |
| 2026-05-09 | 将对话和成长候选中的 skill 创建统一收敛为 Codebot 自动生成技能：OpenCode 生成后按快照迁移到 `skills/auto_*`，成长候选接受时按记忆/定时任务/技能分别落库并先提炼真正的 `SKILL.md` 工作流，同时默认读取 OpenClaw/StepClaw 技能目录 | backend/config.py, backend/api/routes/config.py, backend/api/routes/chat.py, backend/api/routes/growth.py, backend/api/routes/mcp.py, backend/api/routes/skills.py, backend/core/growth.py, backend/core/skill_generator.py, backend/core/skill_registry.py, backend/core/memory_organizer.py, frontend/src/components/GeneralSettings.vue, frontend/src/views/Skills.vue, AGENTS.md |
| 2026-05-09 | 修复聊天内创建 skill 的意图识别，确保 Codebot 聊天调用 OpenCode 生成后按归属迁移/复制到 Codebot `skills/auto_*`，同时让聊天默认回复语言真正跟随“通用设置 → 语言”并默认中文 | backend/config.py, backend/api/routes/config.py, backend/api/routes/chat.py, backend/core/skill_registry.py, frontend/src/components/GeneralSettings.vue, AGENTS.md |
| 2026-05-09 | 修复聊天内创建 skill 先落到 OpenCode 目录的问题，改为在保留 OpenCode 生成链路后自动迁移到 Codebot `skills/auto_*`；同时补齐 OpenClaw 兼容 skill 的默认发现与只读管理 | backend/api/routes/chat.py, backend/api/routes/skills.py, backend/core/skill_registry.py, AGENTS.md |
| 2026-05-08 | 修复任务处理流式事件去重过粗导致执行中步骤气泡丢失，恢复任务进行中的实时流式进展展示 | backend/api/routes/chat.py, backend/core/opencode_ws.py, frontend/src/views/Chat.vue, AGENTS.md |
| 2026-05-08 | 修复聊天回复默认语言跟随通用设置、对话生成 skill 统一落到 Codebot 自动生成技能、补充 OpenClaw 兼容技能读取、去重工具调用事件并恢复任务执行中的进展正文流式展示，同时迁移误生成到 OpenCode 目录的财报自媒体合规 skill | backend/config.py, backend/api/routes/config.py, backend/api/routes/chat.py, backend/api/routes/skills.py, backend/core/skill_registry.py, backend/core/opencode_ws.py, frontend/src/components/GeneralSettings.vue, frontend/src/views/Chat.vue, frontend/src/views/Skills.vue, skills/auto_financial_report_media_publisher/SKILL.md, README.md, AGENTS.md |
| 2026-05-05 | 增强“多Agent群聊”串行/并行任务规划、执行过程回显和 hub 终止联动成员对话 | backend/api/routes/chat.py, frontend/src/views/Chat.vue, skills/multi-agent-collaboration/SKILL.md, README.md, AGENTS.md |
| 2026-05-05 | 新增置顶“多Agent群聊”工作台、成员对话角色加入/退出、hub 清空和任务分派汇总，并内置多Agent协作调度技能 | backend/core/memory_manager.py, backend/api/routes/chat.py, frontend/src/views/Chat.vue, skills/multi-agent-collaboration/SKILL.md, README.md, AGENTS.md |
| 2026-05-05 | 支持聊天输入框粘贴截图、局域网只读分享、群聊取消、日志页查看/恢复归档对话，并修复记忆自动整理聊天扫描漏扫和技能沉淀计数 | backend/api/routes/chat.py, backend/core/memory_manager.py, backend/core/memory_organizer.py, frontend/src/views/Chat.vue, frontend/src/views/SharedConversation.vue, frontend/src/views/Logs.vue, frontend/src/router/index.js, README.md, AGENTS.md |
| 2026-05-03 | 将 Release 工作流改为 `main` 自动触发，自动递增 minor 版本并构建 Windows/macOS/Linux 安装包发布到 GitHub Releases | .github/workflows/release.yml, scripts/bump_version.js, electron/package.json, backend/requirements.txt, README.md, AGENTS.md |
| 2026-05-02 | 修复 GitHub 推送失败问题，移除误提交的 `tmp-release-assets/` 大文件并补充忽略规则，避免发布临时产物进入仓库 | .gitignore, AGENTS.md |
| 2026-05-02 | 新增 `.trae/rules/release-update-compat.md`，固化 GitHub Release 自动更新与兼容资产补传规则，供后续智能体复用 | .trae/rules/release-update-compat.md, README.md, AGENTS.md |
| 2026-05-02 | 修复非会员或未登录用户从 GitHub Releases 下载更新时因安装包资产名不一致导致的 404，并统一后续 Windows 安装包命名 | electron/main.js, frontend/src/App.vue, electron/package.json, README.md |
| 2026-05-02 | 统一应用版本到 `3.2.0`，重新打包桌面端并准备发布到 GitHub Releases | backend/config.py, backend/__init__.py, README.md, electron/package.json, frontend/package.json |
| 2026-05-01 | 初始化项目 AI 上下文文档，记录 Codebot 技术栈、结构、核心模块和协作规范 | 全局 |
| 2026-05-01 | 移除 Electron 内置程序小店窗口右下角无响应的“刷新/程序小店”悬浮按钮 | electron/main.js |
| 2026-05-01 | 修复开发模式 OpenCode 端口配置错误，并兼容旧聊天模型 ID 到当前 OpenCode 模型命名 | electron/main.js, backend/core/opencode_ws.py, backend/api/routes/config.py, backend/api/routes/gateway.py, frontend/src/views/Chat.vue |
| 2026-05-01 | 将 `npm start` 开发模式的 OpenCode 自动启动与重连端口统一收敛到 `127.0.0.1:11200`，避免误连旧端口导致聊天发送失败 | electron/main.js, backend/main.py, backend/utils/installer.py, backend/api/routes/chat.py |
