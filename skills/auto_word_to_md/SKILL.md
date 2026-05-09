---
name: "word-to-md"
slug: "auto_word_to_md"
description: "将 Word 文档（.docx）转换为 Markdown（.md）文件。当用户需要把 Word 文档转成 Markdown、说\"帮我把 docx 转成 md\"、\"转换 Word 文件\"、\"word 转 markdown\"、\"把这个文档转为 md\"、\"转换文档格式\"时，必须调用此 skill。即使用户只是说\"把这个 Word 变成 Markdown 格式\"或者"
version: "1.0.0"
source: auto_generated
compatibility:
  - codebot
  - hermes-agent
  - openclaw
created_at: "2026-05-09T11:22:28.815242"
---

# Word → Markdown 转换 Skill

将 `.docx` 文件转换为结构清晰的 Markdown 文件，保留标题层级、列表、表格、粗体/斜体等格式。

## 工作流程

1. **确认输入文件路径** — 让用户提供 `.docx` 文件的完整路径（或从对话上下文中获取）
2. **确认输出位置** — 默认输出到与源文件相同目录，文件名相同但后缀改为 `.md`；如用户指定了输出路径，则用用户指定的
3. **调用转换脚本** — 运行 `scripts/convert.py`
4. **告知结果** — 报告生成的 md 文件路径，并可选地展示前几行内容供预览

## 调用脚本

```bash
python "D:/wenjian/github/codebot/skills/auto_word_to_md/scripts/convert.py" "<输入.docx路径>" "<输出.md路径>"
```

如果用户只给了输入路径，输出路径可省略（脚本会自动推断）：

```bash
python "D:/wenjian/github/codebot/skills/auto_word_to_md/scripts/convert.py" "<输入.docx路径>"
```

## 转换说明

脚本使用 `mammoth` 库将 Word 转为 HTML，再用 `markdownify` 转为 Markdown，保留：
- 标题（H1–H6）
- 加粗 / 斜体
- 有序列表 / 无序列表
- 超链接
- 表格（转为 Markdown 表格）
- 代码块（如有）

图片会被跳过（mammoth 提取图片较复杂），脚本会在 md 文件中留下 `[图片]` 占位符并给出提示。

## 错误处理

- 文件不存在 → 提示用户检查路径
- 文件不是 `.docx` 格式 → 提示仅支持 `.docx`
- 转换中出现警告 → 将 mammoth 的警告信息一并输出给用户参考

## 依赖

需要安装：`mammoth`、`markdownify`（已通过 pip 安装）

```bash
pip install mammoth markdownify
```

