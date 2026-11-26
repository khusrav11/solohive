"""Отчёт в PDF: Markdown отчёта → PDF A4 (fpdf2, встроенный шрифт DejaVu с кириллицей и знаком ₸).

Таблицы переносятся по страницам с повтором шапки, статусы (✅ ⚠️ ❌, 🔴 🟡 🟢) рисуются цветными символами,
на каждой странице — колонтитул с названием и номером страницы.
"""

from __future__ import annotations

import re
from pathlib import Path

from .mdblocks import Block, parse, plain, spans

FONTS = Path(__file__).resolve().parents[1] / "fonts"
SANS, SANS_B, MONO = "DejaVuSansCondensed.ttf", "DejaVuSansCondensed-Bold.ttf", "DejaVuSansMono.ttf"

# Символы, которых нет в шрифте, → близкие знаки шрифта и цвет
_GLYPHS = {"✅": ("✓", (21, 128, 61)), "❌": ("✗", (185, 28, 28)), "⚠️": ("⚠", (180, 83, 9)), "⚠": ("⚠", (180, 83, 9)),
           "🔴": ("●", (185, 28, 28)), "🟡": ("●", (202, 138, 4)), "🟢": ("●", (21, 128, 61)),
           "⛔": ("⊘", (185, 28, 28)), "❓": ("?", (75, 85, 99)), "➖": ("–", (107, 114, 128))}
_DROP = ("️", "🐝", "✍", "💬", "🔍", "✂", "🧭", "🎭", "🔬", "⚖", "📄")
_INK, _MUTED, _RULE, _HEAD_BG = (17, 24, 39), (75, 85, 99), (209, 213, 219), (243, 244, 246)


def _clean(text: str) -> str:
    for ch in _DROP:
        text = text.replace(ch, "")
    for k, (v, _) in _GLYPHS.items():
        text = text.replace(k, v)
    return text


def _cell_color(raw: str):
    for k, (_, color) in _GLYPHS.items():
        if raw.strip().startswith(k):
            return color
    return None


def _make_pdf(title: str):
    from fpdf import FPDF

    class _PDF(FPDF):
        def footer(self):
            self.set_y(-10)
            self.set_font("dv", size=7)
            self.set_text_color(*_MUTED)
            self.cell(0, 5, f"{title} · стр. {self.page_no()}/{{nb}}", align="C")
            self.set_text_color(*_INK)

    pdf = _PDF(orientation="P", unit="mm", format="A4")
    pdf.add_font("dv", "", str(FONTS / SANS))
    pdf.add_font("dv", "B", str(FONTS / SANS_B))
    pdf.add_font("dv", "I", str(FONTS / SANS))         # курсива в комплекте нет — обычное начертание
    pdf.add_font("dv", "BI", str(FONTS / SANS_B))
    pdf.add_font("dvm", "", str(FONTS / MONO))
    pdf.set_margins(14, 14, 14)
    pdf.set_auto_page_break(True, margin=14)
    pdf.set_title(title)
    pdf.set_creator("SoloHive Requirements Validator")
    pdf.set_text_color(*_INK)
    return pdf


_MARK = re.compile("(" + "|".join(re.escape(k) for k in sorted(_GLYPHS, key=len, reverse=True)) + ")")


def _write_spans(pdf, text: str, size: float, line_h: float, base_bold: bool = False) -> None:
    ink = pdf.text_color
    for s in spans(text):
        if s.code:
            pdf.set_font("dvm", size=size - 1)
        else:
            pdf.set_font("dv", "B" if (s.bold or base_bold) else "", size=size)
        for part in _MARK.split(s.text):
            if not part:
                continue
            if part in _GLYPHS:
                glyph, color = _GLYPHS[part]
                pdf.set_text_color(*color)
                pdf.write(line_h, glyph)
                pdf.text_color = ink
                continue
            chunk = _clean(part)
            if chunk:
                pdf.write(line_h, chunk, link=s.link or "")
    pdf.set_font("dv", size=size)


def _col_widths(pdf, block: Block, total: float, size: float, pad: float) -> list[float]:
    """Ширина колонки не меньше самого длинного слова в ней; остаток делится по средней длине текста."""
    import math
    n = len(block.header)
    mins, weights = [], []
    for j in range(n):
        cells = [block.header[j]] + [r[j] for r in block.rows]
        texts = [_clean(plain(c)) for c in cells]
        words = [w for t in texts for w in t.split()] or [""]
        pdf.set_font("dv", "B", size=size)
        head_word = max((pdf.get_string_width(w) for w in texts[0].split()), default=0)
        pdf.set_font("dv", "", size=size)
        body_word = max(pdf.get_string_width(w) for w in words[:4000])
        mins.append(min(max(head_word, body_word) + 2 * pad + 0.6, total * 0.3))
        avg = sum(len(t) for t in texts) / len(texts)
        weights.append(math.sqrt(max(avg, 1.0)))
    spare = total - sum(mins)
    if spare <= 0:
        return [total * m / sum(mins) for m in mins]
    w_sum = sum(weights)
    return [m + spare * w / w_sum for m, w in zip(mins, weights)]


