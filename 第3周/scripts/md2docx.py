# -*- coding: utf-8 -*-
"""
md2docx.py —— 把本周的 Markdown 报告转成 Word（.docx），用于课程提交。

用法：
    python md2docx.py <input.md> <output.docx>

支持：标题（#/##/###）、正文、引用块、无序/有序列表、表格、
      围栏代码块、图片、**加粗**、`行内代码`、[链接](url)、分隔线 ---。
中文字体：正文宋体、标题黑体、代码 Consolas；A4 页面、1.5 倍行距、页脚页码。
"""
import io
import os
import re
import sys

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

BODY_FONT = "宋体"
HEAD_FONT = "黑体"
CODE_FONT = "Consolas"


def set_run_font(run, ascii_font, ea_font, size=None, bold=None, color=None):
    run.font.name = ascii_font
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), ascii_font)
    rfonts.set(qn("w:hAnsi"), ascii_font)
    rfonts.set(qn("w:eastAsia"), ea_font)
    if size is not None:
        run.font.size = size
    if bold is not None:
        run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color


def shade_paragraph(p, fill):
    pPr = p._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    pPr.append(shd)


def shade_cell(cell, fill):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tcPr.append(shd)


INLINE_RE = re.compile(r"(\*\*.+?\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))")


def add_inline(paragraph, text, size=None, bold_all=False, color=None):
    """拆 **加粗**、`代码`、[链接](url) 后逐段写入"""
    for part in INLINE_RE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            # 加粗里可能还嵌着 `代码`，拆一层，避免反引号原样输出
            for seg in re.split(r"(`[^`]+`)", part[2:-2]):
                if not seg:
                    continue
                if seg.startswith("`") and seg.endswith("`"):
                    r = paragraph.add_run(seg[1:-1])
                    set_run_font(r, CODE_FONT, BODY_FONT, Pt(9.5), bold=True, color=color)
                else:
                    r = paragraph.add_run(seg)
                    set_run_font(r, BODY_FONT, BODY_FONT, size, bold=True, color=color)
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            r = paragraph.add_run(part[1:-1])
            set_run_font(r, CODE_FONT, BODY_FONT, Pt(9.5), bold=bold_all, color=color)
        elif part.startswith("[") and "](" in part and part.endswith(")"):
            label, url = re.match(r"\[([^\]]+)\]\(([^)]+)\)", part).groups()
            r = paragraph.add_run(label)
            set_run_font(r, BODY_FONT, BODY_FONT, size, bold=bold_all, color=color)
            if url.startswith("http"):
                r2 = paragraph.add_run("（" + url + "）")
                set_run_font(r2, BODY_FONT, BODY_FONT, Pt(9), color=RGBColor(0x59, 0x63, 0x6F))
        else:
            r = paragraph.add_run(part)
            set_run_font(r, BODY_FONT, BODY_FONT, size, bold=bold_all, color=color)


def is_table_sep(line):
    s = line.strip()
    return bool(re.match(r"^\|[\s:\-\|]+\|$", s)) and "-" in s


def parse_table_row(line):
    s = line.strip().strip("|")
    return [c.strip() for c in s.split("|")]


def add_code_block(doc, lines):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.3)
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.line_spacing = 1.0
    shade_paragraph(p, "F4F5F7")
    for i, line in enumerate(lines):
        r = p.add_run(line)
        set_run_font(r, CODE_FONT, BODY_FONT, Pt(9))
        if i != len(lines) - 1:
            r.add_break()


def add_table(doc, rows):
    cols = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=cols)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True
    for i, row in enumerate(rows):
        for j in range(cols):
            cell = table.cell(i, j)
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(2)
            text = row[j] if j < len(row) else ""
            add_inline(p, text, Pt(9.5), bold_all=(i == 0))
            if i == 0:
                shade_cell(cell, "E8EEF7")
    doc.add_paragraph()


def add_footer(section, text):
    footer = section.footer
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(text + "    第 ")
    set_run_font(r, BODY_FONT, BODY_FONT, Pt(8), color=RGBColor(0x80, 0x80, 0x80))
    # 页码域
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    p._p.append(fld)
    r2 = p.add_run(" 页")
    set_run_font(r2, BODY_FONT, BODY_FONT, Pt(8), color=RGBColor(0x80, 0x80, 0x80))


