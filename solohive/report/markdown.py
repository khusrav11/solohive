"""Отчёт в Markdown: «Кратко» на первой странице, затем структура по разделу 6 ТЗ + вопросы + журнал верификации."""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from .. import __version__
from .. import criteria as C
from ..agents.interviewer import STATUS_RU
from ..agents.segmenter import KIND_RU
from ..models import Finding, Report, Requirement, Severity, Status
from ..textutil import nform, plural
from .mdblocks import slug

VERDICT_ICON = {"no": "⛔", "conditional": "⚠️", "yes": "✅"}
SEV_WORD = {Severity.CRITICAL: ("критичный", "критичных", "критичных"),
            Severity.MAJOR: ("существенный", "существенных", "существенных"),
            Severity.MINOR: ("замечание", "замечания", "замечаний")}
SEV_NAME = {Severity.CRITICAL: "критичный", Severity.MAJOR: "существенный", Severity.MINOR: "замечание"}
RES_ICON = {"resolved": "✅ устранён", "partial": "◐ частично", "open": "⏳ открыт"}
SET_SECTION = "4.2. Системные дефекты набора"
ADDRESSEE_ORDER = ["Бизнес / Product owner", "Системный аналитик", "Архитектор", "Информационная безопасность",
                   "Тестирование"]


def _cut(t: str, limit: int) -> str:
    """Обрезка по границе слова: «…» вместо оборванного слова."""
    if not limit or len(t) <= limit:
        return t
    head = t[: limit - 1]
    word = head.rsplit(" ", 1)[0]
    return (word if len(word) >= limit * 0.6 else head).rstrip(" ,;:—-") + "…"


def cell(text: str | None, limit: int = 0) -> str:
    t = _cut(" ".join((text or "").split()), limit).replace("|", "\\|")
    return t or "—"


def first_sentence(text: str | None, limit: int = 200) -> str:
    """Первое законченное предложение (если оно не слишком короткое) — для сжатых таблиц."""
    t = " ".join((text or "").split())
    m = re.search(r"(?<=[.!?])\s+(?=[A-ZА-ЯЁ«])", t[40:])
    if m:
        t = t[: 40 + m.start()]
    return cell(t, limit)


def q(text: str, limit: int = 0) -> str:
    """Цитата в «ёлочках» без markdown-разметки внутри."""
    t = _cut(" ".join((text or "").split()), limit).replace("*", "\\*").replace("_", "\\_")
    return f"«{t}»"


def short_loc(loc: str) -> str:
    """«Часть A › Цели · строка 16 (страница 1)» → «Цели, строка 16, стр. 1»."""
    where, _, rest = (loc or "").rpartition(" · ")
    rest = re.sub(r"\s*\(страница (\d+)\)", r", стр. \1", rest)
    last = where.split(" › ")[-1] if where else ""
    return f"{last}, {rest}" if last else (rest or "—")


def finding_anchor(f: Finding) -> str:
    """Куда ведёт ссылка на дефект: карточка требования или раздел системных дефектов набора."""
    if f.scope == "requirement" and f.rids:
        return "#" + f.rids[0].lower()
    return "#" + slug(SET_SECTION)


def _sev_phrase(cnt: Counter) -> str:
    parts = [nform(cnt[s], *SEV_WORD[s]) for s in Severity if cnt[s]]
    if not parts:
        return "нет"
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " и " + parts[-1]


def _sev_cell(total: int, cnt: Counter) -> str:
    return (f"{total} (критичных — {cnt[Severity.CRITICAL]}, существенных — {cnt[Severity.MAJOR]}, "
            f"замечаний — {cnt[Severity.MINOR]})")


def _resolved_ids(report: Report) -> set[str]:
    cl = report.clarification
    return {r.fid for r in cl.resolutions if r.status == "resolved"} if cl else set()


