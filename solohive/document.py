"""Модель исходного документа: строки с номерами, разделы, таблицы, поиск цитат."""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_MD_MARKS = re.compile(r"[*_`#>|]")
_SPACES = re.compile(r"\s+")


def clean_md(text: str) -> str:
    """Убирает markdown-разметку, сохраняя текст (для заголовков, ячеек, отображения)."""
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"^\s*[*\-+]\s+", "", t)
    t = t.replace("<br>", " ").replace("\\", "")
    return _SPACES.sub(" ", t).strip()


def normalize(text: str) -> str:
    """Нормализация для сопоставления цитат: регистр, ё, кавычки, тире, разметка, пробелы."""
    t = text.lower().replace("ё", "е")
    t = t.replace(" ", " ").replace(" ", " ").replace(" ", " ")
    for q in "«»“”„\"'‘’`":
        t = t.replace(q, "")
    for d in "–—−‑":
        t = t.replace(d, "-")
    t = t.replace("…", "...")
    t = _MD_MARKS.sub(" ", t)
    t = re.sub(r"(?<=\d) (?=\d{3}\b)", "", t)  # «1 000» == «1000»
    return _SPACES.sub(" ", t).strip()


@dataclass
class Line:
    no: int
    text: str
    page: int | None
    section: tuple[str, ...]


@dataclass
class Section:
    title: str
    level: int
    path: tuple[str, ...]
    line_start: int
    line_end: int


@dataclass
class Table:
    section: tuple[str, ...]
    header: list[str]
    rows: list[list[str]]
    header_line: int
    row_lines: list[int]


@dataclass
class Match:
    line_start: int
    line_end: int
    score: float
    exact: bool


