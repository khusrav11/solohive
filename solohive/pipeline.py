"""Оркестратор роя SoloHive.

Граф выполнения (стрелка — зависимость, «∥» — параллельно):

    Загрузчик → Инспектор (правила, мгновенно)
              → [ Нарезчик ∥ Системные аналитики ×2 ∥ Сценарист ]   (знают находки правил)
              → Рецензенты (пакеты параллельно)
              → Привязка цитат + дедупликация → Критик → Судья (вердикт по правилам)
              → Интервьюер (вопросы + обоснование) → Отчёт
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path

from . import APP_NAME
from .agents.analyst import analyze_set
from .agents.critic import deterministic_dedupe, verify
from .agents.interviewer import STATUS_RU, fallback_questions, interview
from .agents.common import rule_hints
from .agents.reviewer import review_all
from .agents.scenarist import play_scenarios
from .agents.segmenter import segment
from .config import Settings
from .linters import RULES, run_linters
from .llm import create_llm
from .llm.base import BaseLLM
from .loaders import load_document
from .models import Finding, Passport, Report, Requirement, Severity, Verdict
from .progress import Progress
from .textutil import findings_n, nform
from .version import agent_commit, agent_version

TEST_BLOCKERS = {"verifiable", "validatable", "consistent", "set_consistent", "unambiguous", "complete",
                 "set_complete"}


async def run_pipeline(input_path: str | Path, settings: Settings, llm: BaseLLM | None = None,
                       progress: Progress | None = None) -> Report:
    progress = progress or Progress(enabled=False)
    started = datetime.now().astimezone()
    t0 = time.monotonic()
    times: dict[str, float] = {}
    warnings: list[str] = []

    doc = load_document(input_path)
    progress.step("📄", "Загрузчик", f"{doc.path.name} ({doc.fmt}): {nform(len(doc.lines), 'строка', 'строки', 'строк')}, "
                                     f"{nform(len(doc.sections), 'раздел', 'раздела', 'разделов')}, "
                                     f"{nform(len(doc.tables), 'таблица', 'таблицы', 'таблиц')}")
    own_llm = llm is None
    llm = llm or create_llm(settings)
    if progress.enabled:
        llm.on_wait = progress.note
    try:
        # --- Раунд 1: правила ∥ нарезка ∥ системный анализ ------------------------------------------------
        async def timed(name, coro):
            s = time.monotonic()
            res = await coro
            times[name] = round(time.monotonic() - s, 2)
            return res

        s = time.monotonic()
        lint = run_linters(doc)
        times["Инспектор (правила)"] = round(time.monotonic() - s, 3)
        progress.step("🔍", "Инспектор (правила)", f"{findings_n(len([f for f in lint if f.status == 'active']))} "
                                                  f"по {len(RULES)} правилам, без LLM")
        hints = rule_hints(lint)
        seg_task = asyncio.create_task(timed("Нарезчик", segment(doc, llm)))
        set_task = asyncio.create_task(timed("Системные аналитики", analyze_set(doc, llm, hints)))
        scen_task = asyncio.create_task(timed("Сценарист", play_scenarios(doc, llm, hints)))

        reqs, w = await seg_task
        warnings += w
        progress.step("✂️ ", "Нарезчик", f"{nform(len(reqs), 'требование', 'требования', 'требований')} с точными цитатами")

        # --- Раунд 2: рецензенты (пакетами параллельно), системный анализ продолжает работать ---------------
        review_task = asyncio.create_task(timed("Рецензенты", review_all(doc, reqs, llm, settings.review_batch_size,
                                                                         hints)))
        set_evals, set_findings, set_q, w = await set_task
        warnings += w
        progress.step("🧭", "Системные аналитики", f"{findings_n(len(set_findings))} по набору требований")
        scenarios, scen_findings, w = await scen_task
        warnings += w
        gaps = sum(1 for x in scenarios if x.verdict != "defined")
        progress.step("🎭", "Сценарист", f"{nform(len(scenarios), 'сценарий', 'сценария', 'сценариев')} проиграно, "
                                        f"не определено или противоречиво: {gaps}")
        reviews, rev_findings, rev_q, w = await review_task
        warnings += w
        n_batches = -(-len(reqs) // settings.review_batch_size)
        progress.step("🔬", "Рецензенты", f"проверено {len(reviews)} из {len(reqs)} требований "
                                         f"({nform(n_batches, 'пакет', 'пакета', 'пакетов')} параллельно), "
                                         f"{findings_n(len(rev_findings))}")

        # --- Сборка, привязка, дедупликация ------------------------------------------------------------------
        findings = lint + rev_findings + set_findings + scen_findings
        unverified = _attach(findings, reqs)
        for i, f in enumerate(findings, start=1):
            f.fid = f"F-{i:03d}"
        raw_total = len([f for f in findings if f.status == "active"]) + unverified
        auto_merged = deterministic_dedupe(findings)

        # --- Раунд 3: критик ----------------------------------------------------------------------------------
        s = time.monotonic()
        dropped, merged, w = await verify(doc, findings, llm)
        times["Критик"] = round(time.monotonic() - s, 2)
        warnings += w
        progress.step("⚖️ ", "Критик", f"отклонено ложных: {dropped}, объединено дубликатов: {merged + auto_merged}, "
                                      f"отброшено без цитаты: {unverified}")

        # --- Судья (правила) + интервьюер (LLM) ---------------------------------------------------------------
        verdict = compute_verdict(reqs, findings)
        s = time.monotonic()
        questions, verdict, w = await interview(doc, reqs, findings, verdict, rev_q + set_q, llm)
        times["Интервьюер и судья"] = round(time.monotonic() - s, 2)
        warnings += w
        if not questions:
            questions = fallback_questions(set_q + rev_q)
        progress.step("💬", "Интервьюер и судья", f"{nform(len(questions), 'уточняющий вопрос', 'уточняющих вопроса', 'уточняющих вопросов')}; "
                                                 f"разработка: {STATUS_RU[verdict.dev_status]}")
        _renumber(findings, questions, verdict)
        _link_scenarios(scenarios, findings)

        warnings += llm.summary_warnings()
        if settings.provider == "offline":
            warnings = ["Офлайн-режим: LLM не использовалась. Отчёт построен на детерминированных правилах и "
                        "нарезке по структуре документа; смысловые проверки, вопросы и обоснование вердикта недоступны."]
        calls = llm.calls
        passport = Passport(
            document_title=doc.title, document_file=doc.path.name, document_format=doc.fmt,
            document_sha256=doc.sha256, document_lines=len(doc.lines),
            run_started=started.strftime("%Y-%m-%d %H:%M:%S %Z (UTC%z)"),
            duration_sec=round(time.monotonic() - t0, 1), provider=settings.provider,
            models=list(llm.used_models) or [settings.model], agent_name=APP_NAME,
            agent_version=agent_version(), agent_commit=agent_commit(), temperature=settings.temperature,
            llm_calls=len([c for c in calls if c.ok]), llm_cached_calls=len([c for c in calls if c.cached]),
            input_tokens=sum(c.input_tokens for c in calls), output_tokens=sum(c.output_tokens for c in calls),
        )
        report = Report(passport=passport, requirements=reqs, reviews=reviews, findings=findings,
                        set_evals=set_evals, scenarios=scenarios, questions=questions, verdict=verdict, calls=calls,
                        stage_times=times, warnings=warnings, source_lines=[ln.text for ln in doc.lines])
        report.stage_times["raw_findings"] = raw_total
        return report
    finally:
        if own_llm:
            await llm.aclose()


def _attach(findings: list[Finding], reqs: list[Requirement]) -> int:
    """Привязывает находки к требованиям по строкам доказательств; неподтверждённые цитатами — убирает."""
    unverified = 0
    for f in findings:
        if f.status != "active":
            continue
        verified = [e for e in f.evidence if e.verified]
        if not verified:
            f.status = "dropped"
            f.note = "не подтверждено цитатой из документа (возможная галлюцинация модели)"
            unverified += 1
            continue
        # подтверждённые доказательства — первыми
        f.evidence = verified + [e for e in f.evidence if not e.verified]
        lines = [ln for e in verified for ln in range(e.line or 0, (e.line_end or e.line or 0) + 1) if ln]
        for r in reqs:
            if r.line_start is None:
                continue
            if any(r.line_start <= ln <= (r.line_end or r.line_start) for ln in lines) and r.rid not in f.rids:
                f.rids.append(r.rid)
        if f.scope == "requirement" and not f.rids:
            f.scope = "set"
    return unverified


def _link_scenarios(scenarios, findings: list[Finding]) -> None:
    by_scen = {f.scenario: f for f in findings if f.scenario}
    for sc in scenarios:
        f = by_scen.get(sc.sid)
        if f is None:
            continue
        if f.status == "active":
            sc.finding = f.fid
        elif f.status == "merged" and f.note.startswith("объединено с "):
            sc.finding = f.note.split()[2].rstrip(":")


def compute_verdict(reqs: list[Requirement], findings: list[Finding]) -> Verdict:
    """Детерминированный вердикт: одинаковые находки → одинаковое решение."""
    act = [f for f in findings if f.status == "active"]
    crit = [f for f in act if f.severity == Severity.CRITICAL]
    major = [f for f in act if f.severity == Severity.MAJOR]
    dev = "no" if crit else ("conditional" if major else "yes")
    if any(f.criterion in TEST_BLOCKERS for f in crit):
        test = "no"
    elif crit or major:
        test = "conditional"
    else:
        test = "yes"
    serious = {rid for f in act if f.severity != Severity.MINOR for rid in f.rids}
    clean = [r for r in reqs if r.rid not in serious]
    score = round(100 * len(clean) / len(reqs)) if reqs else 0
    return Verdict(dev_status=dev, test_status=test, score=score)


def _renumber(findings: list[Finding], questions, verdict: Verdict) -> None:
    """Итоговая нумерация D-01…: по критичности, затем по месту в документе."""
    active = [f for f in findings if f.status == "active"]
    active.sort(key=lambda f: (f.severity.rank, 0 if f.scope == "requirement" else 1,
                               min((e.line or 10 ** 6) for e in f.evidence) if f.evidence else 10 ** 6))
    mapping: dict[str, str] = {}
    for i, f in enumerate(active, start=1):
        mapping[f.fid] = f"D-{i:02d}"
    for f in findings:
        if f.status == "merged" and f.note.startswith("объединено с "):
            target = f.note.split()[2].rstrip(":")
            f.note = f.note.replace(target, mapping.get(target, target), 1)
    rest = [f for f in findings if f.status != "active"]
    for j, f in enumerate(rest, start=1):
        mapping[f.fid] = f"X-{j:02d}"
    for f in findings:
        f.fid = mapping.get(f.fid, f.fid)
    for q in questions:
        q.related = [mapping.get(x, x) for x in q.related if mapping.get(x, "").startswith("D-")]
    for p in verdict.priorities:
        p.fids = [mapping.get(x, x) for x in p.fids if mapping.get(x, "").startswith("D-")]
    findings.sort(key=lambda f: (f.status != "active", f.fid))