def setup_document():
    doc = Document()
    sec = doc.sections[0]
    sec.page_width = Cm(21.0)
    sec.page_height = Cm(29.7)
    sec.top_margin = Cm(2.4)
    sec.bottom_margin = Cm(2.2)
    sec.left_margin = Cm(2.6)
    sec.right_margin = Cm(2.4)

    normal = doc.styles["Normal"]
    normal.font.name = BODY_FONT
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
    normal.paragraph_format.line_spacing = 1.5
    normal.paragraph_format.space_after = Pt(3)

    for name, size in (("Heading 1", 16), ("Heading 2", 13.5), ("Heading 3", 12)):
        st = doc.styles[name]
        st.font.name = HEAD_FONT
        st.font.size = Pt(size)
        st.font.bold = True
        st.font.color.rgb = RGBColor(0x12, 0x30, 0x4F)
        rpr = st.element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.append(rfonts)
        rfonts.set(qn("w:eastAsia"), HEAD_FONT)
        rfonts.set(qn("w:ascii"), HEAD_FONT)
        rfonts.set(qn("w:hAnsi"), HEAD_FONT)
    doc.styles["Heading 1"].paragraph_format.space_before = Pt(14)
    doc.styles["Heading 1"].paragraph_format.space_after = Pt(8)
    doc.styles["Heading 2"].paragraph_format.space_before = Pt(10)
    doc.styles["Heading 2"].paragraph_format.space_after = Pt(6)
    return doc


def convert(md_path, docx_path):
    base = os.path.dirname(os.path.abspath(md_path))
    lines = io.open(md_path, encoding="utf-8").read().splitlines()

    doc = setup_document()
    add_footer(doc.sections[0], "云仓五号 · 第 2 周")

    first_title = True
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 空行
        if not stripped:
            i += 1
            continue

        # 分隔线
        if re.match(r"^-{3,}$", stripped):
            i += 1
            continue

        # 代码块
        if stripped.startswith("```"):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            add_code_block(doc, buf)
            continue

        # 图片
        m = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", stripped)
        if m:
            alt, src = m.group(1), m.group(2)
            img_path = os.path.normpath(os.path.join(base, src))
            if os.path.exists(img_path):
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.add_run().add_picture(img_path, width=Cm(15.5))
                if alt:
                    cap = doc.add_paragraph()
                    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    r = cap.add_run(alt)
                    set_run_font(r, BODY_FONT, BODY_FONT, Pt(9), color=RGBColor(0x66, 0x66, 0x66))
            else:
                p = doc.add_paragraph()
                add_inline(p, "（图片未找到：" + src + "）", Pt(9.5))
            i += 1
            continue

        # 标题
        m = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            if level == 1 and first_title:
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                add_inline(p, text, Pt(20), bold_all=True)
                p.paragraph_format.space_after = Pt(10)
                first_title = False
            else:
                doc.add_heading(text, level=min(level - 1, 3) if level > 1 else 1)
                if level == 1:
                    first_title = False
            i += 1
            continue

        # 表格
        if stripped.startswith("|") and stripped.endswith("|"):
            rows = []
            while i < len(lines):
                cur = lines[i].strip()
                if not (cur.startswith("|") and cur.endswith("|")):
                    break
                if not is_table_sep(cur):
                    rows.append(parse_table_row(cur))
                i += 1
            add_table(doc, rows)
            continue

        # 引用块
        if stripped.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.5)
            for k, t in enumerate(buf):
                if k:
                    p.add_run().add_break()
                add_inline(p, t, Pt(9.5), color=RGBColor(0x4B, 0x55, 0x63))
            continue

        # 无序列表
        if re.match(r"^[-*]\s+", stripped):
            p = doc.add_paragraph(style="List Bullet")
            add_inline(p, re.sub(r"^[-*]\s+", "", stripped))
            i += 1
            continue

        # 有序列表
        if re.match(r"^\d+\.\s+", stripped):
            p = doc.add_paragraph(style="List Number")
            add_inline(p, re.sub(r"^\d+\.\s+", "", stripped))
            i += 1
            continue

        # 普通段落
        p = doc.add_paragraph()
        add_inline(p, stripped)
        i += 1

    doc.core_properties.title = os.path.splitext(os.path.basename(md_path))[0]
    doc.core_properties.author = "云仓五号"
    doc.save(docx_path)

    chk = Document(docx_path)
    tables = len(chk.tables)
    images = len(chk.inline_shapes)
    paras = len(chk.paragraphs)
    print("saved:", docx_path)
    print("  段落 %d / 表格 %d / 图片 %d" % (paras, tables, images))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("用法: python md2docx.py <input.md> <output.docx>")
        sys.exit(1)
    convert(sys.argv[1], sys.argv[2])
