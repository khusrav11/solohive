"""Загрузка входного документа: PDF, Markdown, TXT, DOCX, HTML → единый markdown с номерами строк."""

from __future__ import annotations

import hashlib
import re
import statistics
from collections import Counter
from pathlib import Path

from .document import SourceDocument

SUPPORTED = {".pdf": "pdf", ".md": "markdown", ".markdown": "markdown", ".txt": "text",
             ".docx": "docx", ".html": "html", ".htm": "html"}


class LoadError(RuntimeError):
    pass


def load_document(path: str | Path) -> SourceDocument:
    p = Path(path).expanduser()
    if not p.exists():
        raise LoadError(f"Файл не найден: {p}")
    ext = p.suffix.lower()
    fmt = SUPPORTED.get(ext)
    if fmt is None:
        raise LoadError(f"Формат {ext or '(без расширения)'} не поддерживается. Поддерживаются: "
                        + ", ".join(sorted(SUPPORTED)))
    data = p.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    pages = None
    if fmt == "markdown":
        markdown = _decode(data)
    elif fmt == "text":
        markdown = promote_headings(_decode(data))
    elif fmt == "pdf":
        markdown, pages = _pdf_to_markdown(p)
    elif fmt == "docx":
        markdown = _docx_to_markdown(p)
    else:
        markdown = _html_to_markdown(_decode(data))
    if not markdown.strip():
        raise LoadError(f"Не удалось извлечь текст из {p.name}: документ пуст или это скан без текстового слоя.")
    return SourceDocument.from_markdown(p, fmt, sha, markdown, pages)


_NUM_HEADING = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(\S.{1,90})$")


def _looks_caps(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 4 and sum(c.isupper() for c in letters) / len(letters) > 0.85


def promote_headings(text: str) -> str:
    """Распознаёт заголовки в тексте без разметки: «2. ФУНКЦИОНАЛЬНЫЕ ТРЕБОВАНИЯ», «3.1 Логика», строки КАПСОМ."""
    out = []
    seen_title = False
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith(("#", "|", "*", "-", "`")) or line.endswith((".", ",", ";", ":")):
            out.append(raw)
            continue
        m = _NUM_HEADING.match(line)
        if m and (_looks_caps(m.group(2)) or len(line) <= 60):
            level = min(6, m.group(1).count(".") + 2)
            out.append("#" * level + " " + line)
        elif _looks_caps(line) and len(line) <= 120:
            out.append(("## " if seen_title else "# ") + line)
            seen_title = True
        else:
            out.append(raw)
    return "\n".join(out)


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


# --- PDF ------------------------------------------------------------------------------------------

def _pdf_to_markdown(path: Path) -> tuple[str, list[int | None]]:
    """PDF → markdown: заголовки по размеру/жирности шрифта, таблицы через pdfplumber, номера страниц."""
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover
        raise LoadError("Для PDF нужен пакет pdfplumber: pip install -r requirements.txt") from exc

    out: list[str] = []
    pages: list[int | None] = []

    def emit(text: str, page: int) -> None:
        for part in text.split("\n"):
            out.append(part)
            pages.append(page)

    with pdfplumber.open(path) as pdf:
        sizes = Counter(round(c["size"], 1) for pg in pdf.pages for c in pg.chars if c["text"].strip())
        if not sizes:
            return "", []
        body = sizes.most_common(1)[0][0]
        heading_sizes = sorted({s for s in sizes if s >= body * 1.12}, reverse=True)[:4]
        level_of = {s: i + 1 for i, s in enumerate(heading_sizes)}

        for pno, page in enumerate(pdf.pages, start=1):
            items: list[tuple[float, str]] = []
            tables = page.find_tables()
            boxes = [t.bbox for t in tables]
            for t in tables:
                rows = [[_cell(c) for c in row] for row in (t.extract() or []) if row]
                if rows:
                    md = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
                    md += ["| " + " | ".join(r) + " |" for r in rows[1:]]
                    items.append((t.bbox[1], "\n".join(md)))
            for tl in page.extract_text_lines(return_chars=True, strip=True):
                if any(b[0] - 1 <= tl["x0"] and tl["top"] >= b[1] - 1 and tl["bottom"] <= b[3] + 1 and tl["x1"] <= b[2] + 1
                       for b in boxes):
                    continue
                chars = [c for c in tl["chars"] if c["text"].strip()]
                if not chars:
                    continue
                size = round(statistics.median(c["size"] for c in chars), 1)
                bold = sum("bold" in c.get("fontname", "").lower() for c in chars) / len(chars) > 0.85
                text = tl["text"].strip()
                text = re.sub(r"^[•●▪◦‣∙]\s*", "* ", text)
                if size in level_of and len(text) < 160:
                    text = "#" * min(6, level_of[size]) + " " + text
                elif bold and len(text) < 90 and not text.endswith((".", ",", ";")) and not text.startswith("*"):
                    text = "#" * min(6, len(heading_sizes) + 1) + " " + text
                items.append((tl["top"], text))
            for _, text in sorted(items, key=lambda x: x[0]):
                emit(text, pno)
            emit("", pno)
    return "\n".join(out), pages


def _cell(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).replace("|", "/").strip()


# --- DOCX -----------------------------------------------------------------------------------------

def _docx_to_markdown(path: Path) -> str:
    try:
        from docx import Document
        from docx.table import Table as DocxTable
        from docx.text.paragraph import Paragraph
    except ImportError as exc:  # pragma: no cover
        raise LoadError("Для DOCX нужен пакет python-docx: pip install -r requirements.txt") from exc

    d = Document(str(path))
    out: list[str] = []
    for child in d.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, d)
            text = p.text.strip()
            if not text:
                out.append("")
                continue
            style = (p.style.name if p.style is not None else "").lower()
            m = re.search(r"(heading|заголовок)\s*(\d)", style)
            if style in ("title", "название", "заголовок"):
                out.append(f"# {text}")
            elif m:
                out.append("#" * int(m.group(2)) + " " + text)
            elif "list" in style or "спис" in style or child.find(".//{*}numPr") is not None:
                out.append(f"* {text}")
            elif _all_bold(p) and len(text) < 100 and not text.endswith((".", ",", ";", ":")):
                out.append(promote_headings(text) if _NUM_HEADING.match(text) else f"### {text}")
            else:
                out.append(promote_headings(text))
        elif tag == "tbl":
            t = DocxTable(child, d)
            rows = [[_cell(c.text) for c in r.cells] for r in t.rows]
            if rows:
                out.append("| " + " | ".join(rows[0]) + " |")
                out.append("|" + "---|" * len(rows[0]))
                out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
            out.append("")
    return "\n".join(out)


def _all_bold(p) -> bool:
    runs = [r for r in p.runs if r.text.strip()]
    return bool(runs) and all(r.bold for r in runs)


# --- HTML -----------------------------------------------------------------------------------------

def _html_to_markdown(html: str) -> str:
    try:
        from markdownify import markdownify
    except ImportError:  # грубый запасной вариант
        return re.sub(r"<[^>]+>", " ", html)
    return markdownify(html, heading_style="ATX")