@dataclass
class SourceDocument:
    path: Path
    fmt: str
    sha256: str
    lines: list[Line]
    title: str = ""
    sections: list[Section] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    _norm_text: str = ""
    _offsets: list[int] = field(default_factory=list)

    # --- построение -------------------------------------------------------------
    @classmethod
    def from_markdown(cls, path: Path, fmt: str, sha256: str, markdown: str,
                      pages: list[int | None] | None = None) -> "SourceDocument":
        raw = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        stack: list[tuple[int, str]] = []
        lines: list[Line] = []
        sections: list[Section] = []
        open_sections: list[Section] = []
        for i, text in enumerate(raw, start=1):
            m = _HEADING.match(text)
            if m:
                level, title = len(m.group(1)), clean_md(m.group(2))
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, title))
                path_ = tuple(t for _, t in stack)
                while open_sections and open_sections[-1].level >= level:
                    open_sections.pop().line_end = i - 1
                sec = Section(title=title, level=level, path=path_, line_start=i, line_end=len(raw))
                sections.append(sec)
                open_sections.append(sec)
            page = pages[i - 1] if pages and i - 1 < len(pages) else None
            lines.append(Line(no=i, text=text, page=page, section=tuple(t for _, t in stack)))
        for sec in open_sections:
            sec.line_end = len(raw)
        doc = cls(path=path, fmt=fmt, sha256=sha256, lines=lines, sections=sections)
        doc.title = doc._detect_title()
        doc.tables = doc._parse_tables()
        doc._build_index()
        return doc

    def _detect_title(self) -> str:
        h1 = [s for s in self.sections if s.level == 1]
        if h1:
            return h1[0].title
        if self.sections:
            return self.sections[0].title
        for line in self.lines:
            if line.text.strip():
                return clean_md(line.text)[:120]
        return self.path.stem

    def _parse_tables(self) -> list[Table]:
        tables: list[Table] = []
        i = 0
        n = len(self.lines)
        while i < n:
            if self.lines[i].text.lstrip().startswith("|"):
                block = []
                while i < n and self.lines[i].text.lstrip().startswith("|"):
                    block.append(self.lines[i])
                    i += 1
                rows = [(ln, [clean_md(c) for c in ln.text.strip().strip("|").split("|")]) for ln in block]
                rows = [(ln, cells) for ln, cells in rows if not all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in cells)]
                if rows:
                    header_ln, header = rows[0]
                    tables.append(Table(section=header_ln.section, header=header,
                                        rows=[c for _, c in rows[1:]], header_line=header_ln.no,
                                        row_lines=[ln.no for ln, _ in rows[1:]]))
            else:
                i += 1
        return tables

    def _build_index(self) -> None:
        parts, offsets, pos = [], [], 0
        for line in self.lines:
            offsets.append(pos)
            norm = normalize(line.text)
            parts.append(norm)
            pos += len(norm) + 1
        self._norm_text = " ".join(parts)
        self._offsets = offsets

    # --- доступ -----------------------------------------------------------------
    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    def line(self, no: int) -> Line | None:
        return self.lines[no - 1] if 1 <= no <= len(self.lines) else None

    def numbered(self) -> str:
        """Текст с номерами строк — так модель может ссылаться на точное место."""
        width = len(str(len(self.lines)))
        return "\n".join(f"L{line.no:0{width}d}| {line.text}" for line in self.lines)

    def display_section(self, section: tuple[str, ...]) -> str:
        parts = list(section)
        # Заголовок всего документа (единственный H1) не повторяем в каждой ссылке
        if len(parts) > 1 and parts[0] == self.title:
            parts = parts[1:]
        return " › ".join(parts) if parts else "(начало документа)"

    def location(self, line_no: int | None, line_end: int | None = None) -> str:
        line = self.line(line_no) if line_no else None
        if not line:
            return "весь документ"
        where = self.display_section(line.section)
        rng = f"строка {line_no}" if not line_end or line_end == line_no else f"строки {line_no}–{line_end}"
        page = f" (страница {line.page})" if line.page else ""
        return f"{where} · {rng}{page}"

    def section_text(self, section: Section, own_only: bool = False) -> list[Line]:
        out = []
        for line in self.lines[section.line_start: section.line_end]:
            if own_only and _HEADING.match(line.text):
                break
            out.append(line)
        return out

    # --- поиск цитат --------------------------------------------------------------
    def _line_at(self, offset: int) -> int:
        return bisect.bisect_right(self._offsets, offset)

    def find(self, quote: str, hint_line: int | None = None, min_score: float = 87.0) -> Match | None:
        """Находит цитату в документе: точно, по фрагментам (через «...») или нечётко."""
        q = normalize(quote)
        if len(q) < 4:
            return None
        pos = self._norm_text.find(q)
        if pos != -1:
            # если цитата встречается несколько раз — берём ближайшую к подсказке
            if hint_line:
                best = pos
                while pos != -1:
                    if abs(self._line_at(pos) - hint_line) < abs(self._line_at(best) - hint_line):
                        best = pos
                    pos = self._norm_text.find(q, pos + 1)
                pos = best
            return Match(self._line_at(pos), self._line_at(pos + len(q) - 1), 100.0, True)
        # цитата с пропусками: «начало ... конец»
        fragments = [f.strip() for f in re.split(r"\.\.\.|\[\.\.\.\]", q) if len(f.strip()) >= 10]
        if len(fragments) >= 2:
            matches = [self.find(f, hint_line, min_score) for f in fragments]
            if all(matches):
                return Match(min(m.line_start for m in matches), max(m.line_end for m in matches),
                             min(m.score for m in matches), all(m.exact for m in matches))
        res = fuzz.partial_ratio_alignment(q, self._norm_text, score_cutoff=min_score)
        if res is not None:
            return Match(self._line_at(res.dest_start), self._line_at(max(res.dest_start, res.dest_end - 1)),
                         float(res.score), False)
        return None

    def excerpt(self, line_start: int, line_end: int | None = None, limit: int = 400) -> str:
        """Чистый текст строк (без разметки) — как цитата для отчёта."""
        end = line_end or line_start
        parts = [clean_md(self.lines[i - 1].text) for i in range(line_start, end + 1) if self.line(i)]
        text = " ".join(p for p in parts if p)
        return text if len(text) <= limit else text[: limit - 1] + "…"
