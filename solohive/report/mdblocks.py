"""Разбор Markdown отчёта на блоки и строчные фрагменты — общая основа для выгрузки в PDF и DOCX.

Поддерживается подмножество, которое порождают сами генераторы отчёта: заголовки, абзацы, цитаты,
маркированные и нумерованные списки, таблицы (с экранированными «\\|» в ячейках), горизонтальные линии,
строчные **жирный**, *курсив*, `код` и [ссылки](url). Якорь <a id="…"></a> на отдельной строке — блок «anchor»
(цель внутренних ссылок «#…»), комментарий <!-- pagebreak --> — разрыв страницы в PDF и DOCX.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_ANCHOR = re.compile(r'<a\s+id="[^"]*"\s*>\s*</a>')
_ANCHOR_LINE = re.compile(r'^\s*<a\s+id="([^"]*)"\s*>\s*</a>\s*$')
_PAGEBREAK = re.compile(r"^\s*<!--\s*pagebreak\s*-->\s*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_LIST = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(.*)$")
_HR = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_INLINE = re.compile(r"\*\*(?P<b>.+?)\*\*|`(?P<c>[^`]+)`|\[(?P<lt>[^\]]+)\]\((?P<lu>[^)\s]+)\)"
                     r"|(?<![\w*])\*(?P<i>[^*\s][^*]*?)\*(?![\w*])|<br\s*/?>(?P<br>)")
_ESCAPES = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|>])")


@dataclass
class Span:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    link: str = ""


@dataclass
class Block:
    kind: str                     # heading | para | quote | list | table | hr | anchor | pagebreak
    level: int = 0                # уровень заголовка
    text: str = ""                # заголовок, абзац, цитата; имя якоря
    items: list[str] = field(default_factory=list)          # пункты списка
    ordered: bool = False
    header: list[str] = field(default_factory=list)         # таблица
    rows: list[list[str]] = field(default_factory=list)
    align: list[str] = field(default_factory=list)          # left | center | right


def unescape(text: str) -> str:
    return _ESCAPES.sub(r"\1", text)


def spans(text: str) -> list[Span]:
    """Строчная разметка → фрагменты с признаками начертания."""
    out: list[Span] = []
    pos = 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            out.append(Span(unescape(text[pos:m.start()])))
        if m.group("b") is not None:
            out.extend(Span(s.text, bold=True, italic=s.italic, code=s.code, link=s.link) for s in spans(m.group("b")))
        elif m.group("c") is not None:
            out.append(Span(m.group("c"), code=True))
        elif m.group("lt") is not None:
            url = m.group("lu")
            out.extend(Span(s.text, bold=s.bold, italic=s.italic, code=s.code, link=url)
                       for s in spans(m.group("lt")))
        elif m.group("i") is not None:
            out.append(Span(unescape(m.group("i")), italic=True))
        elif m.group("br") is not None:
            out.append(Span("\n"))
        pos = m.end()
    if pos < len(text):
        out.append(Span(unescape(text[pos:])))
    return [s for s in out if s.text]


def plain(text: str) -> str:
    return "".join(s.text for s in spans(text))


def slug(text: str) -> str:
    """Якорь заголовка как у GitHub: «4.2. Системные дефекты набора» → «42-системные-дефекты-набора»."""
    t = plain(text).strip().lower()
    return re.sub(r"[^\w\- ]", "", t).replace(" ", "-")


def split_row(line: str) -> list[str]:
    """Строка таблицы → ячейки; «\\|» внутри ячейки остаётся символом."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, i = [], [], 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur.append("\\|")
            i += 2
            continue
        if ch == "|":
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    cells.append("".join(cur).strip())
    return cells


def _align(sep: str) -> list[str]:
    out = []
    for c in split_row(sep):
        c = c.strip()
        out.append("center" if c.startswith(":") and c.endswith(":") else "right" if c.endswith(":") else "left")
    return out


def parse(md: str) -> list[Block]:
    lines = [ln if _ANCHOR_LINE.match(ln) else _ANCHOR.sub("", ln) for ln in md.splitlines()]
    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = _ANCHOR_LINE.match(line)
        if m:
            blocks.append(Block("anchor", text=m.group(1)))
            i += 1
            continue
        if _PAGEBREAK.match(line):
            blocks.append(Block("pagebreak"))
            i += 1
            continue
        m = _HEADING.match(line)
        if m:
            blocks.append(Block("heading", level=len(m.group(1)), text=m.group(2)))
            i += 1
            continue
        if _HR.match(line):
            blocks.append(Block("hr"))
            i += 1
            continue
        if line.lstrip().startswith("|") and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
            header, align = split_row(line), _align(lines[i + 1])
            rows = []
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                row = split_row(lines[i])
                row = (row + [""] * len(header))[:len(header)]
                rows.append(row)
                i += 1
            blocks.append(Block("table", header=header, rows=rows, align=(align + ["left"] * len(header))[:len(header)]))
            continue
        if line.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i][1:].strip())
                i += 1
            blocks.append(Block("quote", text="\n".join(buf)))
            continue
        m = _LIST.match(line)
        if m:
            ordered = bool(re.match(r"\s*\d", line))
            items = []
            while i < len(lines):
                mm = _LIST.match(lines[i])
                if mm:
                    items.append(mm.group(2))
                elif lines[i].startswith("  ") and lines[i].strip() and items:
                    items[-1] += " " + lines[i].strip()       # продолжение пункта
                else:
                    break
                i += 1
            blocks.append(Block("list", items=items, ordered=ordered))
            continue
        buf = []
        while i < len(lines) and lines[i].strip() and not (
                _HEADING.match(lines[i]) or _HR.match(lines[i]) or lines[i].startswith(">")
                or _LIST.match(lines[i]) or lines[i].lstrip().startswith("|")
                or _ANCHOR_LINE.match(lines[i]) or _PAGEBREAK.match(lines[i])):
            buf.append(lines[i].rstrip())
            i += 1
        if not buf:          # строка, которую не взял ни один разбор, — как абзац
            buf, i = [line], i + 1
        text = "\n".join(b[:-2] if b.endswith("  ") else b for b in buf) if any(b.endswith("  ") for b in buf) \
            else " ".join(b.strip() for b in buf)
        blocks.append(Block("para", text=text))
    return blocks
