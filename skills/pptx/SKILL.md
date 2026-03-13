---
name: pptx
description: "只要涉及 .pptx 文件——无论是输入、输出还是两者兼有——都使用此技能。包括：创建幻灯片、演示文稿或 pitch deck；读取、解析或提取任意 .pptx 文件中的文本（即使提取的内容将用于邮件或摘要等其他用途）；编辑、修改或更新现有演示文稿；合并或拆分幻灯片文件；处理模板、版式、演讲者备注或批注。只要用户提到"幻灯片"、"演示"、"pptx"或引用 .pptx 文件名，无论后续如何使用内容，都应触发此技能。"
---

# PPTX Skill - PowerPoint Presentation Processing

## Overview

Use `python-pptx` library for creating and editing PowerPoint presentations.

## Quick Reference

| Task | Approach |
|------|----------|
| Read/analyze content | `python-pptx` or `markitdown` |
| Create from scratch | `python-pptx` |
| Edit existing | `python-pptx` load and save |

## Reading Content

```python
from pptx import Presentation

prs = Presentation('presentation.pptx')

# Iterate slides
for slide_num, slide in enumerate(prs.slides, 1):
    print(f"=== Slide {slide_num} ===")
    for shape in slide.shapes:
        if shape.has_text_frame:
            for para in shape.text_frame.paragraphs:
                print(para.text)
        if shape.has_table:
            table = shape.table
            for row in table.rows:
                for cell in row.cells:
                    print(cell.text)
```

```bash
# Text extraction via markitdown (if installed)
python -m markitdown presentation.pptx
```

## Creating New Presentations

```python
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

prs = Presentation()

# Set slide size (widescreen 16:9)
prs.slide_width = Inches(13.33)
prs.slide_height = Inches(7.5)

# Use blank layout
blank_layout = prs.slide_layouts[6]
slide = prs.slides.add_slide(blank_layout)

# Add title text box
title_box = slide.shapes.add_textbox(
    Inches(0.5), Inches(0.5), Inches(12), Inches(1.5)
)
tf = title_box.text_frame
tf.word_wrap = True
para = tf.paragraphs[0]
para.text = "Presentation Title"
para.font.size = Pt(40)
para.font.bold = True
para.font.color.rgb = RGBColor(0x1F, 0x49, 0x7D)
para.alignment = PP_ALIGN.CENTER

# Add content text box
content_box = slide.shapes.add_textbox(
    Inches(0.5), Inches(2), Inches(12), Inches(5)
)
tf = content_box.text_frame
tf.word_wrap = True

# Add bullet points
bullets = ['Point 1', 'Point 2', 'Point 3']
for i, bullet in enumerate(bullets):
    if i == 0:
        para = tf.paragraphs[0]
    else:
        para = tf.add_paragraph()
    para.text = bullet
    para.font.size = Pt(20)
    para.level = 0

prs.save('output.pptx')
```

## Adding Images

```python
from pptx import Presentation
from pptx.util import Inches

prs = Presentation()
slide_layout = prs.slide_layouts[6]
slide = prs.slides.add_slide(slide_layout)

# Add image
pic = slide.shapes.add_picture(
    'image.png', 
    Inches(1), Inches(1),   # left, top
    Inches(4), Inches(3)    # width, height
)

prs.save('with_image.pptx')
```

## Adding Tables

```python
from pptx import Presentation
from pptx.util import Inches, Pt

prs = Presentation()
slide = prs.slides.add_slide(prs.slide_layouts[6])

# Add table: rows, cols, left, top, width, height
rows, cols = 3, 3
table = slide.shapes.add_table(
    rows, cols,
    Inches(1), Inches(1),
    Inches(8), Inches(3)
).table

# Set column widths
for col_idx in range(cols):
    table.columns[col_idx].width = Inches(8/cols)

# Add headers
headers = ['Name', 'Value', 'Status']
for col_idx, header in enumerate(headers):
    cell = table.cell(0, col_idx)
    cell.text = header
    cell.text_frame.paragraphs[0].font.bold = True

# Add data rows
data = [['Item 1', '100', 'Active'], ['Item 2', '200', 'Inactive']]
for row_idx, row_data in enumerate(data, 1):
    for col_idx, value in enumerate(row_data):
        table.cell(row_idx, col_idx).text = value

prs.save('with_table.pptx')
```

## Editing Existing Presentations

```python
from pptx import Presentation

prs = Presentation('existing.pptx')

# Find and replace text
for slide in prs.slides:
    for shape in slide.shapes:
        if shape.has_text_frame:
            for para in shape.text_frame.paragraphs:
                for run in para.runs:
                    if 'old text' in run.text:
                        run.text = run.text.replace('old text', 'new text')

prs.save('modified.pptx')
```

## Design Guidelines

- **Bold color palette**: Pick colors fitting the topic
- **Visual elements**: Every slide needs image, chart, icon, or shape
- **Typography**: Use clear font pairing, title 36-44pt, body 14-16pt
- **Spacing**: 0.5" minimum margins, breathing room between elements

## Required Dependencies
```
pip install python-pptx
pip install "markitdown[pptx]"  # optional, for text extraction
```
