# Codebot

基于 OpenCode 的第三方能力工作台 - 所有聊天统一由 OpenCode 处理，Codebot 负责提供 MCP / Skills / 记忆 / 定时任务能力并展示结果

## ✨ 特性

- 🤖 **OpenCode 主控**: 所有聊天消息统一交给 OpenCode 处理，支持多模型切换与原生工具流式事件展示
- 🔗 **Codebot 第三方化**: Codebot 自身以第三方 MCP 形式注册到 OpenCode，OpenCode 可直接调用 Codebot 的记忆、任务、技能与会话工具
- 🧭 **自主执行策略**: 默认优先自主决策与自动重试，减少把流程决策抛给用户
- 💾 **记忆系统**: SQLite + ChromaDB 持久化存储，支持上下文记忆和长期记忆
- 🧠 **自动记忆提取**: 从对话中自动识别并保存用户的习惯、偏好、个人信息等，无需手动触发
- 💡 **记忆提示**: 聊天输入时实时显示相关记忆提示，让 AI 回复更贴合用户背景
- ⏰ **定时任务**: 完整 Cron 表达式支持，AI 辅助创建任务
- 🔔 **通知系统**: 飞书/邮箱/应用内/系统桌面通知，用户可配置
- 📂 **记忆管理**: 归档查看、导出导入、自动清理、自动整理，支持按类别过滤
- 📝 **日志系统**: 详细执行日志，可配置保留期限
- 🌐 **局域网访问**: 支持通过 IP 地址远程访问
- 📱 **移动端适配**: 响应式设计，支持手机浏览器
- 🖥️ **跨平台**: Electron 桌面应用 + Web 应用
- 🛠️ **第三方技能系统**: `skills/` 中的 SKILL.md 会同步到 OpenCode 的技能目录，作为第三方技能直接被调用
- 🔌 **第三方 MCP 支持**: Codebot 会聚合并代理外部 MCP 服务器（尤其是魔搭 ModelScope MCP），再通过自身 MCP SSE 入口统一暴露给 OpenCode 调用
- 🏖️ **沙箱执行**: 工作目录隔离执行环境，AI 生成的代码在独立 `sandbox_workspace/` 目录中运行，无需 QEMU/Docker，开箱即用

## 🚀 快速开始

### 系统要求

