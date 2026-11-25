"""🐝 Пчела-интервьюер и пчела-судья: уточняющие вопросы к бизнесу/аналитику и обоснование вердикта."""

from __future__ import annotations

from pydantic import Field

from ..document import SourceDocument
from ..llm.base import BaseLLM, FatalLLMError
from ..models import Finding, Priority, Question, Requirement, Verdict
from .common import Loose, fill, load_prompt, requirements_brief, system_prompt
from .critic import findings_block


class RawQuestion(Loose):
    addressee: str = "Системный аналитик"
    question: str = ""
    why: str = ""
    options: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)


class RawPriority(Loose):
    fids: list[str] = Field(default_factory=list)
    action: str = ""


class RawVerdict(Loose):
    summary: str = ""
    dev_comment: str = ""
    test_comment: str = ""
    priorities: list[RawPriority] = Field(default_factory=list)


class InterviewResponse(Loose):
    questions: list[RawQuestion] = Field(default_factory=list)
    verdict: RawVerdict = Field(default_factory=RawVerdict)


STATUS_RU = {"no": "НЕ ГОТОВ", "conditional": "ГОТОВ С УСЛОВИЯМИ", "yes": "ГОТОВ"}


async def interview(doc: SourceDocument, reqs: list[Requirement], findings: list[Finding],
                    verdict: Verdict, candidates: list[str], llm: BaseLLM
                    ) -> tuple[list[Question], Verdict, list[str]]:
    verdict_text = (f"разработка — {STATUS_RU[verdict.dev_status]}, тестирование — {STATUS_RU[verdict.test_status]}")
    user = fill(load_prompt("interview"), TITLE=doc.title, DOC=doc.numbered(), ALL_REQS=requirements_brief(reqs),
                FINDINGS=findings_block(findings, with_text=False), VERDICT=verdict_text,
                CANDIDATE_QUESTIONS="\n".join(f"- {q}" for q in candidates[:60]) or "—")
    active = {f.fid for f in findings if f.status == "active"}
    try:
        resp = await llm.generate_json(system_prompt(), user, InterviewResponse, stage="interview", effort="low")
    except FatalLLMError:
        raise
    except Exception as exc:  # noqa: BLE001
        return [], verdict, [f"Интервьюер не выполнен ({exc}); вопросы взяты у рецензентов."]
    questions = []
    for i, q in enumerate([q for q in resp.questions if q.question.strip()], start=1):
        questions.append(Question(qid=f"Q-{i:02d}", addressee=q.addressee.strip() or "Системный аналитик",
                                  question=q.question.strip(), why=q.why.strip(),
                                  options=[o.strip() for o in q.options if o.strip()][:4],
                                  related=[r.strip() for r in q.related if r.strip() in active]))
    verdict.summary = resp.verdict.summary.strip()
    verdict.dev_comment = resp.verdict.dev_comment.strip()
    verdict.test_comment = resp.verdict.test_comment.strip()
    verdict.priorities = [Priority(fids=[x.strip() for x in p.fids if x.strip() in active], action=p.action.strip())
                          for p in resp.verdict.priorities if p.action.strip()][:8]
    return questions, verdict, []


def fallback_questions(candidates: list[str]) -> list[Question]:
    seen, out = set(), []
    for c in candidates:
        key = c.lower()[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(Question(qid=f"Q-{len(out) + 1:02d}", addressee="Системный аналитик", question=c))
        if len(out) >= 15:
            break
    return out
