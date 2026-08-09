# Codebot 安全审查报告

审查日期：2026-08-09
范围：Python/FastAPI 后端、Vue 前端、Electron 主进程、依赖清单与主要文件/命令执行边界；不包含 `vscode-extension/`。

## 结论

本轮共修复 1 个严重、4 个高危和 5 个中危问题。前端与 Electron 的 `npm audit` 生产依赖结果均为 0；后端回归测试和 Electron Windows 目录打包通过。源码扫描未发现应提交的硬编码生产密钥，配置读取接口中的本机密钥已统一脱敏。

后续补充修复了原审查中的三项风险：局域网 API 增加一次性配对码、HttpOnly 会话与 Bearer Token；命令执行增加 Windows Sandbox 虚拟化隔离，并将其设为默认不探测、不启动的显式可选后端；发布流水线增加 Python 3.11 `pip-audit` 超时门禁。Windows Sandbox 尚不能承载完整 OpenCode Agent，CI 审计也必须以实际流水线成功结果为准。

## 已修复问题

### 严重

1. **任意来源下载可触发技能安装，并可能解压恶意归档**
   文件：`electron/main.js:196`、`electron/main.js:388`
   原逻辑只要“来源可信”或“扩展名受支持”满足一个条件就会进入安装链，普通网页提供 `.zip` 即可能触发；归档解压前也未检查路径穿越和链接条目。现改为来源域名与文件类型必须同时通过，且在解压前校验文件数、绝对路径、盘符、`..`、NUL、符号链接和硬链接。

### 高危

1. **Electron 与构建链存在已知高危依赖**
   文件：`electron/package.json:13`、`electron/package.json:14`
   Electron 28 与旧 electron-builder 依赖树的审计结果包含 1 个严重和 8 个高危项。现升级到 Electron 43.3.0 与 electron-builder 26.15.3，`npm audit` 为 0，Windows 目录打包通过。

2. **浏览器可跨站调用具有文件/命令能力的本机 API**
   文件：`backend/main.py:446`、`backend/main.py:468`
   原 CORS 通配符会扩大恶意网页调用本机/LAN API 的风险。现仅允许本机开发前端、显式环境变量白名单和同源请求，并增加防嵌入、禁缓存、禁 MIME 猜测等响应头。

3. **文件白名单使用字符串前缀，可被同名前缀兄弟目录绕过**
   文件：`backend/api/routes/chat.py:4585`
   例如允许 `D:\\workspace\\project` 时，旧判断可能错误接受 `D:\\workspace\\project-secret`。现使用解析后路径的父子关系判断，并新增回归测试。

4. **配置 API 明文回传已保存的 Token、Secret 和密码**
   文件：`backend/utils/secrets.py:9`、`backend/api/routes/mcp.py:160`、`backend/api/routes/config.py:123`、`backend/api/routes/lark.py:30`、`backend/api/routes/notifications.py:112`
   现递归返回固定掩码；前端把掩码原样提交时保留旧值，显式空字符串仍可清空。飞书敏感输入同时改为密码框。

### 中危

1. **聊天附件、文件读取和排队任务缺少资源上限**
   文件：`backend/api/routes/chat.py:247`、`backend/api/routes/chat.py:4287`、`backend/api/routes/chat.py:4672`
   现限制单文件 20 MB、提取文本 200 万字符、每轮 10 个附件/总内容 5000 万字符，以及每个对话最多 20 个排队任务，超限返回 413 或 429。

2. **后台协程缺少强引用和统一异常回收**
   文件：`backend/utils/background_tasks.py:1`、`backend/main.py:237`
   现由统一任务注册表保存强引用、消费异常并在应用退出时取消，降低任务静默丢失和关闭残留风险。

3. **Electron 外链协议和网页权限边界过宽**
   文件：`electron/main.js:382`、`electron/main.js:408`、`electron/main.js:422`
   现拒绝内置浏览器权限请求，只允许受控协议外链，内部地址使用规范化 origin 精确匹配，并明确启用 renderer sandbox。

4. **原“沙箱”名称与实际安全能力不符，并继承宿主凭据环境变量**
   文件：`backend/core/sandbox/manager.py`、`backend/config.py`、`frontend/src/components/SandboxSettings.vue`
   本地命令继续清除常见 Token、Secret、Password 等凭据变量；强隔离链路已改为一次性 Windows Sandbox，默认禁网并限制映射目录。该后端为显式可选项，默认不探测、不启动；需要隔离但后端未配置或不可用时严格失败关闭。兼容字段 `runtime_binary` 只接受文件名为 `WindowsSandbox.exe` 的运行时，不能借此启动任意宿主机程序。

5. **前端构建依赖存在开发服务器漏洞**
   文件：`frontend/package.json:20`、`frontend/package.json:21`
   Vite 与 Vue 插件已升级到 Vite 8.2.1 / plugin-vue 6.0.8；构建通过且完整 `npm audit` 为 0。

## 尚存风险与建议

1. **中危（架构）— 完整 Agent 尚不能进入强隔离后端**
   Windows Sandbox 已能隔离独立命令，但当前 OpenCode Agent 仍由宿主服务运行。聊天链路在要求 Agent 强隔离时会失败关闭；后续若要执行完整的不可信 Agent，应实现 Sandbox 内代理进程和受控 IPC，而不是解除该保护。

2. **中危 — 自动更新/发布产物完整性依赖发布渠道**
   当前 Electron 更新链主要依赖 GitHub Release/HTTPS，仓库未配置稳定的 Windows 代码签名证书和应用层独立摘要校验。建议为正式安装包配置代码签名，并在自定义下载路径校验服务端签名或固定摘要。

3. **已修复 — Python 运行时依赖 CVE**
   首次 CI 门禁实际发现旧版 FastAPI/Starlette、python-multipart、python-dotenv，以及未使用的 aiosmtplib、python-jose/ecdsa 等依赖漏洞。现升级实际运行依赖，删除源码未导入的认证和异步邮件死依赖及对应 PyInstaller hidden imports；CI 改为直接审计 `backend/requirements.txt`，不再把 pip-audit 自身环境中的 setuptools 等包误算为应用运行时依赖。

## 验证记录

- `python -m unittest discover -s backend/tests -p "test_*.py" -v`：15 项通过。
- `python -m compileall -q backend`：通过。
- `frontend npm run build`：通过（Vite 8.2.1，1822 modules）。
- `frontend npm audit`：0 vulnerabilities。
- `electron npm audit`：0 vulnerabilities。
- `node --check electron/main.js`、`node --check electron/preload.js`：通过。
- `electron-builder --dir --win`：通过，成功生成 Windows x64 目录包。
- Python `pip-audit --requirement backend/requirements.txt`：修复后在项目虚拟环境复验为 `No known vulnerabilities found`；最终发布结果同时以对应 GitHub Actions 为准。