def render_brief(report: Report) -> list[str]:
    """Раздел «Кратко»: вывод, вердикт до/после уточнения, блокеры, что осталось — всё из данных прогона, без LLM."""
    V, cl, P = report.verdict, report.clarification, report.passport
    act = report.active_findings
    by_sev = Counter(f.severity for f in act)
    crit = sorted((f for f in act if f.severity == Severity.CRITICAL), key=lambda f: f.fid)
    after = cl is not None and cl.verdict_after is not None
    res = {r.fid: r for r in cl.resolutions} if cl else {}
    resolved = _resolved_ids(report)
    open_cnt = Counter(f.severity for f in act if f.fid not in resolved)
    n_crit, n_major = by_sev[Severity.CRITICAL], by_sev[Severity.MAJOR]

    if V.dev_status == "no":
        lead = "В текущем виде документ нельзя отдавать в разработку" + (
            f": {nform(n_crit, 'критичный дефект', 'критичных дефекта', 'критичных дефектов')} "
            f"{plural(n_crit, 'блокирует', 'блокируют', 'блокируют')} реализацию и приёмку." if n_crit else ".")
    elif V.dev_status == "conditional":
        lead = (f"Документ можно отдавать в разработку после доуточнения: "
                f"{nform(n_major, 'существенный дефект', 'существенных дефекта', 'существенных дефектов')}, критичных нет.")
    else:
        lead = "Документ готов к разработке: " + ("найдены только замечания." if act else "дефектов не найдено.")
    tail = ""
    if after:
        va = cl.verdict_after
        crit_fixed = sum(1 for f in crit if f.fid in resolved)
        tail = (f"После ответов на {nform(len(cl.answers), 'вопрос', 'вопроса', 'вопросов')} устранено "
                f"{nform(len(resolved), 'дефект', 'дефекта', 'дефектов')}")
        if crit and crit_fixed == len(crit):
            tail += f", в том числе все {len(crit)} критичных" if len(crit) > 1 else ", в том числе единственный критичный"
        elif crit_fixed:
            tail += f", из них критичных — {crit_fixed} из {len(crit)}"
        rest = sum(open_cnt.values())
        if rest:
            tail += (f". Открытыми остаются {nform(rest, 'дефект', 'дефекта', 'дефектов')}: {_sev_phrase(open_cnt)} — "
                     + {"conditional": "разработку можно начинать, доуточнив их до спринта.",
                        "yes": "документ готов к разработке.",
                        "no": "блокеры остаются, разработку начинать нельзя."}[va.dev_status])
        else:
            tail += ". Открытых дефектов не осталось."
    L = ["## Кратко", "", f"**{lead}**" + (f" {tail}" if tail else ""), ""]

    if after:
        va = cl.verdict_after
        changed = sum(1 for r in cl.revised if not r.is_new)
        added = sum(1 for r in cl.revised if r.is_new)
        L += ["| | До уточнения | После уточнения |", "| :--- | :--- | :--- |",
              f"| Разработка | {VERDICT_ICON[V.dev_status]} {STATUS_RU[V.dev_status]} | "
              f"{VERDICT_ICON[va.dev_status]} {STATUS_RU[va.dev_status]} |",
              f"| Тестирование | {VERDICT_ICON[V.test_status]} {STATUS_RU[V.test_status]} | "
              f"{VERDICT_ICON[va.test_status]} {STATUS_RU[va.test_status]} |",
              f"| Открытые дефекты | {_sev_cell(len(act), by_sev)} | {_sev_cell(sum(open_cnt.values()), open_cnt)} |",
              f"| Требования | {len(report.requirements)} в документе | переписано: {changed}, добавлено: {added}, "
              f"новых критериев приёмки: {len(cl.acceptance)} |", ""]
    else:
        L += ["| | Вердикт |", "| :--- | :--- |",
              f"| Разработка | {VERDICT_ICON[V.dev_status]} {STATUS_RU[V.dev_status]} |",
              f"| Тестирование | {VERDICT_ICON[V.test_status]} {STATUS_RU[V.test_status]} |",
              f"| Дефекты | {_sev_cell(len(act), by_sev)} |",
              f"| Вопросы к бизнесу и аналитику | {len(report.questions)} — "
              f"[раздел 6](#{slug('6. Уточняющие вопросы к бизнесу и аналитику')}) |", ""]

    if crit:
        L += ["### Критичные дефекты (блокеры)", ""]
        if after and all(f.fid in resolved for f in crit):
            L += ["| Дефект | Суть и место в документе | Как устранён ответом |", "| :--- | :--- | :--- |"]
            for f in crit:
                where = short_loc(f.primary.location) if f.primary else "—"
                L.append(f"| [{f.fid}]({finding_anchor(f)}) | {cell(f.title, 110)} ({cell(where)}) | "
                         f"{first_sentence(res[f.fid].comment, 170)} |")
        elif after:
            L += ["| Дефект | Суть и место в документе | Статус | Что ответ изменил |", "| :--- | :--- | :--- | :--- |"]
            for f in crit:
                r = res.get(f.fid)
                where = short_loc(f.primary.location) if f.primary else "—"
                L.append(f"| [{f.fid}]({finding_anchor(f)}) | {cell(f.title, 110)} ({cell(where)}) | "
                         f"{RES_ICON[r.status] if r else RES_ICON['open']} | {first_sentence(r.comment, 150) if r else '—'} |")
        else:
            L += ["| Дефект | Суть и место в документе | Как исправить |", "| :--- | :--- | :--- |"]
            for f in crit:
                where = short_loc(f.primary.location) if f.primary else "—"
                L.append(f"| [{f.fid}]({finding_anchor(f)}) | {cell(f.title, 120)} ({cell(where)}) | "
                         f"{cell(f.recommendation, 200)} |")
        L.append("")

    if after:
        left = sorted((f for f in act if f.fid not in resolved and f.severity != Severity.MINOR),
                      key=lambda f: (f.severity.rank, -len(f.rids), f.fid))
        if left:
            L += ["### Что осталось доуточнить", ""]
            for i, f in enumerate(left[:3], start=1):
                L.append(f"{i}. [{f.fid}]({finding_anchor(f)}) — {cell(f.title, 120)}")
            more = sum(open_cnt.values()) - min(len(left), 3)
            L += ["", "Выше — открытые дефекты, затрагивающие больше всего требований"
                  + (f"; остальные {more} — в [разделе 8](#{slug('8. Уточнение: ответы на вопросы и пересмотр')})."
                     if more > 0 else "."), ""]
    else:
        L += ["### Что исправить в первую очередь", ""]
        if V.priorities:
            for i, pr in enumerate(V.priorities[:5], start=1):
                L.append(f"{i}. {cell(pr.action, 220)}" + (f" ({', '.join(pr.fids)})" if pr.fids else ""))
        else:
            for i, f in enumerate(sorted(act, key=lambda x: (x.severity.rank, x.fid))[:5], start=1):
                L.append(f"{i}. {cell(f.recommendation or f.title, 220)} ({f.fid})")
        L.append("")

    verified = sum(1 for f in act if any(e.verified for e in f.evidence))
    raw = int(report.stage_times.get("raw_findings", len(report.findings)))
    dropped = [f for f in report.findings if f.status == "dropped"]
    merged = sum(1 for f in report.findings if f.status == "merged")
    no_quote = sum(1 for f in dropped if "цитат" in f.note)
    critic = len(dropped) - no_quote
    funnel = [x for x in (
        f"{merged} {plural(merged, 'объединён', 'объединены', 'объединены')} как дубликаты" if merged else "",
        f"{no_quote} {plural(no_quote, 'отброшен', 'отброшены', 'отброшены')} без подтверждения цитатой" if no_quote else "",
        f"{critic} {plural(critic, 'отклонён критиком как ложный', 'отклонены критиком как ложные', 'отклонены критиком как ложные')}"
        if critic else "") if x]
    sec = float(P.duration_sec or 0)
    took = f"{sec:.0f} с" + (f" (≈{sec / 60:.1f} мин)".replace(".", ",") if sec >= 60 else "")
    if act:
        proof = (f"Каждая из {len(act)} находок подтверждена" if verified == len(act)
                 else f"{verified} из {len(act)} находок подтверждены")
        L += [f"*Как получено: рой LLM-агентов и детерминированные правила (ISO/IEC/IEEE 29148, IEEE 830), {took}. "
              f"{proof} дословной цитатой из документа"
              + (f"; из {raw} кандидатов {', '.join(funnel)}" if funnel else "") + ".*", ""]
    return L


