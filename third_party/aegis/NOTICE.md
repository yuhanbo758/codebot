# Aegis 方法适配来源

- 上游项目：[GanyuanRan/Aegis](https://github.com/GanyuanRan/Aegis)
- 参考提交：`d4942b96fb5d9a96b71ed27030f9eaaf4285481b`
- 核对日期：2026-10-17
- 许可证：MIT，完整条款见同目录 `LICENSE`；保留 Jesse Vincent 与 Ganyuan Ran 的版权声明。
- Codebot 实现：`backend/core/prompt_optimizer.py`，由共享聊天提示词构建链调用。

参考以下固定版本的方法，以中文精简适配到 Codebot：

- [项目上下文](https://github.com/GanyuanRan/Aegis/blob/d4942b96fb5d9a96b71ed27030f9eaaf4285481b/skills/establishing-project-context/SKILL.md)：只读取相关上下文，区分现有事实和未决问题。
- [系统化调试](https://github.com/GanyuanRan/Aegis/blob/d4942b96fb5d9a96b71ed27030f9eaaf4285481b/skills/systematic-debugging/SKILL.md)：复现、定位责任模块、逐项证伪与最小充分修复。
- [代码审查](https://github.com/GanyuanRan/Aegis/blob/d4942b96fb5d9a96b71ed27030f9eaaf4285481b/skills/requesting-code-review/SKILL.md)：先给有证据的问题与影响，审查意见不等同于合并授权。
- [完成前验证](https://github.com/GanyuanRan/Aegis/blob/d4942b96fb5d9a96b71ed27030f9eaaf4285481b/skills/verification-before-completion/SKILL.md)：以新鲜且覆盖声明范围的证据报告结果。
- [复杂度治理](https://github.com/GanyuanRan/Aegis/blob/d4942b96fb5d9a96b71ed27030f9eaaf4285481b/skills/using-aegis/references/complexity-governance.md)：优先正确的责任归属，控制重复逻辑、兜底和依赖增长。
- [简洁沟通](https://github.com/GanyuanRan/Aegis/blob/d4942b96fb5d9a96b71ed27030f9eaaf4285481b/skills/communicating-concisely/SKILL.md)：减少冗余并保留技术准确性。Codebot 保留自然完整句子和有依据的不确定性，不默认采用原文的碎片化表达。

这是 Codebot 的方法层适配，不是完整 Aegis 安装或上游官方宿主适配。没有打包或运行其安装器、自动更新、hooks、工作区状态机、自动提交或多代理编排。运行时、权限、项目文件访问和完成状态仍由原有执行器与 Codebot 管理。上游 benchmark 不代表 Codebot 的实测效果。

分发时请同时保留本文件与 `LICENSE`。后端 PyInstaller 和 Electron 的资源配置均包含这两份文件；后端产物门禁检查其存在性。
