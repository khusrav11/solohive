"""Сохранение отчёта: Markdown (основной), JSON (машиночитаемый), HTML (для просмотра)."""

from __future__ import annotations

import json
from pathlib import Path

from ..models import Report
from .markdown import render


def write_report(report: Report, output: str | Path, also_json: bool = True) -> list[Path]:
    out = Path(output).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    ext = out.suffix.lower()
    if ext == ".json":
        out.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return [out]
    md = render(report)
    if ext in (".html", ".htm"):
        out.write_text(_to_html(md, report.passport.document_title), encoding="utf-8")
    else:
        out.write_text(md, encoding="utf-8")
    written.append(out)
    if also_json:
        js = out.with_suffix(".json")
        js.write_text(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(js)
    return written


def _to_html(md: str, title: str) -> str:
    try:
        import markdown as mdlib
        body = mdlib.markdown(md, extensions=["tables", "toc", "sane_lists"])
    except ImportError:  # pragma: no cover
        import html
        body = f"<pre>{html.escape(md)}</pre>"
    return f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<title>Отчёт SoloHive — {title}</title>
<style>
body{{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:1100px;margin:32px auto;padding:0 20px;
line-height:1.5;color:#1f2328}} table{{border-collapse:collapse;margin:12px 0;font-size:14px}}
th,td{{border:1px solid #d0d7de;padding:5px 8px;vertical-align:top}} th{{background:#f6f8fa}}
blockquote{{border-left:4px solid #f0b400;margin:8px 0;padding:4px 12px;background:#fffbea}}
code{{background:#f6f8fa;padding:1px 4px;border-radius:4px}} h1{{border-bottom:2px solid #f0b400}}
</style></head><body>{body}</body></html>"""
