"""Перевыпуск готового отчёта в другом формате без нового прогона LLM.

    python -m solohive.render reports/report.json reports/report.pdf
    python -m solohive.render reports/report.json reports/report.docx

Содержание берётся из JSON прогона как есть: меняется только формат файла.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .models import Report
from .report import DOC_FORMATS, write_document, write_report


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print(__doc__.strip())
        return 2
    src, out = Path(args[0]), Path(args[1])
    report = Report.model_validate(json.loads(src.read_text(encoding="utf-8")))
    paths = write_report(report, out, also_json=False)
    if report.clarification is not None and report.clarification.revised and out.suffix.lower() in DOC_FORMATS:
        from .agents.reviser import revised_document
        title = f"{report.passport.document_title} — исправленная редакция требований"
        paths.append(write_document(revised_document(report), out.with_name(out.stem + "_revised" + out.suffix), title))
    print("Готово: " + ", ".join(str(p) for p in paths))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
