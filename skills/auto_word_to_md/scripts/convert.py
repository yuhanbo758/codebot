#!/usr/bin/env python3
"""
Word (.docx) → Markdown (.md) 转换脚本
用法：
    python convert.py <input.docx> [output.md]
"""

import sys
import os
import re

def convert(input_path: str, output_path: str | None = None) -> str:
    """转换 docx 文件为 md，返回输出路径。"""
    try:
        import mammoth
        from markdownify import markdownify as md
    except ImportError as e:
        print(f"[错误] 缺少依赖：{e}")
        print("请运行：pip install mammoth markdownify")
        sys.exit(1)

    if not os.path.exists(input_path):
        print(f"[错误] 文件不存在：{input_path}")
        sys.exit(1)

    if not input_path.lower().endswith(".docx"):
        print(f"[错误] 仅支持 .docx 格式，当前文件：{input_path}")
        sys.exit(1)

    if output_path is None:
        base = os.path.splitext(input_path)[0]
        output_path = base + ".md"

    print(f"正在转换：{input_path}")

    # mammoth 将图片转为空字符串占位
    def handle_image(image):
        return {"src": "IMAGE_PLACEHOLDER"}

    with open(input_path, "rb") as f:
        result = mammoth.convert_to_html(
            f,
            convert_image=mammoth.images.img_element(handle_image),
        )

    html = result.value
    warnings = result.messages

    # 替换图片占位为 Markdown 图片注释
    html = html.replace('src="IMAGE_PLACEHOLDER"', 'alt="图片"')

    # 转为 Markdown
    markdown_text = md(
        html,
        heading_style="ATX",        # 使用 # 风格标题
        bullets="-",                 # 无序列表用 -
        strip=["script", "style"],
    )

    # 清理多余空行（超过 2 个连续空行压缩为 2 个）
    markdown_text = re.sub(r'\n{3,}', '\n\n', markdown_text)
    markdown_text = markdown_text.strip() + "\n"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(markdown_text)

    print(f"转换完成：{output_path}")

    if warnings:
        print("\n[转换警告]")
        for w in warnings:
            print(f"  - {w.message}")

    return output_path


def main():
    if len(sys.argv) < 2:
        print("用法：python convert.py <input.docx> [output.md]")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) >= 3 else None

    out = convert(input_path, output_path)

    # 预览前 10 行
    with open(out, "r", encoding="utf-8") as f:
        lines = f.readlines()

    print("\n--- 预览（前10行）---")
    for line in lines[:10]:
        print(line, end="")
    if len(lines) > 10:
        print(f"\n... 共 {len(lines)} 行")


if __name__ == "__main__":
    main()
