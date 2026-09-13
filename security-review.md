# Codebot Agent 提示词隐私与消耗检查

日期：2026-09-13。范围：Codebot 聊天提示组装、OpenCode 请求路由、Codex thread 恢复、聊天日志读写。未做全产品渗透测试、真实模型提示注入攻击或真实账单对比。

## 发现与修复

| 严重程度 | 位置 | 证据与影响 | 处理 |
| --- | --- | --- | --- |
| 中：隐私暴露面 | backend/api/routes/chat.py:1346；backend/api/routes/logs.py:157 | 原日志写入完整 internal_prompt，列表及详情 SELECT cl.* 返回正文，其中可能含私人记忆、技能正文和路径。日志路由有应用鉴权，不能据此断言发生未授权外传。 | 新记录不保存内部提示正文；列表、按会话过滤列表及详情用显式投影屏蔽旧正文。旧数据库和备份未删除。 |
| 中：上下文膨胀 | backend/api/routes/chat.py:3276；backend/core/prompt_optimizer.py:101 | 原来只有条数上限，无字符预算；事实、profile 及兜底长期记忆桶可重复纳入相同内容。 | 全局去重，补充记忆总量上限 4000 字符，单条上限 800；用户原文不受此预算裁剪。 |
| 中：路由不确定 | backend/core/opencode_ws.py:155 | 原来只有 plan/build 设置 agent 字段，Agent/Editor 遗漏后依赖上游默认代理。若默认代理是 plan 或自定义代理，执行意图可能不匹配。 | Agent/Editor 显式映射 build；Plan 保持 plan。模拟请求验证，不声称当前安装实例已复现该上游配置。 |
| 低：重复行为指导 | backend/api/routes/chat.py:3056；backend/core/prompt_optimizer.py:58 | Agent 基础规则、复杂任务契约与可选技能索引重复表达规划、验证、协作、沉淀要求。 | 精简复杂契约，移除每轮重复索引，明确验证完成即结束、出现新证据或失败才扩展检查。显式技能选择仍保留。 |
| 低：记忆信任边界 | backend/core/prompt_optimizer.py:101 | 原记忆标题写“可信，优先使用”，会诱导模型将检索资料当作高优先级规则。 | 明确资料可能过时、内部命令不构成指令。这是提示层缓解，不是提示注入免疫保证。 |

## 确认未发现的问题与局限

- OpenCode 主聊天把 system 与用户 parts 分开；Codex 使用 developerInstructions；内部 internal_prompt 事件在聊天流聚合处被消费，不直接推送为用户可见聊天事件。不能把角色分离称为彻底防止模型复述系统提示。
- Codex 已有 thread 恢复成功时不重新注入数据库历史，只有新建/失效恢复线程才装载恢复历史；本次未改其恢复逻辑。
- 提示词优化器是本地规则判断，没有额外模型改写调用。没有证据证明它自身增加一次计费请求。
- 4000/800 是补充记忆的字符上限，不是完整请求或模型 token 上限。长记忆可能被截断；关键细节仍需按需查询记忆工具。历史会话和工具输出、上游执行器自身提示、模型推理 token 尚未做逐任务账单测量。
- 不通过正则删除正常回答来假装防泄露。已注入模型的文字不能作为秘密保险箱；本次没有进行真实模型攻击测试。

## 离线字符对比

固定请求：`请检查 a.py 并修复问题，运行测试，保持原接口不变。`
基线取自修改前 Git HEAD 中的提示构造器与优化器，配置及检索使用相同隔离环境；仅计算 Codebot system 字符数。

| 样例 | 修改前 | 修改后 | 减少 |
| --- | ---: | ---: | ---: |
| 无记忆 | 665 | 589 | 11.4% |
| 相同 8000 字符记忆同时命中事实与 profile | 32783 | 1459 | 95.5% |

第二项是边界样例，不代表普通任务平均节省 95.5% token。

## 验证与交付

- 后端完整 unittest：113 项通过，包含新增 4 项隐私、预算和路由测试。
- 最后精简执行契约后，新增 4 项回归再次通过。
- `git diff --check` 通过。
- 使用临时数据库、模拟记忆和模拟上游；未调用付费模型，未改写真实聊天库。
- 源码分支：`codex/agent-prompt-privacy-budget`；未提交、推送、打包或重启已安装应用。
- 源码后端重启后生效；已安装 exe 需重新打包/更新。旧会话中上游已保存的上下文不会被本补丁清除，评估新提示建议使用新会话。
- 回滚可恢复本次涉及的源码文件；不涉及数据库结构迁移、配置结构或依赖版本调整。


---

# 历史审查记录（保留原文）

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
   首次 CI 门禁实际发现旧版 FastAPI/Starlette、python-multipart、python-dotenv，以及未使用的 aiosmtplib、python-jose/ecdsa 等依赖漏洞。现升级实际运行依赖，删除源码未导入的认证和异步邮件死依赖及对应 PyInstaller hidden imports；CI 改为直接审计 `backend/requirements.txt`，不再把 pip-audit 自身环境中的 setuptools 等包误算为应用运行时依赖。2026-09-11 新增的 `PYSEC-2026-3813`、`PYSEC-2026-3814` 覆盖 ChromaDB 当前全部可用版本且上游尚无修复版；Codebot 只使用本地进程内 `PersistentClient`，不启动或连接 Chroma HTTP 服务，因此流水线在源码边界检查通过后精确豁免这两个公告。若后续出现 `HttpClient`、Chroma 服务端命令或 v2 HTTP API 路径，门禁会失败并要求重新评估；其他新漏洞仍保持阻断。同轮 Electron 审计发现的 `@xmldom/xmldom`、`fast-uri`、`js-yaml` 高危漏洞均已有修复，已更新锁文件而未做豁免；CI Node 运行时同步升级到 Electron 43 所需的 Node 22，并使用当前 `setup-python` Action，避免旧 Node 运行时兼容警告。

## 验证记录

- `python -m unittest discover -s backend/tests -p "test_*.py" -v`：15 项通过。
- `python -m compileall -q backend`：通过。
- `frontend npm run build`：通过（Vite 8.2.1，1822 modules）。
- `frontend npm audit`：0 vulnerabilities。
- `electron npm audit`：0 vulnerabilities。
- `node --check electron/main.js`、`node --check electron/preload.js`：通过。
- `electron-builder --dir --win`：通过，成功生成 Windows x64 目录包。
- Python `pip-audit --requirement backend/requirements.txt`：2026-09-11 在项目虚拟环境复验为 `No known vulnerabilities found, 4 ignored`，四条命中对应上述两个公告的重复别名记录；最终发布结果同时以对应 GitHub Actions 为准。