def _table(pdf, block: Block) -> None:
    from fpdf.fonts import FontFace

    n = len(block.header)
    size = 8 if n <= 4 else 7 if n <= 8 else 6.5
    widths = _col_widths(pdf, block, pdf.epw, size, 1.2)
    pdf.set_font("dv", size=size)
    head_style = FontFace(emphasis="BOLD", fill_color=_HEAD_BG)
    align_map = {"left": "LEFT", "center": "CENTER", "right": "RIGHT"}
    with pdf.table(col_widths=widths, text_align=[align_map[a] for a in block.align], headings_style=head_style,
                   line_height=size * 0.5, padding=1.2, borders_layout="SINGLE_TOP_LINE",
                   repeat_headings=1, first_row_as_headings=True) as table:
        row = table.row()
        for h in block.header:
            row.cell(_clean(plain(h)))
        for r in block.rows:
            row = table.row()
            for raw in r:
                color = _cell_color(raw)
                bold = raw.strip().startswith("**") and raw.strip().endswith("**")
                style = FontFace(color=color, emphasis="BOLD" if bold else None) if (color or bold) else None
                row.cell(_clean(plain(raw)), style=style)
    pdf.ln(2)


def _heading(pdf, block: Block) -> None:
    sizes = {1: 17, 2: 13.5, 3: 11.5, 4: 10, 5: 9.5, 6: 9}
    size = sizes.get(block.level, 9)
    if block.level <= 2 and pdf.get_y() > pdf.h - 60:
        pdf.add_page()
    pdf.ln(4 if block.level <= 2 else 2.5)
    pdf.set_font("dv", "B", size=size)
    pdf.multi_cell(0, size * 0.5, _clean(plain(block.text)), align="L", new_x="LMARGIN", new_y="NEXT")
    if block.level <= 2:
        pdf.set_draw_color(*_RULE)
        pdf.line(pdf.l_margin, pdf.get_y() + 0.8, pdf.w - pdf.r_margin, pdf.get_y() + 0.8)
        pdf.ln(2.5)
    else:
        pdf.ln(1)
    pdf.set_font("dv", size=9)


def render_pdf(md: str, path: str | Path, title: str = "Отчёт") -> Path:
    pdf = _make_pdf(title)
    pdf.alias_nb_pages()
    pdf.add_page()
    for block in parse(md):
        pdf.set_x(pdf.l_margin)
        if block.kind == "heading":
            _heading(pdf, block)
        elif block.kind == "para":
            _write_spans(pdf, block.text, 9, 4.4)
            pdf.ln(6)
        elif block.kind == "quote":
            y0 = pdf.get_y()
            pdf.set_left_margin(pdf.l_margin + 4)
            pdf.set_x(pdf.l_margin)
            pdf.set_text_color(*_MUTED)
            _write_spans(pdf, block.text.replace("  \n", "\n"), 8.5, 4.2)
            pdf.set_text_color(*_INK)
            pdf.set_left_margin(pdf.l_margin - 4)
            pdf.ln(4.6)
            if pdf.get_y() > y0:
                pdf.set_draw_color(*_RULE)
                pdf.set_line_width(0.8)
                pdf.line(pdf.l_margin + 1, y0, pdf.l_margin + 1, pdf.get_y() - 1)
                pdf.set_line_width(0.2)
            pdf.ln(1.5)
        elif block.kind == "list":
            for k, item in enumerate(block.items, start=1):
                pdf.set_x(pdf.l_margin)
                pdf.set_font("dv", size=9)
                pdf.write(4.4, f"{k}. " if block.ordered else "•  ")
                pdf.set_left_margin(pdf.l_margin + 5)
                _write_spans(pdf, item, 9, 4.4)
                pdf.set_left_margin(pdf.l_margin - 5)
                pdf.ln(5.2)
            pdf.ln(1)
        elif block.kind == "table":
            _table(pdf, block)
        elif block.kind == "hr":
            pdf.ln(1)
            pdf.set_draw_color(*_RULE)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.ln(3)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(out))
    return out
