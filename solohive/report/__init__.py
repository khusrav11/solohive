"""Сохранение отчёта: Markdown, PDF или DOCX (по расширению файла), рядом — интерактивный HTML и JSON."""

from __future__ import annotations

import json
from pathlib import Path

from ..models import Report
from .html import render_html
from .markdown import render

DOC_FORMATS = (".md", ".pdf", ".docx")
# формат отчёта по умолчанию — как у входного документа
_BY_INPUT = {".pdf": ".pdf", ".docx": ".docx", ".md": ".md", ".markdown": ".md", ".txt": ".md",
             ".html": ".html", ".htm": ".html"}


def default_report_ext(input_path: str | Path) -> str:
    return _BY_INPUT.get(Path(input_path).suffix.lower(), ".md")


def write_document(md: str, path: str | Path, title: str) -> Path:
    """Markdown-текст → файл в формате по расширению: .pdf, .docx или .md."""
    out = Path(path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    ext = out.suffix.lower()
    if ext == ".pdf":
        from .pdf import render_pdf
        return render_pdf(md, out, title)
    if ext == ".docx":
        from .word import render_docx
        return render_docx(md, out, title)
    out.write_text(md, encoding="utf-8")
    return out


def write_report(report: Report, output: str | Path, also_json: bool = True, also_html: bool = True) -> list[Path]:
    """Пишет отчёт в формат по расширению output (.md, .pdf, .docx, .html, .json); рядом кладёт .md, .html, .json."""
    out = Path(output).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    ext = out.suffix.lower()
    written: list[Path] = []
    if ext == ".json":
        _write_json(report, out)
        return [out]
    title = f"Отчёт о проверке требований — {report.passport.document_title}"
    md = render(report)
    if ext in (".html", ".htm"):
        out.write_text(render_html(report), encoding="utf-8")
        written.append(out)
    else:
        written.append(write_document(md, out, title))
        if ext in (".pdf", ".docx"):
            written.append(write_document(md, out.with_suffix(".md"), title))
        if also_html:
            h = out.with_suffix(".html")
            h.write_text(render_html(report), encoding="utf-8")
            written.append(h)
    if also_json:
        js = out.with_suffix(".json")
        _write_json(report, js)
        written.append(js)
    return written


def _write_json(report: Report, path: Path) -> None:
    path.write_text(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
