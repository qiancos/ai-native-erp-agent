# -*- coding: utf-8 -*-
"""
Markdown -> DOCX（V2.0 文档排版）
在 md_to_docx.py 基础上增强：封面、页眉页脚、页码、表格列宽按内容分配、标题底纹。
用法：python build_docs_v2.py <input.md> <output.docx> <文档标题> <副标题> <版本>
"""
import sys
import re
from docx import Document
from docx.shared import Pt, Cm, RGBColor, Emu
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.section import WD_SECTION
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

BODY_FONT = '宋体'
HEAD_FONT = '黑体'
MONO_FONT = 'Consolas'
INK = RGBColor(0x1A, 0x1A, 0x1A)
GRAY = RGBColor(0x59, 0x59, 0x59)
ACCENT = RGBColor(0x1F, 0x3A, 0x93)

CONTENT_WIDTH_CM = 16.0


def set_east_asia(run, font):
    run.font.name = font if font != BODY_FONT else 'Times New Roman'
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn('w:rFonts'))
    if rfonts is None:
        rfonts = OxmlElement('w:rFonts')
        rpr.append(rfonts)
    rfonts.set(qn('w:eastAsia'), font)


def _set_run_font(run, font=BODY_FONT, size=10.5, bold=False, color=None, italic=False):
    run.font.name = 'Times New Roman'
    set_east_asia(run, font)
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    if color is not None:
        run.font.color.rgb = color


def add_inline(paragraph, text, size=10.5, base_font=BODY_FONT, color=None):
    parts = re.split(r'(\*\*.+?\*\*|`.+?`|\*[^*\n]+?\*)', text)
    for part in parts:
        if not part:
            continue
        if part.startswith('**') and part.endswith('**') and len(part) > 4:
            r = paragraph.add_run(part[2:-2])
            _set_run_font(r, font=base_font, size=size, bold=True, color=color)
        elif part.startswith('*') and part.endswith('*') and len(part) > 2:
            r = paragraph.add_run(part[1:-1])
            _set_run_font(r, font=base_font, size=size, italic=True, color=color)
        elif part.startswith('`') and part.endswith('`') and len(part) > 2:
            r = paragraph.add_run(part[1:-1])
            _set_run_font(r, font=MONO_FONT, size=size - 1, color=color)
        else:
            r = paragraph.add_run(part)
            _set_run_font(r, font=base_font, size=size, color=color)


def shade_cell(cell, fill='F2F4F8'):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), fill)
    tc_pr.append(shd)


def shade_paragraph(p, fill='F7F8FA'):
    p_pr = p._p.get_or_add_pPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), fill)
    p_pr.append(shd)


def set_row_cant_split(row):
    tr_pr = row._tr.get_or_add_trPr()
    tr_pr.append(OxmlElement('w:cantSplit'))


def set_row_header(row):
    tr_pr = row._tr.get_or_add_trPr()
    tr_pr.append(OxmlElement('w:tblHeader'))


def set_table_borders(table, color='D0D5DD'):
    tbl = table._tbl
    tbl_pr = tbl.tblPr
    borders = OxmlElement('w:tblBorders')
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        el = OxmlElement(f'w:{edge}')
        el.set(qn('w:val'), 'single')
        el.set(qn('w:sz'), '4')
        el.set(qn('w:space'), '0')
        el.set(qn('w:color'), color)
        borders.append(el)
    tbl_pr.append(borders)


def display_width(text):
    text = re.sub(r'\*\*|`', '', text or '')
    w = 0
    for ch in text:
        w += 2 if ord(ch) > 0x2E80 else 1
    return max(w, 3)


def add_table(rows, doc):
    if not rows:
        return
    n_cols = max(len(r) for r in rows)
    rows = [r + [''] * (n_cols - len(r)) for r in rows]
    weights = []
    for ci in range(n_cols):
        longest = max(display_width(r[ci]) for r in rows)
        weights.append(min(longest, 34))
    total = sum(weights)
    widths = [max(2.0, CONTENT_WIDTH_CM * w / total) for w in weights]
    scale = CONTENT_WIDTH_CM / sum(widths)
    widths = [w * scale for w in widths]

    table = doc.add_table(rows=len(rows), cols=n_cols)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    set_table_borders(table)
    tbl_pr = table._tbl.tblPr
    layout = OxmlElement('w:tblLayout')
    layout.set(qn('w:type'), 'fixed')
    tbl_pr.append(layout)

    for ci, w in enumerate(widths):
        table.columns[ci].width = Cm(w)
    for ri, row in enumerate(rows):
        set_row_cant_split(table.rows[ri])
        table.rows[ri].height = Cm(0.55)
        if ri == 0:
            set_row_header(table.rows[ri])
        for ci in range(n_cols):
            cell = table.rows[ri].cells[ci]
            cell.width = Cm(widths[ci])
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.line_spacing = 1.15
            add_inline(p, row[ci], size=9, color=INK)
            if ri == 0:
                shade_cell(cell)
                for r in p.runs:
                    r.font.bold = True
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(2)
    spacer.paragraph_format.space_before = Pt(0)