- Python 3.11+
- Node.js 18+
- OpenCode CLI
  - `opencode serve` 默认端口 4096（<http://127.0.0.1:4096）>
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
opencode serve --port 4096 --hostname 127.0.0.1
```

应用程序配置文件（设置 → 通用设置 → 配置文件 → `config.json`）里的 `server_url` 需要与 `opencode serve` 保持一致（端口/地址一致）。如果连不上，请优先检查该配置项。桌面端启动后端时会自动尝试拉起 OpenCode 服务，端口优先级为 `127.0.0.1:1120` → 配置端口 → `127.0.0.1:4096`。若 `1120` 被其他进程占用，会直接回退到后续候选端口，不会改用 `1121/1122` 这类随机端口。这样可优先为 Codebot 单独使用 1120，不干扰已有的 `4096` 服务。
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
- 添加参数填：`serve --port 4096 --hostname 127.0.0.1`
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
Electron 会优先使用应用内置的 `opencode` 可执行文件（`electron/vendor/opencode` 或打包后的 `resources/opencode`）自动拉起 `opencode serve`；若内置文件不可用或不可执行，会自动回退到系统 PATH 中的 `opencode`。桌面端会强制开启 OpenCode 自动拉起，并优先尝试 1120（回退配置端口与 4096）。

### Windows 沙箱现状

- 沙箱已重构为**工作目录隔离**模式，移除 QEMU 依赖，开箱即用，无需安装任何额外软件
- AI 生成的代码在独立的 `data/sandbox/workspace/` 目录中执行，通过 `asyncio` 子进程运行，带超时控制
- 执行结果实时返回，支持 `stdout`/`stderr`/`exit_code` 完整输出
- 旧版 QEMU 相关端点（`/install-qemu`、`/start`、`/stop`）保留但返回本地模式说明，保持 API 向后兼容

### 访问

- **本地访问**: <http://127.0.0.1:8080>
- **局域网访问**: http\://<你的 IP>:8080
- **移动端**: 使用手机浏览器访问局域网地址

OpenCode Server 默认地址：<http://127.0.0.1:4096（由> `opencode serve` 提供 OpenAPI）
`server_url` 建议填写 OpenCode Server 的 HTTP 地址（例如 `http://127.0.0.1:4096`），后端会自动规范化为可访问的 HTTP 基础地址。连不上的话请优先检查 `config.json` 的 `server_url` 是否与 `opencode serve` 一致。
如果 OpenCode 未连接，不会在应用启动时弹窗打扰；当你在聊天中创建定时任务/保存记忆时，会优先使用本地规则解析并直接落库到“定时任务/记忆”，仅在解析失败且 OpenCode 可用时，才会通过 OpenCode HTTP API 请求获取结构化结果。意图识别阶段要求 OpenCode 输出结构化 JSON，若输出不符合格式会自动重试一次。后端会将 OpenCode Server 输出写入 `logs/opencode_server.*.log` 便于排查。
提醒类定时任务在 OpenCode 未连接时也会按计划执行并发送应用内通知；创建任务也不依赖 OpenCode（支持 `20:05` 与 `20：05`）。

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
│   └── xlsx/SKILL.md           # Excel 技能
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
- **分组聊天**: 支持将多个对话合并为群组模式
- **对话分享**: 生成分享链接（`share_id`），可供他人只读查看
- 支持在聊天中创建定时任务（如"每天8点写一个故事并保存到D盘""每天8:10提醒我喝水"，可在定时任务中查看）
- 支持在聊天中保存记忆（如"帮我记住 广东揭阳普宁船埔 这个地址""10月2日是姐姐的生日"，可在记忆中查看）
- **意图分类**: 消息自动分类为"定时任务/保存记忆/普通对话"，避免误判
- **Agent 模式**: 支持 `plan`（结构化规划）和 `build`（直接执行）两种模式
- 消息一键复制（Electron 使用系统剪贴板）
- 支持文件附件上传；多模态模型支持图片分析
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

### 1.1 Codebot 作为第三方的工作方式

- OpenCode 是主聊天入口，负责模型选择、推理、工具决策与最终回答
- Codebot 通过 `/api/mcp/codebot/sse` 暴露第三方 MCP，向 OpenCode 提供记忆、任务、技能、会话等工具
- Codebot 会把“第三方 MCP”页面中启用的远程 MCP 工具代理成 `codebot_mcp__...` 形式的工具名，再暴露给 OpenCode
- Codebot 会把 `skills/` 目录中的技能同步到 OpenCode 技能目录，使其作为第三方技能被直接调用
- 聊天请求发往 OpenCode 时只附带必要的用户记忆上下文，不再由 Codebot 预判技能或代替 OpenCode 做二次工具编排
- `backend/core/tool_dispatcher.py` 已收敛为桥接辅助模块，仅负责技能发现与 MCP 协议适配，不再承担聊天主链路上的工具调度
- 前端聊天页只展示 OpenCode 的流式步骤与最终结果，并提示当前第三方桥接状态

### 2. 记忆系统

