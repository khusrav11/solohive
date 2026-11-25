"""Интерактивный HTML-отчёт: дефекты рядом с размеченным документом, фильтры, поиск, матрица, сценарии, вопросы.

Файл самодостаточный (без внешних шрифтов и скриптов), открывается офлайн и печатается. Весь контент
рендерится на сервере — без JavaScript отчёт читается полностью; JS добавляет фильтры и связь «дефект ↔ строка».
"""

from __future__ import annotations

import html
import re
from collections import Counter, defaultdict

from .. import criteria as C
from ..agents.interviewer import STATUS_RU
from ..agents.segmenter import KIND_RU
from ..models import Finding, Report, Severity, Status
from .markdown import ADDRESSEE_ORDER, effective_criteria, source_label

E = html.escape
SEV_RU = {"CRITICAL": "Критичный", "MAJOR": "Существенный", "MINOR": "Замечание"}
VERDICT_TEXT = {"no": "не готов", "conditional": "готов с условиями", "yes": "готов"}
SCEN_RU = {"defined": "определено", "undefined": "не определено", "contradictory": "противоречиво"}


def _lines_of(f: Finding) -> list[int]:
    out = []
    for e in f.evidence:
        if e.verified and e.line:
            out += list(range(e.line, (e.line_end or e.line) + 1))
    return sorted(set(out))


def _doc_line(text: str) -> tuple[str, str]:
    """Строка исходника → (css-класс, html)."""
    t = text.rstrip()
    m = re.match(r"^(#{1,6})\s+(.*)$", t)
    if m:
        return f"h h{len(m.group(1))}", E(re.sub(r"\*\*(.+?)\*\*", r"\1", m.group(2)))
    if t.lstrip().startswith("|"):
        if re.fullmatch(r"\|?[\s:|-]+\|?", t.strip()):
            return "rule", ""
        cells = [c.strip() for c in t.strip().strip("|").split("|")]
        return "row", "".join(f"<span class=c>{E(re.sub(r'[*`]', '', c))}</span>" for c in cells)
    body = E(t)
    body = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", body)
    body = re.sub(r"`([^`]+)`", r"<code>\1</code>", body)
    return ("code" if t.startswith("```") else "p"), body


def _finding_html(f: Finding, req_by_id) -> str:
    ev = [e for e in f.evidence if e.verified]
    rows = []
    for i, e in enumerate(ev[:4]):
        lab = "Цитата" if i == 0 else "Связанное место"
        rows.append(f'<div class="ev"><q>{E(e.quote[:420])}</q><a class="loc" href="#L{e.line}" data-goto="{e.line}" '
                    f'title="{E(e.location)}">{E(_short_loc(e.location))}</a><span class="evlab">{lab}</span></div>')
    reqs = ", ".join(f'<a href="#{r.lower()}">{E(r)}</a>' + (f" ({E(req_by_id[r].doc_id)})" if r in req_by_id and req_by_id[r].doc_id else "")
                     for r in f.rids)
    meta = [f"<span>{E(C.name(f.criterion))}</span>", f"<span>{E(source_label(f))}</span>"]
    if reqs:
        meta.append(f"<span>Требования: {reqs}</span>")
    if f.scenario:
        meta.append(f"<span>Сценарий {E(f.scenario)}</span>")
    if f.note:
        meta.append(f"<span>{E(f.note)}</span>")
    return (f'<article class="defect s-{f.severity.value}" id="{f.fid}" data-sev="{f.severity.value}" '
            f'data-crit="{f.criterion}" data-scope="{f.scope}" data-lines="{",".join(map(str, _lines_of(f)))}" tabindex="0">'
            f'<header><span class="sev">{SEV_RU[f.severity.value]}</span><span class="fid">{f.fid}</span>'
            f'<h3>{E(f.title)}</h3></header>'
            + "".join(rows)
            + (f'<p class="why"><b>Почему это проблема.</b> {E(f.explanation)}</p>' if f.explanation else "")
            + (f'<p class="fix"><b>Как исправить.</b> {E(f.recommendation)}</p>' if f.recommendation else "")
            + f'<footer>{"".join(meta)}</footer></article>')


