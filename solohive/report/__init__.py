"""Сохранение отчёта: Markdown (основной файл по ТЗ), интерактивный HTML и машиночитаемый JSON."""

from __future__ import annotations

import json
from pathlib import Path

from ..models import Report
from .html import render_html
from .markdown import render


def write_report(report: Report, output: str | Path, also_json: bool = True, also_html: bool = True) -> list[Path]:
    """Пишет отчёт в формат по расширению output; рядом кладёт .html и .json (если не отключено)."""
    out = Path(output).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    ext = out.suffix.lower()
    written: list[Path] = []
    if ext == ".json":
        _write_json(report, out)
        return [out]
    if ext in (".html", ".htm"):
        out.write_text(render_html(report), encoding="utf-8")
        written.append(out)
    else:
        out.write_text(render(report), encoding="utf-8")
        written.append(out)
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