- **上下文记忆**: 自动保存对话历史
- **长期记忆**: 保存用户习惯、偏好、事实信息（用于之后对话检索问答）
- **自动提取**: 每次对话后，后台自动进行规则+AI双通道提取，识别重要信息并保存，无需依赖“记住”关键词
- **记忆类别**: `habit`（习惯）、`preference`（偏好）、`profile`（个人信息）、`note`（笔记）、`contact`（联系人）、`address`（地址）
- **聊天记忆分类补充**: 聊天中手动“记住”时会优先识别生日/姓名/职业/账号密码等个人信息关键词，自动归类到 `profile`
- **记忆提示**: 聊天输入时自动检索相关记忆并在输入框上方显示提示气泡，AI 回复时也会注明"根据我的记忆"
- **记忆搜索**: 语义搜索相关记忆
- **记忆归档**: 自动或手动归档旧记忆，支持按类别过滤查看
- **事实同步**: 打开活跃记忆列表时，会将生日类事实记忆自动补齐到长期记忆，避免“有事实但列表为空”
- **删除联动**: 删除带 `memory_key`/`fact_key` 的长期记忆时会同步归档对应事实，防止生日等条目被自动补回
- **存储诊断**: 提供 `/api/memory/storage-status`，可直接查看当前数据库路径与表计数
- **一键自检**: 活跃记忆页支持“一键自检”，会检测数据库路径、关键表计数和接口读链路状态
- **备份恢复**: 导出记忆为 ZIP 文件（保存至 `data/backups/`），或上传备份文件恢复（导入前自动备份当前数据）
- **记忆整理**: 每日在配置时间点（默认 03:00）自动用 AI 对活跃记忆进行优化，也可在"活跃记忆"页手动触发
- **聊天整理联动**: 自动整理时会扫描新增聊天记录，从聊天中补充记忆，并尝试沉淀相关定时任务与可复用技能
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
- **执行日志**: 详细的任务执行记录
- **提醒任务**: 带 `__REMINDER__` 标志的纯提醒任务不依赖 OpenCode 也能按计划触发通知；AI 类任务（生成内容/写文件等）需要 OpenCode 在线执行
- **像聊天一样执行**: 定时任务到达执行时间时，系统会像聊天一样通过 OpenCode CLI 处理任务内容，充分利用 AI 的代码生成与文件写入能力
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

### 5. 技能系统

- **内置技能**: `web_search`（网页搜索）、`web_fetch`（抓取网页）、`news`（新闻获取）、`file_reader`（文件读取）、`pdf`（PDF 处理）、`docx`（Word 文档）、`pptx`（PowerPoint，含缩略图脚本）、`xlsx`（Excel，含重算脚本）
- **技能定义**: Markdown 文件（`SKILL.md`）带 YAML front-matter（`name`、`description`），自动匹配用户提示
- **自动调度**: `tool_dispatcher.py` 通过关键词 + 语义匹配，将 `SKILL.md` 内容注入到对应请求的提示词中
- **低干扰注入**: 仅在高相关度下启用技能上下文，降低无关技能误触发
- **内部上下文隔离**: 技能与 MCP 上下文仅用于内部推理，不向用户直接展示“技能参考”等标签
- **自动沉淀技能**: 对高复用的已完成任务，自动生成可复用技能元数据，便于后续任务快速命中
- **对话生成技能**: 本地规则生成，不调用 OpenCode，写入 `codebot/skills`
- **OpenCode 本地技能**: 自动读取 `~/.agents/skills`，可在技能页卸载
- **自定义目录技能**: 支持配置多个外部文件夹路径，自动扫描其中包含 `SKILL.md` 的子目录并加载为只读技能，方便复用其他工具的技能文件
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

如果直接访问或刷新 `/memory/active`、`/skills` 等前端路由地址，请确保通过后端服务地址访问（如 `http://127.0.0.1:8080`）。
当前版本后端已支持 SPA History 路由回退，刷新不会再丢失页面。

### 启动时报 sqlite3.OperationalError: Cannot add a column with non-constant default

这是 SQLite 的限制：`ALTER TABLE ... ADD COLUMN` 不能使用 `CURRENT_TIMESTAMP` 这类“非固定常量”的默认值。
当前版本已在迁移逻辑中兼容旧库：新增 `updated_at` 列时不设置默认值，并对历史数据进行回填；升级后再次启动即可恢复正常。

### 启动时报 PermissionError / WinError 32: chroma.sqlite3 被占用

这通常是因为上一次的后端/Electron 进程未完全退出，导致 `data/chroma/chroma.sqlite3` 被占用。

- 先确保所有 Codebot 相关进程已退出（关闭终端、退出 Electron）
- 重新启动后端
- 如仍失败，可手动将 `data/chroma` 重命名为 `data/chroma_backup_manual` 后再启动（向量索引会自动重建）

### 启动时报 WinError 10048: 端口 8080 被占用

这表示你在同一台机器上启动了多个 Codebot 后端实例（或其它程序占用了配置端口）。

- 用 PowerShell 查找占用进程：`netstat -ano | findstr :8080`
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

### 记忆整理

