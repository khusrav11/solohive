"""✍️ Пчела-редактор: по ответам на уточняющие вопросы пересматривает дефекты и пишет исправленную редакцию требований."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import Field

from ..llm.base import BaseLLM
from ..models import (AcceptanceCase, Answer, Clarification, Report, Resolution, RevisedRequirement,
                      Severity, Verdict)
from .common import Loose, fill, load_prompt, requirements_brief, system_prompt
from .critic import findings_block


class RawResolution(Loose):
    fid: str
    status: str = "open"
    comment: str = ""


class RawRevised(Loose):
    rid: str = ""
    doc_id: str | None = None
    text: str = ""
    fixes: list[str] = Field(default_factory=list)


class RawNew(Loose):
    id: str = ""
    section: str = ""
    text: str = ""
    fixes: list[str] = Field(default_factory=list)


class RawAcceptance(Loose):
    given: str = ""
    when: str = ""
    then: str = ""
    covers: list[str] = Field(default_factory=list)


class ReviseResponse(Loose):
    resolutions: list[RawResolution] = Field(default_factory=list)
    revised: list[RawRevised] = Field(default_factory=list)
    new_requirements: list[RawNew] = Field(default_factory=list)
    acceptance: list[RawAcceptance] = Field(default_factory=list)
    summary: str = ""


# --- ответы: файл или консоль ------------------------------------------------------------------------------

def load_answers(path: str | Path) -> dict[str, str]:
    """Читает ответы: JSON {"Q-01": "…"} или текст со строками «Q-01: ответ»."""
    text = Path(path).expanduser().read_text(encoding="utf-8")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return {k.strip().upper(): str(v).strip() for k, v in data.items() if str(v).strip()}
        if isinstance(data, list):
            return {str(d["qid"]).upper(): str(d["answer"]).strip() for d in data if d.get("answer")}
    except json.JSONDecodeError:
        pass
    out: dict[str, str] = {}
    current = None
    for line in text.splitlines():
        m = re.match(r"^\s*(Q-\d+)\s*[:.)-]\s*(.*)$", line, re.I)
        if m:
            current = m.group(1).upper()
            out[current] = m.group(2).strip()
        elif current and line.strip():
            out[current] += " " + line.strip()
    return {k: v for k, v in out.items() if v}


def ask_in_console(report: Report) -> dict[str, str]:
    """Интерактивно задаёт вопросы в консоли. Enter — пропустить, «стоп» — закончить."""
    answers: dict[str, str] = {}
    print("\n💬 Агент задаёт уточняющие вопросы. Ответьте текстом или буквой варианта; "
          "Enter — пропустить, «стоп» — закончить.\n")
    for q in report.questions:
        print(f"[{q.qid}] ({q.addressee}) {q.question}")
        if q.why:
            print(f"    зачем: {q.why}")
        for i, o in enumerate(q.options):
            print(f"    {chr(0x430 + i)}) {o}")
        try:
            ans = input("  ответ > ").strip()
        except EOFError:
            break
        if ans.lower() in ("стоп", "stop", "q"):
            break
        if ans:
            # буква варианта → текст варианта
            if len(ans) == 1 and q.options and 0 <= ord(ans.lower()) - 0x430 < len(q.options):
                ans = q.options[ord(ans.lower()) - 0x430]
            answers[q.qid] = ans
        print()
    return answers


# --- пересмотр ------------------------------------------------------------------------------------------------

async def revise(report: Report, answers: dict[str, str], llm: BaseLLM) -> Clarification:
    from ..pipeline import compute_verdict  # локальный импорт, чтобы избежать цикла

    qa_list = [Answer(qid=q.qid, question=q.question, answer=answers.get(q.qid, "")) for q in report.questions]
    qa = "\n".join(
        f"{a.qid} [{', '.join(q.related) or '—'}] {a.question}\n   Ответ: {a.answer or '(нет ответа)'}"
        + (f"\n   Варианты: {'; '.join(f'{chr(0x430 + i)}) {o}' for i, o in enumerate(q.options))}" if q.options else "")
        for a, q in zip(qa_list, report.questions)
    )
    width = len(str(len(report.source_lines)))
    numbered = "\n".join(f"L{i:0{width}d}| {t}" for i, t in enumerate(report.source_lines, start=1))
    user = fill(load_prompt("revise"), TITLE=report.passport.document_title, DOC=numbered,
                ALL_REQS=requirements_brief(report.requirements), FINDINGS=findings_block(report.findings), QA=qa)
    resp = await llm.generate_json(system_prompt(), user, ReviseResponse, stage="revise")

    active = {f.fid: f for f in report.active_findings}
    req_ids = {r.rid: r for r in report.requirements}
    resolutions = []
    for r in resp.resolutions:
        if r.fid.strip() in active:
            st = r.status.strip().lower()
            resolutions.append(Resolution(fid=r.fid.strip(), status=st if st in ("resolved", "partial", "open") else "open",
                                          comment=r.comment.strip()))
    revised = [RevisedRequirement(rid=x.rid.strip() if x.rid.strip() in req_ids else "",
                                  doc_id=x.doc_id or (req_ids[x.rid.strip()].doc_id if x.rid.strip() in req_ids else None),
                                  section=req_ids[x.rid.strip()].section if x.rid.strip() in req_ids else "",
                                  text=x.text.strip(), fixes=[f for f in x.fixes if f in active])
               for x in resp.revised if x.text.strip()]
    revised += [RevisedRequirement(doc_id=x.id.strip() or None, section=x.section.strip(), text=x.text.strip(),
                                   fixes=[f for f in x.fixes if f in active], is_new=True)
                for x in resp.new_requirements if x.text.strip()]
    acceptance = [AcceptanceCase(given=a.given.strip(), when=a.when.strip(), then=a.then.strip(), covers=a.covers)
                  for a in resp.acceptance if a.then.strip()]

    # вердикт после уточнения: устранённые находки больше не учитываются
    resolved = {r.fid for r in resolutions if r.status == "resolved"}
    remaining = [f.model_copy() for f in report.findings if f.fid not in resolved]
    after: Verdict = compute_verdict(report.requirements, remaining)
    return Clarification(answers=[a for a in qa_list if a.answer], resolutions=resolutions, revised=revised,
                         acceptance=acceptance, summary=resp.summary.strip(), verdict_after=after)


def revised_document(report: Report) -> str:
    """Исправленная редакция требований — отдельный markdown-документ для аналитика."""
    cl = report.clarification
    if cl is None:
        return ""
    P = report.passport
    L = [f"# {P.document_title} — исправленная редакция требований", "",
         f"Подготовлено агентом SoloHive по результатам проверки и ответов на уточняющие вопросы "
         f"({len(cl.answers)} из {len(report.questions)}). Исходный файл: `{P.document_file}`.", "",
         "Плейсхолдеры [уточнить: …] — вопросы, на которые ещё нет ответа.", ""]
    changed = [r for r in cl.revised if not r.is_new]
    new = [r for r in cl.revised if r.is_new]
    if changed:
        L += ["## Изменённые требования", ""]
        for r in changed:
            orig = next((q for q in report.requirements if q.rid == r.rid), None)
            L += [f"### {r.doc_id or r.rid}" + (f" ({r.section})" if r.section else ""), ""]
            if orig:
                L += [f"**Было:** {orig.quote}", ""]
            L += [f"**Стало:** {r.text}", ""]
            if r.fixes:
                L += [f"*Устраняет: {', '.join(r.fixes)}*", ""]
    if new:
        L += ["## Новые требования", ""]
        for r in new:
            L += [f"- **{r.doc_id or 'Новое'}**" + (f" ({r.section})" if r.section else "") + f": {r.text}"
                  + (f" *(устраняет: {', '.join(r.fixes)})*" if r.fixes else "")]
        L.append("")
    if cl.acceptance:
        L += ["## Критерии приёмки", "", "| Требование | Дано | Когда | Ожидается |", "| :--- | :--- | :--- | :--- |"]
        for a in cl.acceptance:
            L.append(f"| {', '.join(a.covers) or '—'} | {a.given} | {a.when} | {a.then} |".replace("\n", " "))
        L.append("")
    return "\n".join(L)


def severity_left(report: Report) -> dict[str, int]:
    resolved = {r.fid for r in (report.clarification.resolutions if report.clarification else []) if r.status == "resolved"}
    left = [f for f in report.active_findings if f.fid not in resolved]
    return {s.value: sum(1 for f in left if f.severity == s) for s in Severity}