def add_code_block(lines, doc):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.left_indent = Cm(0.4)
    p.paragraph_format.line_spacing = 1.05
    shade_paragraph(p)
    for i, line in enumerate(lines):
        r = p.add_run(line if line else ' ')
        _set_run_font(r, font=MONO_FONT, size=8.5, color=RGBColor(0x33, 0x33, 0x33))
        if i < len(lines) - 1:
            r.add_break()


def add_heading(doc, text, level):
    p = doc.add_paragraph()
    p.paragraph_format.keep_with_next = True
    if level == 1:
        p.paragraph_format.space_before = Pt(18)
        p.paragraph_format.space_after = Pt(8)
        pPr = p._p.get_or_add_pPr()
        pBdr = OxmlElement('w:pBdr')
        bottom = OxmlElement('w:bottom')
        bottom.set(qn('w:val'), 'single')
        bottom.set(qn('w:sz'), '8')
        bottom.set(qn('w:space'), '2')
        bottom.set(qn('w:color'), '1F3A93')
        pBdr.append(bottom)
        pPr.append(pBdr)
    elif level == 2:
        p.paragraph_format.space_before = Pt(14)
        p.paragraph_format.space_after = Pt(6)
    else:
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after = Pt(4)
    sizes = {1: 17, 2: 13.5, 3: 11.5, 4: 10.5}
    r = p.add_run(text)
    _set_run_font(r, font=HEAD_FONT, size=sizes.get(level, 10.5), bold=True,
                  color=ACCENT if level <= 2 else INK)


