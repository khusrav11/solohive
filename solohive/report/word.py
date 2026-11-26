"""Отчёт в DOCX (Word): Markdown отчёта → документ python-docx с заголовками, таблицами и списками."""

from __future__ import annotations

from pathlib import Path

from .mdblocks import Block, parse, plain, spans

_COLORS = {"✅": "15803D", "❌": "B91C1C", "⚠": "B45309", "🔴": "B91C1C", "🟡": "CA8A04", "🟢": "15803D",
           "⛔": "B91C1C"}


def _set_cell_shading(cell, hex_fill: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tc_pr.append(shd)


def _add_runs(paragraph, text: str, size: float | None = None, bold: bool = False, color: str | None = None) -> None:
    from docx.shared import Pt, RGBColor
    for s in spans(text):
        run = paragraph.add_run(s.text)
        run.bold = s.bold or bold or None
        run.italic = s.italic or None
        if s.code:
            run.font.name = "Consolas"
        if size:
            run.font.size = Pt(size)
        hue = color or next((c for k, c in _COLORS.items() if s.text.strip().startswith(k)), None)
        if hue:
            run.font.color.rgb = RGBColor.from_string(hue)


def _widths_cm(block: Block, total: float, size: float) -> list[float]:
    """Как в PDF: колонка не уже самого длинного слова, остаток — по средней длине текста."""
    import math
    char_cm = 0.19 * size / 9
    mins, weights = [], []
    for j in range(len(block.header)):
        texts = [plain(block.header[j])] + [plain(r[j]) for r in block.rows]
        longest = max((len(w) for t in texts for w in t.split()), default=1)
        mins.append(min(longest * char_cm + 0.35, total * 0.3))
        weights.append(math.sqrt(max(sum(len(t) for t in texts) / len(texts), 1.0)))
    spare = total - sum(mins)
    if spare <= 0:
        return [total * m / sum(mins) for m in mins]
    return [m + spare * w / sum(weights) for m, w in zip(mins, weights)]


def _table(doc, block: Block, total_cm: float) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm
    n = len(block.header)
    size = 9 if n <= 4 else 8 if n <= 8 else 7
    table = doc.add_table(rows=1, cols=n)
    table.style = "Table Grid"
    table.autofit = False
    widths = [Cm(w) for w in _widths_cm(block, total_cm, size)]
    align = {"left": WD_ALIGN_PARAGRAPH.LEFT, "center": WD_ALIGN_PARAGRAPH.CENTER, "right": WD_ALIGN_PARAGRAPH.RIGHT}
    for j, h in enumerate(block.header):
        cell = table.rows[0].cells[j]
        _set_cell_shading(cell, "F3F4F6")
        p = cell.paragraphs[0]
        p.alignment = align[block.align[j]]
        _add_runs(p, h, size=size, bold=True)
    for r in block.rows:
        cells = table.add_row().cells
        for j, raw in enumerate(r):
            p = cells[j].paragraphs[0]
            p.alignment = align[block.align[j]]
            _add_runs(p, raw.replace("<br>", "\n"), size=size)
    for j, col in enumerate(table.columns):
        col.width = widths[j]
        for cell in col.cells:
            cell.width = widths[j]
    doc.add_paragraph()


def render_docx(md: str, path: str | Path, title: str = "Отчёт") -> Path:
    from docx import Document
    from docx.enum.section import WD_ORIENT  # noqa: F401  (ориентация по умолчанию — книжная)
    from docx.shared import Cm, Pt

    doc = Document()
    for section in doc.sections:
        section.left_margin = section.right_margin = Cm(1.6)
        section.top_margin = section.bottom_margin = Cm(1.5)
    sec = doc.sections[0]
    total_cm = (sec.page_width - sec.left_margin - sec.right_margin) / 360000
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10)
    doc.core_properties.title = title
    doc.core_properties.author = "SoloHive Requirements Validator"

    for block in parse(md):
        if block.kind == "heading":
            h = doc.add_heading(level=min(block.level, 4) if block.level > 1 else 0)
            _add_runs(h, block.text)
        elif block.kind == "para":
            _add_runs(doc.add_paragraph(), block.text)
        elif block.kind == "quote":
            p = doc.add_paragraph(style="Quote")
            _add_runs(p, block.text.replace("  \n", "\n"), size=9)
        elif block.kind == "list":
            style = "List Number" if block.ordered else "List Bullet"
            for item in block.items:
                _add_runs(doc.add_paragraph(style=style), item)
        elif block.kind == "table":
            _table(doc, block, total_cm)
        elif block.kind == "hr":
            doc.add_paragraph("―" * 30).alignment = 1
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    return out