def source_label(f: Finding) -> str:
    names = []
    for s in f.sources:
        if s.startswith("rule:"):
            names.append(f"правило {s[5:]}")
        elif s == "llm:reviewer":
            names.append("LLM-рецензент")
        elif s.startswith("llm:system"):
            names.append("LLM-системный аналитик")
        elif s == "llm:scenario":
            names.append("LLM-сценарист")
        else:
            names.append(s)
    return " + ".join(dict.fromkeys(names))


def render_finding(f: Finding, lines: list[str], indent: str = "") -> None:
    crit = C.name(f.criterion)
    lines.append(f"{indent}- {f.severity.icon} **{f.fid} · {f.severity.value} · {crit}** — {cell(f.title)}")
    p = indent + "  "
    ev = [e for e in f.evidence if e.verified]
    if ev:
        lines.append(f"{p}- **Где:** {ev[0].location}")
        lines.append(f"{p}- **Цитата:** {q(ev[0].quote, 400)}")
        for e in ev[1:4]:
            lines.append(f"{p}- **Связанное место:** {q(e.quote, 300)} — *{e.location}*")
    if f.explanation:
        lines.append(f"{p}- **Почему это проблема:** {cell(f.explanation)}")
    if f.recommendation:
        lines.append(f"{p}- **Как исправить:** {cell(f.recommendation)}")
    meta = f"Источник: {source_label(f)}"
    if len(f.rids) > 1:
        meta += f" · требования: {', '.join(f.rids)}"
    if f.note:
        meta += f" · {f.note}"
    lines.append(f"{p}- *{meta}*")


def effective_criteria(rv, own: list[Finding]) -> dict[str, tuple[Status, str]]:
    """Итоговая оценка по критериям: оценка рецензента, ужесточённая найденными дефектами.

    Одна функция для матрицы и карточки — чтобы они никогда не расходились.
    """
    base: dict[str, tuple[Status, str]] = {e.criterion: (e.status, e.comment) for e in rv.criteria} if rv else {}
    individual = {c.code for c in C.INDIVIDUAL}
    for f in sorted(own, key=lambda x: x.severity.rank):
        if f.criterion not in individual:
            continue
        cur, comment = base.get(f.criterion, (Status.NA, ""))
        worst = Status.FAIL if f.severity != Severity.MINOR else Status.WARN
        if cur in (Status.OK, Status.NA) or (cur == Status.WARN and worst == Status.FAIL):
            base[f.criterion] = (worst, f"{f.fid}: {f.title}")
    return base


