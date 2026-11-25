"""Отчёт в Markdown: структура строго по разделу 6 ТЗ + уточняющие вопросы + журнал верификации."""

from __future__ import annotations

from collections import Counter, defaultdict

from .. import criteria as C
from ..agents.interviewer import STATUS_RU
from ..agents.segmenter import KIND_RU
from ..models import Finding, Report, Requirement, Severity, Status

VERDICT_ICON = {"no": "⛔", "conditional": "⚠️", "yes": "✅"}
ADDRESSEE_ORDER = ["Бизнес / Product owner", "Системный аналитик", "Архитектор", "Информационная безопасность",
                   "Тестирование"]


def cell(text: str | None, limit: int = 0) -> str:
    t = " ".join((text or "").split()).replace("|", "\\|")
    if limit and len(t) > limit:
        t = t[: limit - 1] + "…"
    return t or "—"


def q(text: str, limit: int = 0) -> str:
    """Цитата в «ёлочках» без markdown-разметки внутри."""
    t = " ".join((text or "").split()).replace("*", "\\*").replace("_", "\\_")
    if limit and len(t) > limit:
        t = t[: limit - 1] + "…"
    return f"«{t}»"


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
        f"> Сформировано агентом **{P.agent_name} v{P.agent_version}** (коммит `{P.agent_commit}`), "
        f"модель `{', '.join(P.models)}`. Проверка по ISO/IEC/IEEE 29148:2018 (п. 5.2.5, 5.2.6) и IEEE Std 830-1998 (п. 4.3).",
        "",
        "**Содержание:** [1. Паспорт прогона](#1-паспорт-прогона) · [2. Нарезка требований](#2-как-агент-нарезал-требования) · "
        "[3. Проверка каждого требования](#3-проверка-каждого-требования) · [4. Проверка набора](#4-проверка-всего-набора-требований) · "
        "[5. Сводка](#5-сводка) · [6. Уточняющие вопросы](#6-уточняющие-вопросы-к-бизнесу-и-аналитику) · [7. Итог](#7-итог) · "
        + ("[8. Уточнение](#8-уточнение-ответы-на-вопросы-и-пересмотр) · " if report.clarification else "")
        + "[Приложения](#приложение-a-журнал-верификации-находок)",
        "",
    ]

    # --- 1. паспорт -----------------------------------------------------------------------------------
    L += [
        "## 1. Паспорт прогона", "",
        "| Параметр | Значение |", "| :--- | :--- |",
        f"| Название документа | {cell(P.document_title)} |",
        f"| Файл | `{cell(P.document_file)}` ({P.document_format}, {P.document_lines} строк) |",
        f"| SHA-256 файла | `{P.document_sha256}` |",
        f"| Дата и время прогона | {P.run_started} |",
        f"| Модель LLM | `{', '.join(P.models)}` (провайдер: {P.provider}, temperature={P.temperature}) |",
        f"| Версия агента | {P.agent_name} v{P.agent_version}, коммит `{P.agent_commit}` |",
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
          "Колонка «Дефекты» — число нарушений по критичности.", ""]
    head = "| ID | " + " | ".join(c.short for c in C.INDIVIDUAL) + " | Дефекты |"
    L += [head, "| :--- | " + " | ".join(":---:" for _ in C.INDIVIDUAL) + " | :--- |"]
    for r in report.requirements:
        rv = report.reviews.get(r.rid)
        eff = effective_criteria(rv, primary.get(r.rid, []))
        icons = " | ".join(eff[c.code][0].icon if c.code in eff else "·" for c in C.INDIVIDUAL)
        cnt = Counter(f.severity for f in primary.get(r.rid, []))
        d = " ".join(f"{s.icon}{cnt[s]}" for s in Severity if cnt[s]) or "—"
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
            icon = {"resolved": "✅ устранён", "partial": "◐ частично", "open": "⏳ открыт"}
            L += ["### Пересмотр дефектов", "", "| Дефект | Статус | Как ответ меняет требование |", "| :--- | :--- | :--- |"]
            for r in cl.resolutions:
                f = next((x for x in act if x.fid == r.fid), None)
                L.append(f"| {r.fid} {cell(f.title, 80) if f else ''} | {icon[r.status]} | {cell(r.comment)} |")
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
                 f"(коммит {P.agent_commit}). Ручная правка отчёта не производилась.*", ""]
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