记忆整理通过 AI 对长期记忆进行批量优化：

- **合并重复**：相似或互相包含的条目合并为一条
- **补全描述**：过于简短的内容根据语义适当补全
- **标准化格式**：统一表达方式，去除冗词
- **修正矛盾**：同类别中互相矛盾的条目，保留较新的，旧的归档
- **聊天补全**：额外扫描新增聊天消息，补提取高价值信息到长期记忆
- **任务/技能联动**：当聊天内容具备任务或技能线索时，自动触发任务创建与技能沉淀流程

无 OpenCode 连接时降级为规则模式（仅去重完全相同的条目）。每批最多处理 30 条，避免超出模型上下文窗口。活跃记忆永远不会被整理删除，只会被归档或更新内容。

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

- 日志保留天数：默认 30 天（0=永久）
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

说明：`npm start` 会先构建前端（`frontend/dist`），再启动 Electron。

补充说明：

- Electron 启动时会先检查 `http://127.0.0.1:8080/api/health`，若后端已在运行则复用现有实例，不会重复拉起后端进程。
- 若检测到已有后端运行但 `opencode_connected=false`，Electron 会自动重启后端以重新拉起 `opencode serve`。
- 开发模式下 `npm start` 始终使用 `venv\Scripts\python.exe backend\main.py` 启动后端，确保运行的是当前源码。
- 开发模式若检测到 8080 端口为非源码后端（`runtime_source != source`），会先终止该进程再拉起源码后端，避免看不到最新流式改动。
- 桌面端打包默认读取 `backend/dist_build/codebot-backend` 作为后端资源目录。
- Windows 下建议使用根目录 `build.bat` 执行完整打包流程（后端 PyInstaller + 前端构建 + Electron 安装包）。
- Windows 打包产物默认输出到 `electron/dist/electron_new/`。
- Windows 产物会同时生成安装版（`Codebot Setup 1.0.0.exe`）和免安装版（`Codebot 1.0.0.exe`，portable）。
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
- `POST /api/memory/memories` - 创建记忆
- `GET /api/memory/memories/search` - 搜索记忆
- `GET /api/memory/hints` - 根据当前输入返回相关记忆提示（供前端实时展示）
- `POST /api/memory/export` - 导出记忆为 ZIP 文件（保存至服务端 `data/backups/`）
- `POST /api/memory/import` - 上传 ZIP 备份文件并恢复（multipart/form-data）
- `POST /api/memory/cleanup` - 手动触发清理（支持 `cleanup_archived_memories` 参数）
- `POST /api/memory/organize` - 手动触发记忆整理（后台异步执行）
- `GET /api/memory/organize/status` - 查询整理任务状态及上次运行时间

### 定时任务 API

- `GET /api/scheduler/tasks` - 获取任务列表
- `POST /api/scheduler/tasks` - 创建任务
- `PUT /api/scheduler/tasks/{id}` - 更新任务
- `DELETE /api/scheduler/tasks/{id}` - 删除任务
- `POST /api/scheduler/ai-generate` - AI 生成 Cron 表达式

### 技能 API

- `GET /api/skills` - 获取技能列表
- `POST /api/skills` - 创建技能
- `DELETE /api/skills/{name}` - 删除技能

### MCP API

- `GET /api/mcp/servers` - 获取 MCP 服务器列表
- `POST /api/mcp/servers` - 添加 MCP 服务器
- `PUT /api/mcp/servers/{id}` - 更新 MCP 服务器配置
- `DELETE /api/mcp/servers/{id}` - 删除 MCP 服务器

### 通知 API

- `GET /api/notifications` - 获取通知列表
- `POST /api/notifications/{id}/read` - 标记通知已读
- `POST /api/notifications/read-all` - 全部标记已读

### 沙箱 API

- `GET /api/sandbox/status` - 获取沙箱状态
- `POST /api/sandbox/start` - 启动沙箱 VM
- `POST /api/sandbox/stop` - 停止沙箱 VM
- `POST /api/sandbox/execute` - 在沙箱中执行命令

### 日志 API

- `GET /api/logs` - 获取执行日志列表
- `DELETE /api/logs/cleanup` - 清理过期日志

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
