"""Командная строка: один запуск — один отчёт.

    python -m solohive --input docs/requirements.pdf --output reports/report.md
    python -m solohive docs/requirements.md reports/report.md
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from . import APP_NAME, __version__
from .agents.interviewer import STATUS_RU
from .config import ConfigError, Settings
from .llm.base import LLMError
from .loaders import SUPPORTED, LoadError
from .models import Severity
from .pipeline import run_pipeline
from .progress import Progress
from .report import write_report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m solohive",
        description=f"{APP_NAME}: статическая проверка документа с требованиями (ISO/IEC/IEEE 29148, IEEE 830).",
    )
    p.add_argument("input_pos", nargs="?", metavar="INPUT", help="файл требований (" + ", ".join(sorted(SUPPORTED)) + ")")
    p.add_argument("output_pos", nargs="?", metavar="OUTPUT", help="файл отчёта (.md, .html или .json)")
    p.add_argument("-i", "--input", help="путь к файлу требований")
    p.add_argument("-o", "--output", help="путь к файлу отчёта (по умолчанию reports/<имя>_report.md)")
    p.add_argument("--provider", choices=["gemini", "openai", "anthropic", "ollama"], help="провайдер LLM (иначе из .env)")
    p.add_argument("--model", help="модель LLM (иначе из .env); отключает автозамену модели")
    p.add_argument("--batch-size", type=int, help="требований в одном запросе рецензента (по умолчанию 6)")
    p.add_argument("--fast", action="store_true",
                   help="быстрый режим: короткие рассуждения модели и крупные пакеты (быстрее, чуть менее глубоко)")
    p.add_argument("--offline", action="store_true",
                   help="без LLM: только детерминированные правила (если нет ключа/сети или для быстрой проверки)")
    p.add_argument("--no-cache", action="store_true", help="не использовать кэш ответов LLM")
    p.add_argument("--no-json", action="store_true", help="не сохранять машиночитаемый report.json рядом с отчётом")
    p.add_argument("-q", "--quiet", action="store_true", help="без вывода хода работы")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    src = args.input or args.input_pos
    if not src:
        build_parser().print_help()
        return 2
    out = args.output or args.output_pos or str(Path("reports") / f"{Path(src).stem}_report.md")
    progress = Progress(enabled=not args.quiet)
    try:
        settings = Settings.from_env(provider="offline" if args.offline else args.provider, model=args.model,
                                     use_cache=not args.no_cache)
        if args.fast:
            settings.thinking_level = settings.thinking_level or "low"
            settings.review_batch_size = 8
        if args.batch_size:
            settings.review_batch_size = max(1, args.batch_size)
    except ConfigError as exc:
        print(f"❌ Ошибка конфигурации: {exc}", file=sys.stderr)
        return 1

    progress.print(f"\n🐝 [bold]{APP_NAME}[/bold] v{__version__} — рой агентов проверки требований")
    progress.print(f"   Вход: [cyan]{src}[/cyan] · Модель: [cyan]{settings.model}[/cyan] ({settings.provider})"
                   + (f", запасные: {', '.join(settings.fallback_models)}" if settings.fallback_models else ""))
    progress.print("")
    try:
        report = asyncio.run(run_pipeline(src, settings, progress=progress))
    except LoadError as exc:
        print(f"❌ Не удалось прочитать документ: {exc}", file=sys.stderr)
        return 2
    except LLMError as exc:
        print(f"❌ Ошибка LLM: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\nПрервано пользователем.", file=sys.stderr)
        return 130

    paths = write_report(report, out, also_json=not args.no_json)
    act = report.active_findings
    sev = {s: sum(1 for f in act if f.severity == s) for s in Severity}
    v = report.verdict
    progress.print("")
    progress.print(f"  ✅ [bold green]Отчёт готов[/bold green] за {report.passport.duration_sec} с: "
                   + ", ".join(f"[cyan]{p}[/cyan]" for p in paths))
    progress.print(f"     Требований: {len(report.requirements)} · Нарушений: {len(act)} "
                   f"(🔴 {sev[Severity.CRITICAL]} · 🟡 {sev[Severity.MAJOR]} · 🟢 {sev[Severity.MINOR]}) · "
                   f"Вопросов: {len(report.questions)}")
    progress.print(f"     Разработка: [bold]{STATUS_RU[v.dev_status]}[/bold] · Тестирование: "
                   f"[bold]{STATUS_RU[v.test_status]}[/bold]")
    for w in report.warnings:
        progress.warn(w)
    progress.print("")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