def add_body(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.line_spacing = 1.55
    p.paragraph_format.first_line_indent = Cm(0)
    add_inline(p, text, color=INK)


def add_bullet(doc, text, level=0):
    p = doc.add_paragraph(style='List Bullet')
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1.45
    p.paragraph_format.left_indent = Cm(0.75 + level * 0.6)
    add_inline(p, text, color=INK)


def add_quote(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.5)
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.line_spacing = 1.45
    r = p.add_run(text)
    _set_run_font(r, size=10, italic=True, color=GRAY)


def parse_md(md_text, doc):
    lines = md_text.splitlines()
    i = 0
    in_code = False
    code_buf = []
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith('```'):
            if not in_code:
                in_code = True
                code_buf = []
            else:
                add_code_block(code_buf, doc)
                in_code = False
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue

        if stripped in ('---', '***'):
            i += 1
            continue

        m = re.match(r'^(#{1,4})\s+(.*)$', stripped)
        if m:
            txt = m.group(2).strip()
            if len(m.group(1)) == 1 and txt in ('目录',):
                i += 1
                continue
            add_heading(doc, txt, len(m.group(1)))
            i += 1
            continue

        if stripped.startswith('|') and stripped.endswith('|'):
            tbl_rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                row_line = lines[i].strip()
                cells = [c.strip() for c in row_line.strip('|').split('|')]
                if not all(re.fullmatch(r':?-{2,}:?', c or '---') for c in cells):
                    tbl_rows.append(cells)
                i += 1
            add_table(tbl_rows, doc)
            continue

        if stripped.startswith('>'):
            add_quote(doc, stripped.lstrip('>').strip())
            i += 1
            continue

        m = re.match(r'^[-*]\s+(.*)$', stripped)
        if m:
            add_bullet(doc, m.group(1))
            i += 1
            continue

        m = re.match(r'^(\d+)\.\s+(.*)$', stripped)
        if m:
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.line_spacing = 1.45
            p.paragraph_format.left_indent = Cm(0.75)
            p.paragraph_format.first_line_indent = Cm(-0.55)
            r = p.add_run(f'{m.group(1)}. ')
            _set_run_font(r, size=10.5, bold=True, color=ACCENT)
            add_inline(p, m.group(2), color=INK)
            i += 1
            continue

        if stripped == '':
            i += 1
            continue

        para = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if nxt == '' or nxt.startswith('#') or nxt.startswith('|') \
                    or nxt.startswith('```') or nxt.startswith('- ') or nxt.startswith('* ') \
                    or re.match(r'^\d+\.', nxt) or nxt in ('---', '***') or nxt.startswith('>'):
                break
            para.append(nxt)
            i += 1
        add_body(doc, ' '.join(para))


def add_field(paragraph, instr, size=8.5, color=GRAY):
    run = paragraph.add_run()
    _set_run_font(run, size=size, color=color)
    fld_begin = OxmlElement('w:fldChar')
    fld_begin.set(qn('w:fldCharType'), 'begin')
    instr_el = OxmlElement('w:instrText')
    instr_el.set(qn('xml:space'), 'preserve')
    instr_el.text = instr
    fld_sep = OxmlElement('w:fldChar')
    fld_sep.set(qn('w:fldCharType'), 'separate')
    txt = OxmlElement('w:t')
    txt.text = '1'
    fld_end = OxmlElement('w:fldChar')
    fld_end.set(qn('w:fldCharType'), 'end')
    run._r.append(fld_begin)
    run._r.append(instr_el)
    run._r.append(fld_sep)
    run._r.append(txt)
    run._r.append(fld_end)


def build_cover(doc, title, subtitle, version, date_str):
    for _ in range(4):
        doc.add_paragraph()
    p = doc.add_paragraph()
    r = p.add_run('ERP-AGENT  ·  PRODUCT DOCUMENT')
    _set_run_font(r, font='Arial', size=10, bold=True, color=ACCENT)
    p.paragraph_format.space_after = Pt(6)

    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(4)
    r = p.add_run('AI 原生 ERP 智能体平台')
    _set_run_font(r, font=HEAD_FONT, size=16, bold=True, color=INK)

    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(18)
    r = p.add_run(title)
    _set_run_font(r, font=HEAD_FONT, size=30, bold=True, color=INK)

    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(40)
    r = p.add_run(subtitle)
    _set_run_font(r, size=11, color=GRAY)

    info = [('文档版本', version), ('编写日期', date_str),
            ('文档状态', '评审稿'), ('文档密级', '内部公开')]
    table = doc.add_table(rows=len(info), cols=2)
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    for ri, (k, v) in enumerate(info):
        for ci, val in enumerate((k, v)):
            cell = table.rows[ri].cells[ci]
            cell.width = Cm(3.6 if ci == 0 else 9.0)
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(3)
            p.paragraph_format.space_after = Pt(3)
            r = p.add_run(val)
            _set_run_font(r, size=9.5, bold=(ci == 0), color=GRAY if ci == 0 else INK)
            tc_pr = cell._tc.get_or_add_tcPr()
            borders = OxmlElement('w:tcBorders')
            for edge in ('top', 'left', 'right'):
                el = OxmlElement(f'w:{edge}')
                el.set(qn('w:val'), 'nil')
                borders.append(el)
            bottom = OxmlElement('w:bottom')
            bottom.set(qn('w:val'), 'single')
            bottom.set(qn('w:sz'), '4')
            bottom.set(qn('w:color'), 'E4E7EC')
            borders.append(bottom)
            tc_pr.append(borders)

    doc.add_page_break()


def build_header_footer(section, doc_title, version):
    section.different_first_page_header_footer = True
    header = section.header
    hp = header.paragraphs[0]
    hp.paragraph_format.tab_stops.add_tab_stop(Cm(CONTENT_WIDTH_CM), WD_TAB_ALIGNMENT.RIGHT)
    r = hp.add_run('AI 原生 ERP 智能体平台')
    _set_run_font(r, size=8.5, color=GRAY)
    r = hp.add_run('\t' + doc_title + '  ' + version)
    _set_run_font(r, size=8.5, color=GRAY)

    footer = section.footer
    fp = footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = fp.add_run('第 ')
    _set_run_font(r, size=8.5, color=GRAY)
    add_field(fp, 'PAGE')
    r = fp.add_run(' 页 / 共 ')
    _set_run_font(r, size=8.5, color=GRAY)
    add_field(fp, 'NUMPAGES')
    r = fp.add_run(' 页')
    _set_run_font(r, size=8.5, color=GRAY)


def main():
    input_md, output_docx, title, subtitle, version = sys.argv[1:6]
    with open(input_md, 'r', encoding='utf-8') as f:
        md_text = f.read()

    doc = Document()
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.4)
    section.bottom_margin = Cm(2.2)
    section.left_margin = Cm(2.5)
    section.right_margin = Cm(2.5)
    section.header_distance = Cm(1.3)
    section.footer_distance = Cm(1.2)

    normal = doc.styles['Normal']
    normal.font.name = 'Times New Roman'
    normal.font.size = Pt(10.5)
    normal._element.rPr.rFonts.set(qn('w:eastAsia'), BODY_FONT)

    build_header_footer(section, title, version)
    build_cover(doc, title, subtitle, version, '2026-11-24')

    # 正文从标题开始
    parse_md(md_text, doc)
    doc.save(output_docx)
    print('Saved DOCX:', output_docx)


if __name__ == '__main__':
    main()