def render_html(report: Report) -> str:
    P, V = report.passport, report.verdict
    act = report.active_findings
    sev = Counter(f.severity.value for f in act)
    req_by_id = {r.rid: r for r in report.requirements}
    marks: dict[int, list[Finding]] = defaultdict(list)
    for f in act:
        for ln in _lines_of(f):
            marks[ln].append(f)
    primary: dict[str, list[Finding]] = defaultdict(list)
    for f in act:
        if f.scope == "requirement" and f.rids:
            primary[f.rids[0]].append(f)

    # --- шапка ---------------------------------------------------------------------------------------
    total = max(1, len(act))
    bar = "".join(f'<i class="s-{s}" style="flex:{sev[s]}" title="{SEV_RU[s]}: {sev[s]}"></i>'
                  for s in ("CRITICAL", "MAJOR", "MINOR") if sev[s])
    head_line = (f"Документ {VERDICT_TEXT[V.dev_status]} к разработке"
                 + (f": {sev['CRITICAL']} {_pl(sev['CRITICAL'], 'блокер', 'блокера', 'блокеров')}" if sev["CRITICAL"] else ""))
    header = f"""
<header class="top">
  <div class="brand">SoloHive <span>отчёт статической проверки требований</span></div>
  <h1>{E(P.document_title)}</h1>
  <p class="verdict v-{V.dev_status}">{E(head_line)}</p>
  <div class="facts">
    <div><b>{len(report.requirements)}</b> {_pl(len(report.requirements), 'требование', 'требования', 'требований')}</div>
    <div><b>{len(act)}</b> {_pl(len(act), 'нарушение', 'нарушения', 'нарушений')}</div>
    <div class="s-CRITICAL"><b>{sev['CRITICAL']}</b> {_pl(sev['CRITICAL'], 'критичный', 'критичных', 'критичных')}</div>
    <div class="s-MAJOR"><b>{sev['MAJOR']}</b> {_pl(sev['MAJOR'], 'существенный', 'существенных', 'существенных')}</div>
    <div class="s-MINOR"><b>{sev['MINOR']}</b> {_pl(sev['MINOR'], 'замечание', 'замечания', 'замечаний')}</div>
    <div><b>{len(report.questions)}</b> {_pl(len(report.questions), 'вопрос', 'вопроса', 'вопросов')}</div>
    <div><b>{len(report.scenarios)}</b> {_pl(len(report.scenarios), 'сценарий', 'сценария', 'сценариев')}</div>
  </div>
  <div class="bar" aria-hidden="true">{bar}</div>
  <div class="status">
    <span class="v-{V.dev_status}">Разработка: {STATUS_RU[V.dev_status].lower()}</span>
    <span class="v-{V.test_status}">Тестирование: {STATUS_RU[V.test_status].lower()}</span>
    <span class="muted">Модель {E(', '.join(P.models))}, агент v{E(P.agent_version)} ({E(P.agent_commit)}), {P.duration_sec} с</span>
  </div>
</header>"""

    nav = """
<nav class="tabs" role="tablist">
  <button role="tab" data-tab="defects" aria-selected="true">Дефекты в документе</button>
  <button role="tab" data-tab="reqs">Требования</button>
  <button role="tab" data-tab="scen">Сценарии</button>
  <button role="tab" data-tab="questions">Вопросы</button>
  <button role="tab" data-tab="final">Итог</button>
  <button role="tab" data-tab="passport">Паспорт</button>
</nav>"""

    # --- дефекты + документ -----------------------------------------------------------------------------
    crit_opts = "".join(f'<option value="{c.code}">{E(c.name)}</option>' for c in C.INDIVIDUAL + C.SET
                        if any(f.criterion == c.code for f in act))
    filters = f"""
<div class="filters">
  <div class="chips" role="group" aria-label="Критичность">
    <button class="chip s-CRITICAL" aria-pressed="true" data-sev="CRITICAL">Критичные {sev['CRITICAL']}</button>
    <button class="chip s-MAJOR" aria-pressed="true" data-sev="MAJOR">Существенные {sev['MAJOR']}</button>
    <button class="chip s-MINOR" aria-pressed="true" data-sev="MINOR">Замечания {sev['MINOR']}</button>
  </div>
  <select id="fcrit" aria-label="Критерий"><option value="">Все критерии</option>{crit_opts}</select>
  <select id="fscope" aria-label="Уровень"><option value="">Требования и набор</option>
    <option value="requirement">Отдельные требования</option><option value="set">Набор целиком</option></select>
  <input id="fq" type="search" placeholder="Поиск по дефектам" aria-label="Поиск по дефектам">
  <span id="fcount" class="muted"></span>
</div>"""
    defects = "".join(_finding_html(f, req_by_id) for f in act)
    doc_rows = []
    for i, text in enumerate(report.source_lines, start=1):
        cls, body = _doc_line(text)
        if cls == "rule" or text.strip() == "---":
            continue
        mk = "".join(f'<a class="mk s-{f.severity.value}" href="#{f.fid}" data-fid="{f.fid}">{f.fid}</a>'
                     for f in sorted(marks.get(i, []), key=lambda x: x.severity.rank)[:4])
        more = len(marks.get(i, [])) - 4
        if more > 0:
            mk += f'<span class="more">+{more}</span>'
        doc_rows.append(f'<li id="L{i}" class="{cls}{" has" if i in marks else ""}"><span class="ln">{i}</span>'
                        f'<span class="tx">{body}</span><span class="mks">{mk}</span></li>')
    tab_defects = f"""
<section id="tab-defects" class="tab on">
  {filters}
  <div class="split">
    <div class="list" id="dlist">{defects or '<p>Дефектов не найдено.</p>'}</div>
    <aside class="doc" aria-label="Проверяемый документ"><div class="dochead">{E(P.document_file)}, {len(report.source_lines)} строк</div>
      <ol>{"".join(doc_rows)}</ol></aside>
  </div>
</section>"""

    # --- требования ----------------------------------------------------------------------------------------
    th = "".join(f'<th title="{E(c.name)} ({E(c.source)})">{E(c.short)}</th>' for c in C.INDIVIDUAL)
    rows = []
    cards = []
    for r in report.requirements:
        rv = report.reviews.get(r.rid)
        own = sorted(primary.get(r.rid, []), key=lambda f: f.severity.rank)
        eff = effective_criteria(rv, own)
        tds = "".join(f'<td class="st-{eff[c.code][0].value if c.code in eff else "x"}" title="{E(c.name)}: '
                      f'{E(eff[c.code][1]) if c.code in eff else ""}">{_st(eff, c.code)}</td>' for c in C.INDIVIDUAL)
        cnt = Counter(f.severity.value for f in own)
        dots = "".join(f'<span class="cnt s-{s}">{cnt[s]}</span>' for s in ("CRITICAL", "MAJOR", "MINOR") if cnt[s])
        rows.append(f'<tr><td><a href="#{r.rid.lower()}">{r.rid}</a></td><td>{E(r.doc_id or "—")}</td>'
                    f'<td class="sum">{E(r.summary[:90])}</td>{tds}<td>{dots or "—"}</td></tr>')
        crit_rows = "".join(f'<li class="st-{st.value}"><b>{E(C.name(code))}</b> {E(cm)}</li>'
                            for code, (st, cm) in eff.items() if st in (Status.WARN, Status.FAIL))
        ok = [C.name(code) for code, (st, _) in eff.items() if st == Status.OK]
        links = ", ".join(f'<a href="#{f.fid}" data-fid="{f.fid}" class="s-{f.severity.value}">{f.fid}</a> {E(f.title)}'
                          for f in own)
        tests = "".join(f"<li>{E(t)}</li>" for t in (rv.test_ideas if rv else []))
        cards.append(
            f'<details class="req" id="{r.rid.lower()}"><summary><span class="rid">{r.rid}</span>'
            f'<span>{E(r.doc_id or "без ID")}</span><span class="kind">{E(KIND_RU.get(r.kind, r.kind))}</span>'
            f'<span class="rs">{E(r.summary)}</span>{dots}</summary>'
            f'<blockquote>{E(r.quote)}<a class="loc" href="#L{r.line_start}" data-goto="{r.line_start}">{E(r.location)}</a></blockquote>'
            + (f'<ul class="crit">{crit_rows}</ul>' if crit_rows else "")
            + (f'<p class="muted">Без нарушений: {E(", ".join(ok))}.</p>' if ok else "")
            + (f'<p><b>Дефекты:</b> {links}</p>' if links else "")
            + (f'<p class="rewrite"><b>Предлагаемая формулировка.</b> {E(rv.rewrite)}</p>' if rv and rv.rewrite else "")
            + (f'<p><b>Идеи приёмочных тестов</b></p><ul class="tests">{tests}</ul>' if tests else "")
            + "</details>")
    tab_reqs = f"""
<section id="tab-reqs" class="tab">
  <p class="lead">Агент выделил {len(report.requirements)} {_pl(len(report.requirements), 'требование', 'требования', 'требований')} и проверил каждое по 10 свойствам
  ISO/IEC/IEEE 29148 и IEEE 830. Наведите на ячейку, чтобы увидеть комментарий.</p>
  <div class="scroll"><table class="matrix"><thead><tr><th>ID</th><th>В документе</th><th>Требование</th>{th}<th>Дефекты</th></tr></thead>
  <tbody>{"".join(rows)}</tbody></table></div>
  <div class="cards">{"".join(cards)}</div>
</section>"""

    # --- сценарии ------------------------------------------------------------------------------------------
    sc_rows = "".join(
        f'<tr class="sc-{s.verdict}"><td>{s.sid}</td><td><b>{E(s.title)}</b><br><span class="muted">{E(s.given_when)}</span></td>'
        f'<td>{E(s.spec_says)}' + (f'<br><a class="loc" href="#L{s.evidence[0].line}" data-goto="{s.evidence[0].line}">'
                                   f'{E(_short_loc(s.evidence[0].location))}</a>' if s.evidence else "") + "</td>"
        f'<td class="verd">{SCEN_RU[s.verdict]}</td>'
        f'<td>{f"<a href=#{s.finding} data-fid={s.finding}>{s.finding}</a>" if s.finding else "—"}</td></tr>'
        for s in report.scenarios)
    scnt = Counter(s.verdict for s in report.scenarios)
    tab_scen = f"""
<section id="tab-scen" class="tab">
  <p class="lead">Агент-сценарист проиграл {len(report.scenarios)} {_pl(len(report.scenarios), 'конкретный сценарий', 'конкретных сценария', 'конкретных сценариев')} по тексту ТЗ.
  Результат определён в {scnt['defined']}, не определён в {scnt['undefined']}, противоречив в {scnt['contradictory']}.</p>
  {f'<div class="scroll"><table class="scen"><thead><tr><th>ID</th><th>Сценарий</th><th>Что предписывает ТЗ</th><th>Итог</th><th>Дефект</th></tr></thead><tbody>{sc_rows}</tbody></table></div>' if sc_rows else '<p>Сценарный анализ не выполнялся.</p>'}
</section>"""

    # --- вопросы -------------------------------------------------------------------------------------------
    by_addr: dict[str, list] = defaultdict(list)
    for q in report.questions:
        by_addr[q.addressee].append(q)
    qhtml = []
    for addr in sorted(by_addr, key=lambda a: ADDRESSEE_ORDER.index(a) if a in ADDRESSEE_ORDER else 99):
        items = []
        for q in by_addr[addr]:
            opts = "".join(f"<li>{E(o)}</li>" for o in q.options)
            rel = ", ".join(f'<a href="#{x}" data-fid="{x}">{x}</a>' for x in q.related)
            items.append(f'<li class="q"><p class="qq"><span class="qid">{q.qid}</span>{E(q.question)}</p>'
                         + (f'<p class="muted">{E(q.why)}</p>' if q.why else "")
                         + (f'<ol class="opts" type="a">{opts}</ol>' if opts else "")
                         + (f'<p class="rel">Связано: {rel}</p>' if rel else "") + "</li>")
        qhtml.append(f'<h2>{E(addr)}</h2><ol class="qs">{"".join(items)}</ol>')
    tab_q = f"""
<section id="tab-questions" class="tab">
  <p class="lead">Без ответов на эти вопросы требования нельзя реализовать и проверить однозначно.
  У вопросов есть варианты ответа, чтобы бизнесу было проще решить.</p>
  {"".join(qhtml) or "<p>Вопросов нет.</p>"}
</section>"""

    # --- итог ----------------------------------------------------------------------------------------------
    prio = "".join(f'<li>{E(p.action)} ' + " ".join(f'<a href="#{x}" data-fid="{x}">{x}</a>' for x in p.fids) + "</li>"
                   for p in V.priorities)
    crit_list = "".join(f'<li><a href="#{f.fid}" data-fid="{f.fid}">{f.fid}</a> {E(f.title)}</li>'
                        for f in act if f.severity == Severity.CRITICAL)
    tab_final = f"""
<section id="tab-final" class="tab">
  <div class="final">
    <p class="big v-{V.dev_status}">Разработка: {STATUS_RU[V.dev_status].lower()}.</p>
    <p>{E(V.dev_comment)}</p>
    <p class="big v-{V.test_status}">Тестирование: {STATUS_RU[V.test_status].lower()}.</p>
    <p>{E(V.test_comment)}</p>
    {f"<p>{E(V.summary)}</p>" if V.summary else ""}
    <h2>Что исправить в первую очередь</h2>
    <ol class="prio">{prio or "<li>См. критичные дефекты.</li>"}</ol>
    <h2>Блокеры разработки</h2>
    <ul>{crit_list or "<li>Критичных дефектов нет.</li>"}</ul>
    <p class="muted">Правило вердикта: есть критичный дефект — разработка не готова; есть существенные — готова с условиями;
    только замечания — готова. Без критичных и существенных дефектов {V.score}% требований.</p>
  </div>
</section>"""

    # --- паспорт ---------------------------------------------------------------------------------------------
    dropped = [f for f in report.findings if f.status == "dropped"]
    merged = [f for f in report.findings if f.status == "merged"]
    calls = "".join(f"<tr><td>{E(c.stage)}</td><td>{E(c.model)}</td><td>{c.input_tokens}/{c.output_tokens}</td>"
                    f"<td>{c.latency}</td><td>{'кэш' if c.cached else ('ok' if c.ok else E(c.error[:80]))}</td></tr>"
                    for c in report.calls)
    drop_rows = "".join(f"<tr><td>{E(f.fid)}</td><td>{E(f.title)}</td><td>{E(f.note)}</td></tr>" for f in dropped)
    tab_pass = f"""
<section id="tab-passport" class="tab">
  <table class="kv">
    <tr><th>Документ</th><td>{E(P.document_title)}</td></tr>
    <tr><th>Файл</th><td>{E(P.document_file)} ({E(P.document_format)}, {P.document_lines} строк)</td></tr>
    <tr><th>SHA-256</th><td class="hash">{E(P.document_sha256)}</td></tr>
    <tr><th>Дата прогона</th><td>{E(P.run_started)}</td></tr>
    <tr><th>Модель</th><td>{E(', '.join(P.models))} (провайдер {E(P.provider)}, temperature {P.temperature})</td></tr>
    <tr><th>Версия агента</th><td>{E(P.agent_name)} v{E(P.agent_version)}, коммит {E(P.agent_commit)}</td></tr>
    <tr><th>Время анализа</th><td>{P.duration_sec} с, вызовов LLM {P.llm_calls} (из кэша {P.llm_cached_calls}),
      токены {P.input_tokens} / {P.output_tokens}</td></tr>
    <tr><th>Критерии</th><td>ISO/IEC/IEEE 29148:2018 п. 5.2.5, 5.2.6; IEEE Std 830-1998 п. 4.3</td></tr>
    <tr><th>Верификация</th><td>в отчёте {len(act)}, объединено дубликатов {len(merged)}, отклонено {len(dropped)}</td></tr>
  </table>
  {f'<h2>Отклонённые находки</h2><div class="scroll"><table class="log"><thead><tr><th>ID</th><th>Находка</th><th>Причина</th></tr></thead><tbody>{drop_rows}</tbody></table></div>' if drop_rows else ''}
  <h2>Вызовы модели</h2>
  <div class="scroll"><table class="log"><thead><tr><th>Этап</th><th>Модель</th><th>Токены</th><th>Время, с</th><th>Статус</th></tr></thead>
  <tbody>{calls}</tbody></table></div>
</section>"""

    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SoloHive — {E(P.document_title)}</title><style>{CSS}</style></head>
