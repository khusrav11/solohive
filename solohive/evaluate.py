"""Оценка полноты отчёта по контрольному списку эксперта.

    python -m solohive.evaluate reports/requirements_report.json eval/gold_defects.yaml
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def _load_yaml(path: Path) -> list[dict]:
    try:
        import yaml  # type: ignore
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except ImportError:  # минимальный разбор нашего простого формата
        items, cur = [], None
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split(" #")[0].rstrip()
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if line.startswith("- "):
                cur = {}
                items.append(cur)
                line = line[2:]
            key, _, val = line.strip().partition(":")
            val = val.strip()
            if val.startswith("["):
                val = [v.strip().strip('"') for v in val.strip("[]").split(",") if v.strip()]
            cur[key] = val
        for it in items:
            it["lines"] = [int(x) for x in it.get("lines", [])]
        return items


def _line_mapper(report: dict, reference: Path | None):
    """Строки эталона (Markdown) → строки документа отчёта (например, PDF) по тексту, а не по номеру."""
    if reference is None or not reference.exists() or not report.get("source_lines"):
        return lambda n: {n}
    from .document import SourceDocument, normalize
    from .loaders import load_document

    ref = load_document(reference)
    target = SourceDocument.from_markdown(Path("report"), "markdown", "", "\n".join(report["source_lines"]))
    if [normalize(x.text) for x in ref.lines] == [normalize(x) for x in report["source_lines"]]:
        return lambda n: {n}
    cache: dict[int, set[int]] = {}

    def mapper(n: int) -> set[int]:
        if n not in cache:
            line = ref.line(n)
            text = normalize(line.text) if line else ""
            m = target.find(text[:120]) if len(text) >= 6 else None
            cache[n] = set(range(m.line_start, m.line_end + 1)) if m else set()
        return cache[n]
    return mapper


def evaluate(report_json: Path, gold_path: Path, reference: Path | None = None) -> dict:
    report = json.loads(report_json.read_text(encoding="utf-8"))
    gold = _load_yaml(gold_path)
    active = [f for f in report["findings"] if f["status"] == "active"]
    to_target = _line_mapper(report, reference)
    found, missed = [], []
    for g in gold:
        lines = set().union(*(to_target(int(x)) for x in g["lines"])) if g["lines"] else set()
        kws = [k.lower() for k in g["keywords"]]
        hit = None
        for f in active:
            ev_lines = {ln for e in f["evidence"] if e.get("verified") and e.get("line")
                        for ln in range(e["line"], (e.get("line_end") or e["line"]) + 1)}
            text = " ".join([f["title"], f.get("explanation", "")]).lower()
            if ev_lines & lines and any(k in text for k in kws):
                hit = f
                break
        (found if hit else missed).append((g, hit))
    recall = len(found) / len(gold) if gold else 0.0
    return {"recall": recall, "found": found, "missed": missed, "total_active": len(active)}


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    report = Path(argv[0])
    root = Path(__file__).resolve().parents[1]
    gold = Path(argv[1]) if len(argv) > 1 else root / "eval" / "gold_defects.yaml"
    res = evaluate(report, gold, reference=root / "examples" / "requirements.md")
    print(f"Полнота (recall) по контрольному списку: {res['recall']:.0%} "
          f"({len(res['found'])}/{len(res['found']) + len(res['missed'])}); находок в отчёте: {res['total_active']}")
    for g, f in res["found"]:
        print(f"  ✅ {g['id']} {g['title'][:80]}  ←  {f['fid']}")
    for g, _ in res["missed"]:
        print(f"  ❌ {g['id']} {g['title'][:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
