---
name: docx
description: "当用户需要创建、读取、编辑或操作 Word 文档（.docx 文件）时使用此技能。触发条件包括：提到"Word 文档"、".docx"，或要求生成带有目录、标题、页码、信头等格式的专业文档；从 .docx 文件中提取或重组内容；在 Word 文档中插入或替换图片；对 Word 文件进行查找与替换；处理修订记录或批注；将内容转换为精心排版的 Word 文档。如果用户要求将"报告"、"备忘录"、"信函"、"模板"等作为 Word 或 .docx 文件交付，则使用此技能。不适用于 PDF、电子表格、Google 文档或与文档生成无关的编程任务。"
---

# DOCX creation, editing, and analysis

## Overview

A .docx file is a ZIP archive containing XML files. Python library `python-docx` is the primary tool.

## Quick Reference

| Task | Approach |
|------|----------|
| Read/analyze content | Use `python-docx` |
| Create new document | Use `python-docx` |
| Edit existing document | Use `python-docx` load and save |

## Reading Content

```python
from docx import Document

doc = Document('document.docx')

# Read paragraphs
for para in doc.paragraphs:
    print(para.text)

# Read tables
for table in doc.tables:
    for row in table.rows:
        for cell in row.cells:
            print(cell.text)
```

## Creating New Documents

```python
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

doc = Document()

# Add title
doc.add_heading('Document Title', 0)

# Add paragraph
para = doc.add_paragraph('First paragraph text.')

# Add heading
doc.add_heading('Section 1', level=1)

# Add styled paragraph
para = doc.add_paragraph()
run = para.add_run('Bold text')
run.bold = True
run = para.add_run(' and normal text')

# Add table
table = doc.add_table(rows=2, cols=3)
table.style = 'Table Grid'
hdr_cells = table.rows[0].cells
hdr_cells[0].text = 'Column 1'
hdr_cells[1].text = 'Column 2'
hdr_cells[2].text = 'Column 3'

# Add image
doc.add_picture('image.png', width=Inches(4))

# Save
doc.save('output.docx')
```

## Editing Existing Documents

```python
from docx import Document

doc = Document('existing.docx')

# Find and replace text
for para in doc.paragraphs:
    if 'old text' in para.text:
        for run in para.runs:
            if 'old text' in run.text:
                run.text = run.text.replace('old text', 'new text')

# Add paragraph at end
doc.add_paragraph('New content')

doc.save('modified.docx')
```

## Formatting

```python
from docx.shared import Pt, RGBColor, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH

# Font formatting
run.font.size = Pt(12)
run.font.bold = True
run.font.italic = True
run.font.color.rgb = RGBColor(0xFF, 0x00, 0x00)

# Paragraph formatting
para.alignment = WD_ALIGN_PARAGRAPH.CENTER
para.paragraph_format.space_before = Pt(12)
para.paragraph_format.space_after = Pt(6)

# Page margins
from docx.shared import Inches
section = doc.sections[0]
section.top_margin = Inches(1)
section.bottom_margin = Inches(1)
section.left_margin = Inches(1.25)
section.right_margin = Inches(1.25)
```

## Working with Tables

```python
from docx import Document
from docx.shared import Pt, RGBColor
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

doc = Document()
table = doc.add_table(rows=1, cols=3)
table.style = 'Table Grid'

# Header row
hdr = table.rows[0].cells
hdr[0].text = 'Name'
hdr[1].text = 'Value'
hdr[2].text = 'Status'

# Make header bold
for cell in hdr:
    for para in cell.paragraphs:
        for run in para.runs:
            run.bold = True

# Add data rows
data = [('Item 1', '100', 'Active'), ('Item 2', '200', 'Inactive')]
for name, value, status in data:
    row = table.add_row().cells
    row[0].text = name
    row[1].text = value
    row[2].text = status

doc.save('table_doc.docx')
```

## Required Dependencies
```
pip install python-docx
```