<body>{header}{nav}<main>{tab_defects}{tab_reqs}{tab_scen}{tab_q}{tab_final}{tab_pass}</main>
<footer class="foot">Отчёт сформирован автоматически агентом {E(P.agent_name)} v{E(P.agent_version)}. Ручная правка не производилась.</footer>
<script>{JS}</script></body></html>"""


def _short_loc(loc: str) -> str:
    """«Часть A › Требования › Функциональные требования · строка 39» → «Функциональные требования, строка 39»."""
    where, _, rest = loc.rpartition(" · ")
    last = where.split(" › ")[-1] if where else ""
    return f"{last}, {rest}" if last else loc


def _st(eff, code) -> str:
    if code not in eff:
        return "·"
    return {"ok": "✓", "warn": "!", "fail": "✕", "na": "–"}[eff[code][0].value]


def _pl(n, one, few, many) -> str:
    from ..textutil import plural
    return plural(n, one, few, many)


CSS = r"""
:root{--paper:#fff;--bg:#f4f6f9;--ink:#18202c;--mute:#5d6777;--rule:#d8dde5;--link:#1f4fa3;
--crit:#b42318;--crit-bg:#fdecea;--major:#a15c07;--major-bg:#fdf3e3;--minor:#36694a;--minor-bg:#e9f4ed;
--mark:#ffe58a;--sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PT Sans",Roboto,"Helvetica Neue",Arial,sans-serif;
--serif:"Iowan Old Style","PT Serif",Charter,Georgia,"Times New Roman",serif}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 var(--sans);font-variant-numeric:tabular-nums}
a{color:var(--link)}a:focus-visible,button:focus-visible,select:focus-visible,input:focus-visible,[tabindex]:focus-visible{outline:3px solid #6d8fd6;outline-offset:2px}
.muted{color:var(--mute)}
.top{background:var(--paper);border-bottom:1px solid var(--rule);padding:28px max(24px,calc((100vw - 1320px)/2)) 20px}
.brand{font-weight:700;letter-spacing:.01em}.brand span{font-weight:400;color:var(--mute);margin-left:.5em}
.top h1{font-family:var(--serif);font-weight:600;font-size:2.1rem;line-height:1.2;margin:.35em 0 .15em;max-width:40ch}
.verdict{font-size:1.25rem;margin:0 0 16px}.v-no{color:var(--crit)}.v-conditional{color:var(--major)}.v-yes{color:var(--minor)}
.facts{display:flex;flex-wrap:wrap;gap:6px 22px;color:var(--mute)}.facts b{color:var(--ink);font-size:1.15rem;margin-right:.25em}
.facts .s-CRITICAL b{color:var(--crit)}.facts .s-MAJOR b{color:var(--major)}.facts .s-MINOR b{color:var(--minor)}
.bar{display:flex;height:6px;margin:14px 0 12px;max-width:640px;gap:2px}.bar i{display:block;min-width:4px}
.bar .s-CRITICAL{background:var(--crit)}.bar .s-MAJOR{background:#d9a441}.bar .s-MINOR{background:#7fb08e}
.status{display:flex;flex-wrap:wrap;gap:6px 20px;font-size:.92rem}.status span:not(.muted){font-weight:600}
.tabs{position:sticky;top:0;z-index:5;background:var(--paper);border-bottom:1px solid var(--rule);display:flex;gap:2px;
overflow-x:auto;padding:0 max(16px,calc((100vw - 1320px)/2 - 8px))}
.tabs button{font:inherit;background:none;border:0;border-bottom:3px solid transparent;padding:12px 14px;color:var(--mute);cursor:pointer;white-space:nowrap}
.tabs button[aria-selected=true]{color:var(--ink);border-bottom-color:var(--ink);font-weight:600}
main{max-width:1320px;margin:0 auto;padding:20px 24px 40px}
.tab{display:none}.tab.on{display:block}
.lead{max-width:75ch;color:var(--mute);margin-top:0}
.filters{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:14px}
.chip{font:inherit;font-size:.9rem;border:1px solid var(--rule);background:var(--paper);padding:5px 12px;border-radius:999px;cursor:pointer;color:var(--mute)}
.chip[aria-pressed=true].s-CRITICAL{background:var(--crit-bg);border-color:var(--crit);color:var(--crit)}
.chip[aria-pressed=true].s-MAJOR{background:var(--major-bg);border-color:var(--major);color:var(--major)}
.chip[aria-pressed=true].s-MINOR{background:var(--minor-bg);border-color:var(--minor);color:var(--minor)}
select,input[type=search]{font:inherit;font-size:.92rem;padding:5px 8px;border:1px solid var(--rule);border-radius:6px;background:var(--paper);color:var(--ink)}
input[type=search]{min-width:220px}
.split{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:24px;align-items:start}
.list{display:flex;flex-direction:column;gap:12px}
.defect{background:var(--paper);border:1px solid var(--rule);border-left:5px solid var(--rule);border-radius:4px;padding:14px 16px;scroll-margin-top:70px}
.defect.s-CRITICAL{border-left-color:var(--crit)}.defect.s-MAJOR{border-left-color:#d9a441}.defect.s-MINOR{border-left-color:#7fb08e}
.defect.sel{box-shadow:0 0 0 2px var(--ink)}
.defect header{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 10px}
.defect h3{flex-basis:100%;margin:.2em 0 .4em;font-size:1.05rem;line-height:1.35}
.sev{font-size:.82rem;font-weight:700}.s-CRITICAL .sev{color:var(--crit)}.s-MAJOR .sev{color:var(--major)}.s-MINOR .sev{color:var(--minor)}
.fid{font-size:.82rem;color:var(--mute)}
.ev{margin:6px 0;padding:6px 0 6px 12px;border-left:2px solid var(--rule)}
.ev q{display:block;font-family:var(--serif);quotes:"«" "»";margin-bottom:2px}.evlab{display:none}
.loc{font-size:.84rem;text-decoration:none;color:var(--link);cursor:pointer}.loc:hover{text-decoration:underline}
.why,.fix{margin:.5em 0;max-width:80ch}.defect footer{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:.82rem;color:var(--mute);margin-top:8px}
.doc{position:sticky;top:58px;max-height:calc(100vh - 76px);overflow:auto;background:var(--paper);border:1px solid var(--rule);border-radius:4px}
.dochead{position:sticky;top:0;background:var(--paper);border-bottom:1px solid var(--rule);padding:8px 12px;font-size:.85rem;color:var(--mute);z-index:1}
.doc ol{list-style:none;margin:0;padding:6px 0;font-family:var(--serif);font-size:.93rem}
.doc li{display:grid;grid-template-columns:3.2em minmax(0,1fr) auto;gap:8px;padding:1px 10px 1px 0;scroll-margin-top:120px}
.doc .ln{color:#9aa3b1;text-align:right;font-family:var(--sans);font-size:.78rem;padding-top:3px;user-select:none}
.doc li.has .ln{color:var(--ink);font-weight:700}
.doc li.h .tx{font-family:var(--sans);font-weight:700;padding-top:6px}.doc li.h2 .tx{font-size:1.1rem}.doc li.h3 .tx{font-size:1rem}
.doc li.row .tx{display:flex;flex-wrap:wrap;gap:2px 0;font-size:.88rem}.doc li.row .c{padding:0 8px;border-left:1px solid var(--rule)}
.doc li.row .c:first-child{border-left:0;padding-left:0}
.doc li.code .tx{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.82rem;color:var(--mute)}
.doc li.hl .tx{background:var(--mark);box-shadow:-4px 0 0 var(--mark),4px 0 0 var(--mark)}
.mks{display:flex;gap:3px;flex-wrap:wrap;justify-content:flex-end;max-width:180px}
.mk{font-family:var(--sans);font-size:.72rem;line-height:1.6;padding:0 5px;border-radius:3px;text-decoration:none;color:#fff}
.mk.s-CRITICAL{background:var(--crit)}.mk.s-MAJOR{background:#c07f15}.mk.s-MINOR{background:var(--minor)}
.more{font-size:.72rem;color:var(--mute)}
.scroll{overflow-x:auto}
table{border-collapse:collapse;background:var(--paper);font-size:.9rem}th,td{border:1px solid var(--rule);padding:6px 8px;vertical-align:top;text-align:left}
th{background:#eef1f5;font-weight:600}
.matrix td:nth-child(n+4):not(:last-child){text-align:center;font-weight:700}.matrix td:first-child{white-space:nowrap}
.matrix .sum{min-width:240px}.st-ok{color:var(--minor)}.st-warn{color:var(--major);background:var(--major-bg)}.st-fail{color:var(--crit);background:var(--crit-bg)}
.st-na,.st-x{color:#a0a8b4}
.cnt{display:inline-block;min-width:1.6em;text-align:center;font-size:.78rem;border-radius:3px;margin-right:3px;color:#fff;padding:0 4px}
.cnt.s-CRITICAL{background:var(--crit)}.cnt.s-MAJOR{background:#c07f15}.cnt.s-MINOR{background:var(--minor)}
.cards{margin-top:22px;display:flex;flex-direction:column;gap:8px}
.req{background:var(--paper);border:1px solid var(--rule);border-radius:4px;padding:0 14px;scroll-margin-top:70px}
.req summary{cursor:pointer;padding:10px 0;display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline}
.req .rid{font-weight:700}.req .kind{color:var(--mute);font-size:.88rem}.req .rs{flex:1 1 300px}
.req blockquote{font-family:var(--serif);margin:0 0 10px;padding:8px 12px;border-left:3px solid var(--rule);background:#fafbfc}
.req blockquote .loc{display:block;font-family:var(--sans);margin-top:4px}
.crit{padding-left:1.1em;margin:.4em 0}.crit li{margin:2px 0}.crit li.st-fail b{color:var(--crit)}.crit li.st-warn b{color:var(--major)}
.crit .st-fail,.crit .st-warn{background:none}
.rewrite{background:var(--minor-bg);padding:8px 12px;border-radius:4px}.tests{margin-top:0}
.scen td:first-child,.scen .verd{white-space:nowrap}.sc-defined .verd{color:var(--minor)}.sc-undefined .verd{color:var(--major);font-weight:600}
.sc-contradictory .verd{color:var(--crit);font-weight:700}
.qs{padding-left:0;list-style:none;max-width:90ch}.q{background:var(--paper);border:1px solid var(--rule);border-radius:4px;padding:10px 14px;margin:8px 0}
.qq{margin:0 0 4px;font-weight:600}.qid{color:var(--mute);font-weight:400;margin-right:.6em}.opts{margin:4px 0}.rel{font-size:.85rem;margin:4px 0 0}
.tab h2{font-size:1.15rem;margin:22px 0 6px}
.final{max-width:80ch}.final .big{font-size:1.3rem;font-weight:700;margin:16px 0 4px}.prio li{margin:6px 0}
.kv th{width:200px}.hash{font-size:.8rem;word-break:break-all}.log{min-width:600px}
.foot{max-width:1320px;margin:0 auto;padding:0 24px 30px;color:var(--mute);font-size:.85rem}
@media (max-width:1000px){.split{grid-template-columns:1fr}.doc{position:static;max-height:60vh}.top h1{font-size:1.6rem}}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
@media print{.tabs,.filters{display:none}.tab{display:block!important}.doc{position:static;max-height:none}.split{grid-template-columns:1fr}}
"""

JS = r"""
(function(){
const $=(s,r=document)=>r.querySelector(s),$$=(s,r=document)=>Array.from(r.querySelectorAll(s));
function show(tab){$$('.tabs button').forEach(b=>b.setAttribute('aria-selected',b.dataset.tab===tab));
  $$('.tab').forEach(t=>t.classList.toggle('on',t.id==='tab-'+tab));}
$$('.tabs button').forEach(b=>b.addEventListener('click',()=>{show(b.dataset.tab);history.replaceState(null,'','#'+b.dataset.tab)}));
function hl(lines){$$('.doc li.hl').forEach(l=>l.classList.remove('hl'));let first=null;
  lines.forEach(n=>{const li=document.getElementById('L'+n);if(li){li.classList.add('hl');first=first||li}});
  if(first){const box=$('.doc');box.scrollTo({top:first.offsetTop-box.clientHeight/3,behavior:'smooth'})}}
function select(fid,scroll){const el=document.getElementById(fid);if(!el)return;show('defects');
  $$('.defect.sel').forEach(d=>d.classList.remove('sel'));el.classList.add('sel');
  hl((el.dataset.lines||'').split(',').filter(Boolean));if(scroll)el.scrollIntoView({block:'center',behavior:'smooth'})}
$$('.defect').forEach(d=>{d.addEventListener('click',e=>{if(e.target.closest('a'))return;select(d.id,false)});
  d.addEventListener('keydown',e=>{if(e.key==='Enter')select(d.id,false)})});
document.addEventListener('click',e=>{const a=e.target.closest('[data-fid]');if(a){e.preventDefault();select(a.dataset.fid,true);return}
  const g=e.target.closest('[data-goto]');if(g){e.preventDefault();show('defects');hl([g.dataset.goto])}});
const st={sev:new Set(['CRITICAL','MAJOR','MINOR'])};
function apply(){const q=$('#fq').value.trim().toLowerCase(),c=$('#fcrit').value,s=$('#fscope').value;let n=0;
  $$('.defect').forEach(d=>{const ok=st.sev.has(d.dataset.sev)&&(!c||d.dataset.crit===c)&&(!s||d.dataset.scope===s)&&(!q||d.textContent.toLowerCase().includes(q));
  d.hidden=!ok;if(ok)n++});$('#fcount').textContent='Показано: '+n}
$$('.chip').forEach(b=>b.addEventListener('click',()=>{const on=b.getAttribute('aria-pressed')!=='true';b.setAttribute('aria-pressed',on);
  on?st.sev.add(b.dataset.sev):st.sev.delete(b.dataset.sev);apply()}));
['#fq','#fcrit','#fscope'].forEach(s=>$(s).addEventListener('input',apply));apply();
const h=location.hash.slice(1);if(['reqs','scen','questions','final','passport'].includes(h))show(h);
else if(/^D-\d+$/.test(h))select(h,true);
})();
"""