def render(report: Report) -> str:
    P = report.passport
    act = report.active_findings
    by_sev = Counter(f.severity for f in act)
    req_by_id = {r.rid: r for r in report.requirements}
    primary: dict[str, list[Finding]] = defaultdict(list)
    secondary: dict[str, list[Finding]] = defaultdict(list)
    for f in act:
        if f.scope == "requirement" and f.rids:
            primary[f.rids[0]].append(f)
            for rid in f.rids[1:]:
                secondary[rid].append(f)
    set_findings = [f for f in act if f.scope == "set"]
    V = report.verdict
    L: list[str] = []

    # --- шапка -------------------------------------------------------------------------------------------
    L += [
        "# Отчёт о статической проверке требований",
        "",
        f"> **Документ:** {q(P.document_title)} · **Разработка:** {VERDICT_ICON[V.dev_status]} "
        f"{STATUS_RU[V.dev_status]} · **Тестирование:** {VERDICT_ICON[V.test_status]} {STATUS_RU[V.test_status]}  ",
        f"> **Требований:** {len(report.requirements)} · **Нарушений:** {len(act)} "
        f"(🔴 {by_sev[Severity.CRITICAL]} критичных · 🟡 {by_sev[Severity.MAJOR]} существенных · "
        f"🟢 {by_sev[Severity.MINOR]} замечаний) · **Уточняющих вопросов:** {len(report.questions)}  ",
    ]
    L += [
        f"> Сформировано агентом **{P.agent_name} v{P.agent_version}** (коммит `{P.agent_commit}`), "
        f"модель `{', '.join(P.models)}`. Проверка по ISO/IEC/IEEE 29148:2018 (п. 5.2.5, 5.2.6) и IEEE Std 830-1998 (п. 4.3).",
        "",
        "**Содержание:** [Кратко](#кратко) · [1. Паспорт прогона](#1-паспорт-прогона) · [2. Нарезка требований](#2-как-агент-нарезал-требования) · "
        "[3. Проверка каждого требования](#3-проверка-каждого-требования) · [4. Проверка набора](#4-проверка-всего-набора-требований) · "
        "[5. Сводка](#5-сводка) · [6. Уточняющие вопросы](#6-уточняющие-вопросы-к-бизнесу-и-аналитику) · [7. Итог](#7-итог) · "
        + ("[8. Уточнение](#8-уточнение-ответы-на-вопросы-и-пересмотр) · " if report.clarification else "")
        + "[Приложения](#приложение-a-журнал-верификации-находок)",
        "",
    ]
    L += render_brief(report)

    # --- 1. паспорт -----------------------------------------------------------------------------------
    L += [
        "## 1. Паспорт прогона", "",
        "| Параметр | Значение |", "| :--- | :--- |",
        f"| Название документа | {cell(P.document_title)} |",
        f"| Файл | `{cell(P.document_file)}` ({P.document_format}, {P.document_lines} строк) |",
        f"| SHA-256 файла | `{P.document_sha256}` |",
        f"| Дата и время прогона | {P.run_started} |",
        f"| Модель LLM | `{', '.join(P.models)}` (провайдер: {P.provider}, temperature={P.temperature}) |",
        f"| Версия агента | {P.agent_name} v{P.agent_version}, коммит `{P.agent_commit}`"
        + (f" · отчёт перевыпущен из JSON прогона версией v{__version__}, без новых обращений к модели"
           if P.agent_version != __version__ else "") + " |",
        f"| Время анализа | {P.duration_sec} с |",
        f"| Вызовов LLM | {P.llm_calls} (из кэша: {P.llm_cached_calls}); токены: {P.input_tokens:,} вход / "
        f"{P.output_tokens:,} выход |".replace(",", " "),
        "| Критерии | ISO/IEC/IEEE 29148:2018 п. 5.2.5 (требование), п. 5.2.6 (набор); IEEE Std 830-1998 п. 4.3 |",
        "| Ссылки на место | «раздел › подраздел · строка N» — номер строки в проверяемом файле"
        + (" (для PDF также страница)" if P.document_format == "pdf" else "") + " |",
        "",
    ]

    # --- 2. нарезка ------------------------------------------------------------------------------------
    numbered = sum(1 for r in report.requirements if r.doc_id)
    L += [
        "## 2. Как агент нарезал требования", "",
        f"Выделено **{len(report.requirements)}** требований: {numbered} с идентификатором из документа, "
        f"{len(report.requirements) - numbered} без идентификатора (агент присвоил свой ID и сохранил точную цитату). "
        "Все цитаты сверены с исходным текстом.",
        "",
        "| ID в отчёте | ID в документе | Раздел | Тип | Краткая формулировка | Цитата из документа | Строки |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for r in report.requirements:
        lines_ = f"{r.line_start}" if r.line_start == r.line_end else f"{r.line_start}–{r.line_end}"
        L.append(f"| [{r.rid}](#{r.rid.lower()}) | {cell(r.doc_id) if r.doc_id else '— (нет)'} | {cell(r.section, 60)} | "
                 f"{KIND_RU.get(r.kind, r.kind)} | {cell(r.summary, 120)} | {cell(q(r.quote, 220))} | {lines_} |")
    L.append("")

    # --- 3. каждое требование ---------------------------------------------------------------------------
    L += ["## 3. Проверка каждого требования", "",
          "### 3.1. Матрица соответствия критериям", "",
          "Обозначения: ✅ соответствует · ⚠️ замечание · ❌ нарушение · ➖ неприменимо. "
          "Колонки 🔴 🟡 🟢 — число критичных, существенных дефектов и замечаний.", ""]
    head = "| ID | " + " | ".join(c.short for c in C.INDIVIDUAL) + " | 🔴 | 🟡 | 🟢 |"
    L += [head, "| :--- | " + " | ".join(":---:" for _ in C.INDIVIDUAL) + " | :---: | :---: | :---: |"]
    for r in report.requirements:
        rv = report.reviews.get(r.rid)
        eff = effective_criteria(rv, primary.get(r.rid, []))
        icons = " | ".join(eff[c.code][0].icon if c.code in eff else "·" for c in C.INDIVIDUAL)
        cnt = Counter(f.severity for f in primary.get(r.rid, []))
        d = " | ".join(str(cnt[s]) if cnt[s] else "" for s in Severity)
        L.append(f"| [{r.rid}](#{r.rid.lower()}) | {icons} | {d} |")
    L += ["", "### 3.2. Детальная проверка", ""]
    for r in report.requirements:
        _render_requirement(r, report, primary, secondary, L)

    # --- 4. набор -------------------------------------------------------------------------------------------
    L += ["## 4. Проверка всего набора требований", "",
          "### 4.1. Оценка по критериям качества набора", "",
          "| Критерий | Стандарт | Оценка | Комментарий |", "| :--- | :--- | :---: | :--- |"]
    set_eval_by = {e.criterion: e for e in report.set_evals}
    for c in C.SET:
        e = set_eval_by.get(c.code)
        n = [f for f in set_findings if f.criterion == c.code]
        status = e.status if e else (Status.FAIL if any(f.severity != Severity.MINOR for f in n)
                                     else Status.WARN if n else Status.NA)
        if n and status in (Status.OK, Status.NA):
            status = Status.FAIL if any(f.severity != Severity.MINOR for f in n) else Status.WARN
        comment = (e.comment.strip() if e else "")
        if comment and not comment.endswith((".", "!", "?")):
            comment += "."
        refs = f" Дефекты: {', '.join(f.fid for f in n)}." if n else ""
        L.append(f"| {c.name} | {c.source} | {status.icon} | {cell((comment + refs).strip())} |")
    L += ["", "### 4.2. Системные дефекты набора", ""]
    if not set_findings:
        L += ["Системных дефектов не найдено.", ""]
    grouped: dict[str, list[Finding]] = defaultdict(list)
    for f in set_findings:
        grouped[f.criterion].append(f)
    for c in C.SET + C.INDIVIDUAL:
        if grouped.get(c.code):
            L += [f"#### {c.name}", ""]
            for f in sorted(grouped[c.code], key=lambda x: x.fid):
                render_finding(f, L)
            L.append("")

    # --- 4.3 сценарии -------------------------------------------------------------------------------------
    if report.scenarios:
        sc = report.scenarios
        cnt = Counter(x.verdict for x in sc)
        L += ["### 4.3. Сценарный анализ: проигрывание ТЗ на конкретных примерах", "",
              f"Агент-сценарист проиграл **{len(sc)}** сценариев и проверил, даёт ли документ однозначный ожидаемый "
              f"результат: ✅ определено — {cnt['defined']}, ❓ не определено — {cnt['undefined']}, "
              f"⚔️ противоречиво — {cnt['contradictory']}.", "",
              "| ID | Сценарий | Дано / Когда | Что предписывает ТЗ | Итог | Дефект |",
              "| :--- | :--- | :--- | :--- | :---: | :--- |"]
        for x in sc:
            where = f" ({x.evidence[0].location.split(' · ')[-1]})" if x.evidence else ""
            L.append(f"| {x.sid} | {cell(x.title, 80)} | {cell(x.given_when, 220)} | {cell(x.spec_says, 220)}{where} | "
                     f"{x.icon} | {x.finding or '—'} |")
        L.append("")

    # --- 5. сводка -----------------------------------------------------------------------------------------
    with_def = {rid for f in act if f.scope == "requirement" for rid in f.rids}
    serious = {rid for f in act if f.severity != Severity.MINOR for rid in f.rids}
    total = len(report.requirements)
    L += [
        "## 5. Сводка", "",
        "| Метрика | Значение |", "| :--- | :--- |",
        f"| Всего требований | {total} |",
        f"| Требований без дефектов | {total - len(with_def & set(req_by_id))} |",
        f"| Требований с дефектами | {len(with_def & set(req_by_id))} |",
        f"| Требований без критичных и существенных дефектов | {total - len(serious & set(req_by_id))} "
        f"({V.score}%) |",
        f"| Всего нарушений | **{len(act)}** |",
        f"| — 🔴 Критичных (CRITICAL, блокеры) | {by_sev[Severity.CRITICAL]} |",
        f"| — 🟡 Существенных (MAJOR) | {by_sev[Severity.MAJOR]} |",
        f"| — 🟢 Замечаний (MINOR) | {by_sev[Severity.MINOR]} |",
        f"| Нарушений в отдельных требованиях / в наборе | {len([f for f in act if f.scope == 'requirement'])} / "
        f"{len(set_findings)} |",
        "",
        "### 5.1. Распределение нарушений по критериям", "",
        "| Критерий | 🔴 | 🟡 | 🟢 | Всего |", "| :--- | :---: | :---: | :---: | :---: |",
    ]
    by_crit: dict[str, Counter] = defaultdict(Counter)
    for f in act:
        by_crit[f.criterion][f.severity] += 1
    for c in C.INDIVIDUAL + C.SET:
        cnt = by_crit.get(c.code)
        if cnt:
            L.append(f"| {c.name} | {cnt[Severity.CRITICAL] or ''} | {cnt[Severity.MAJOR] or ''} | "
                     f"{cnt[Severity.MINOR] or ''} | {sum(cnt.values())} |")
    L += ["", "### 5.2. Критичные дефекты (блокеры разработки)", ""]
    crit = [f for f in act if f.severity == Severity.CRITICAL]
    if not crit:
        L.append("Критичных дефектов нет.")
    for i, f in enumerate(crit, start=1):
        where = f.primary.location if f.primary else "—"
        L.append(f"{i}. **{f.fid}** — {cell(f.title)} (*{where}*)")
    L.append("")
    L += ["### 5.3. Непроверяемые, противоречивые и неполные формулировки", ""]
    groups = [
        ("Непроверяемые", {"verifiable", "validatable"}),
        ("Противоречивые", {"consistent", "set_consistent"}),
        ("Неполные", {"complete", "set_complete"}),
        ("Неоднозначные", {"unambiguous", "comprehensible"}),
    ]
    for title, codes in groups:
        items = [f for f in act if f.criterion in codes and f.severity != Severity.MINOR]
        refs = "; ".join(f"**{f.fid}** {cell(f.title, 90)}" for f in items) or "нет"
        L.append(f"- **{title} ({len(items)}):** {refs}")
    L.append("")

    # --- 6. вопросы -----------------------------------------------------------------------------------------
    L += ["## 6. Уточняющие вопросы к бизнесу и аналитику", "",
          "Без ответов на эти вопросы требования нельзя реализовать и протестировать однозначно. "
          "Вопросы сгруппированы по адресатам, у каждого — варианты ответа и связанные дефекты.", ""]
    if not report.questions:
        L += ["Вопросов нет.", ""]
    by_addr: dict[str, list] = defaultdict(list)
    for qq in report.questions:
        by_addr[qq.addressee].append(qq)
    for addr in sorted(by_addr, key=lambda a: ADDRESSEE_ORDER.index(a) if a in ADDRESSEE_ORDER else 99):
        L += [f"### {addr}", ""]
        for qq in by_addr[addr]:
            L.append(f"- **{qq.qid}.** {cell(qq.question)}")
            if qq.why:
                L.append(f"  - *Зачем:* {cell(qq.why)}")
            if qq.options:
                L.append("  - *Варианты ответа:* " + "; ".join(f"{chr(0x430 + i)}) {cell(o)}"
                                                            for i, o in enumerate(qq.options)))
            if qq.related:
                L.append(f"  - *Связанные дефекты:* {', '.join(qq.related)}")
        L.append("")

    # --- 7. итог -------------------------------------------------------------------------------------------
    L += [
        "## 7. Итог", "",
        f"**Можно ли начинать разработку:** {VERDICT_ICON[V.dev_status]} **{STATUS_RU[V.dev_status]}**"
        + (f" — {cell(V.dev_comment)}" if V.dev_comment else ""), "",
        f"**Можно ли начинать тестирование:** {VERDICT_ICON[V.test_status]} **{STATUS_RU[V.test_status]}**"
        + (f" — {cell(V.test_comment)}" if V.test_comment else ""), "",
    ]
    if V.summary:
        L += [cell(V.summary), ""]
    L += ["Правило вердикта: есть хотя бы один 🔴 CRITICAL — разработка не готова; есть 🟡 MAJOR — готова с условиями "
          "(доуточнить до спринта); только 🟢 MINOR — готова.", "",
          "### Что исправить в первую очередь", ""]
    if V.priorities:
        for i, p in enumerate(V.priorities, start=1):
            refs = f" ({', '.join(p.fids)})" if p.fids else ""
            L.append(f"{i}. {cell(p.action)}{refs}")
    else:
        for i, f in enumerate(sorted(act, key=lambda x: x.severity.rank)[:7], start=1):
            L.append(f"{i}. {cell(f.recommendation or f.title)} ({f.fid})")
    L.append("")

    # --- 8. уточнение ---------------------------------------------------------------------------------------------
    cl = report.clarification
    if cl is not None:
        n_res = sum(1 for r in cl.resolutions if r.status == "resolved")
        n_part = sum(1 for r in cl.resolutions if r.status == "partial")
        L += ["## 8. Уточнение: ответы на вопросы и пересмотр", "",
              f"Получено ответов: **{len(cl.answers)}** из {len(report.questions)}. Устранено дефектов: **{n_res}**, "
              f"частично: **{n_part}**.", ""]
        if cl.summary:
            L += [cell(cl.summary), ""]
        if cl.verdict_after:
            va = cl.verdict_after
            L += [f"**Вердикт после уточнения:** разработка — {VERDICT_ICON[va.dev_status]} {STATUS_RU[va.dev_status]}, "
                  f"тестирование — {VERDICT_ICON[va.test_status]} {STATUS_RU[va.test_status]} "
                  f"(с учётом внесения правок из этого раздела).", ""]
        L += ["### Ответы", "", "| Вопрос | Ответ |", "| :--- | :--- |"]
        L += [f"| {a.qid}. {cell(a.question, 200)} | {cell(a.answer)} |" for a in cl.answers]
        L.append("")
        if cl.resolutions:
            L += ["### Пересмотр дефектов", "", "| Дефект | Статус | Как ответ меняет требование |", "| :--- | :--- | :--- |"]
            for r in cl.resolutions:
                f = next((x for x in act if x.fid == r.fid), None)
                L.append(f"| {r.fid} {cell(f.title, 80) if f else ''} | {RES_ICON[r.status]} | {cell(r.comment)} |")
            L.append("")
        done = _resolved_ids(report)
        left = sorted((f for f in act if f.fid not in done), key=lambda f: (f.severity.rank, f.fid))
        if left:
            L += ["### Что остаётся открытым", "",
                  f"Ответы не закрыли {nform(len(left), 'дефект', 'дефекта', 'дефектов')}: "
                  f"{_sev_phrase(Counter(f.severity for f in left))}. Их нужно доуточнить до спринта.", "",
                  "| Дефект | Критичность | Суть | Где |", "| :--- | :--- | :--- | :--- |"]
            for f in left:
                where = short_loc(f.primary.location) if f.primary else "—"
                L.append(f"| [{f.fid}]({finding_anchor(f)}) | {f.severity.icon} {SEV_NAME[f.severity]} | "
                         f"{cell(f.title, 130)} | {cell(where)} |")
            L.append("")
        if cl.revised:
            L += ["### Исправленная редакция требований", ""]
            for r in cl.revised:
                orig = req_by_id.get(r.rid)
                head = (r.doc_id or r.rid or "Новое требование") + (" (новое)" if r.is_new else "")
                L.append(f"- **{cell(head)}**" + (f" — *было:* {q(orig.quote, 300)}" if orig else ""))
                L.append(f"  - *стало:* {cell(r.text)}" + (f" *(устраняет: {', '.join(r.fixes)})*" if r.fixes else ""))
            L.append("")
        if cl.acceptance:
            L += ["### Новые и исправленные критерии приёмки", "", "| Требование | Дано | Когда | Ожидается |",
                  "| :--- | :--- | :--- | :--- |"]
            L += [f"| {cell(', '.join(a.covers))} | {cell(a.given)} | {cell(a.when)} | {cell(a.then)} |" for a in cl.acceptance]
            L.append("")

    # --- приложения ------------------------------------------------------------------------------------------
    dropped = [f for f in report.findings if f.status == "dropped"]
    merged = [f for f in report.findings if f.status == "merged"]
    sev_changed = [f for f in act if f.note.startswith("критичность")]
    raw = int(report.stage_times.get("raw_findings", len(report.findings)))
    L += ["## Приложение A. Журнал верификации находок", "",
          "Каждая находка проходит три фильтра: (1) цитата сверяется с текстом документа — без подтверждения находка "
          "отбрасывается; (2) дубликаты от разных агентов и правил объединяются; (3) независимый агент-критик "
          "отклоняет ложные срабатывания и калибрует критичность.", "",
          f"- Найдено всеми агентами и правилами: **{raw}**",
          f"- Отброшено без подтверждения цитатой: **{len([f for f in dropped if 'цитат' in f.note])}**",
          f"- Объединено дубликатов: **{len(merged)}**",
          f"- Отклонено критиком: **{len([f for f in dropped if 'цитат' not in f.note])}**",
          f"- Изменена критичность: **{len(sev_changed)}**",
          f"- В отчёте: **{len(act)}**", ""]
    if dropped:
        L += ["| ID | Находка | Причина отклонения |", "| :--- | :--- | :--- |"]
        for f in dropped:
            L.append(f"| {f.fid} | {cell(f.title, 120)} | {cell(f.note, 160)} |")
        L.append("")
    if sev_changed:
        L += ["Изменения критичности:", ""] + [f"- {f.fid}: {cell(f.note)}" for f in sev_changed] + [""]

    L += ["## Приложение B. Ход прогона (рой агентов)", "",
          "| Этап (агент) | Время, с |", "| :--- | ---: |"]
    for name, t in report.stage_times.items():
        if name != "raw_findings":
            L.append(f"| {name} | {t} |")
    L += ["", "| Вызов LLM | Модель | Токены (вход/выход) | Время, с | Кэш | Статус |",
          "| :--- | :--- | :--- | ---: | :---: | :--- |"]
    for c in report.calls:
        L.append(f"| {c.stage} | `{c.model}` | {c.input_tokens}/{c.output_tokens} | {c.latency} | "
                 f"{'да' if c.cached else ''} | {'ok' if c.ok else cell(c.error, 80)} |")
    if report.warnings:
        L += ["", "Предупреждения прогона:", ""] + [f"- {cell(w, 300)}" for w in report.warnings]
    L += ["", "## Приложение C. Методика", "",
          "Свойства отдельного требования (ISO/IEC/IEEE 29148:2018 п. 5.2.5, IEEE 830 п. 4.3):", ""]
    L += [f"- **{c.name}** ({c.source}) — {c.question}" for c in C.INDIVIDUAL]
    L += ["", "Свойства набора требований (ISO/IEC/IEEE 29148:2018 п. 5.2.6, IEEE 830 п. 4.3):", ""]
    L += [f"- **{c.name}** ({c.source}) — {c.question}" for c in C.SET]
    L += ["", "Шкала критичности:", "", C.SEVERITY_RUBRIC, "",
          "---", f"*Отчёт сгенерирован автоматически агентом {P.agent_name} v{P.agent_version} "
                 f"(коммит {P.agent_commit}). Ручная правка отчёта не производилась."
                 + (f" Оформление обновлено командой `python -m solohive.render` (v{__version__}); "
                    "находки, цитаты и вердикты прогона не менялись." if P.agent_version != __version__ else "")
                 + "*", ""]
    return "\n".join(L)


def _render_requirement(r: Requirement, report: Report, primary, secondary, L: list[str]) -> None:
    rv = report.reviews.get(r.rid)
    doc_id = r.doc_id or "без ID в документе"
    L += [f'<a id="{r.rid.lower()}"></a>', "",
          f"#### {r.rid} · {doc_id} · {KIND_RU.get(r.kind, r.kind)} — {cell(r.summary, 110)}", "",
          f"> {q(r.quote, 700)}  ", f"> *{r.location}*", ""]
    own = sorted(primary.get(r.rid, []), key=lambda f: (f.severity.rank, f.fid))
    eff = effective_criteria(rv, own)
    if rv or eff:
        problem = [(c, eff[c.code]) for c in C.INDIVIDUAL if c.code in eff and eff[c.code][0] in (Status.WARN, Status.FAIL)]
        okay = [c.name for c in C.INDIVIDUAL if c.code in eff and eff[c.code][0] == Status.OK]
        na = [c.name for c in C.INDIVIDUAL if c.code in eff and eff[c.code][0] == Status.NA]
        if problem:
            L += ["| Критерий | Оценка | Комментарий |", "| :--- | :---: | :--- |"]
            for c, (st, comment) in problem:
                L.append(f"| {c.name} | {st.icon} | {cell(comment)} |")
            L.append("")
        if okay:
            L.append(f"✅ **Без нарушений:** {', '.join(okay)}.  ")
        if na:
            L.append(f"➖ **Неприменимо:** {', '.join(na)}.")
        L.append("")
    if not rv:
        L += ["*Оценка рецензента по всем критериям не получена; показаны находки правил и системного анализа.*", ""]
    if own:
        L += ["**Дефекты:**", ""]
        for f in own:
            render_finding(f, L)
        L.append("")
    elif rv:
        L += ["**Дефектов не найдено.**", ""]
    other = [f for f in secondary.get(r.rid, []) if f not in own]
    if other:
        L += ["**Также затрагивают это требование:** " + "; ".join(
            f"{f.severity.icon} {f.fid} {cell(f.title, 80)} (см. {f.rids[0]})" for f in other), ""]
    if rv and rv.rewrite:
        L += ["**Предлагаемая формулировка:**", "", f"> {cell(rv.rewrite)}", ""]
    if rv and rv.test_ideas:
        L += ["**Идеи приёмочных тестов:**", ""] + [f"- {cell(t)}" for t in rv.test_ideas] + [""]
    L += ["---", ""]
