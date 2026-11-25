"""🐝 Пчёлы-рецензенты: проверка каждого требования по 10 критериям ISO 29148 / IEEE 830 (пакетами, параллельно)."""

from __future__ import annotations

import asyncio

from pydantic import Field

from .. import criteria as C
from ..document import SourceDocument
from ..llm.base import BaseLLM, FatalLLMError
from ..models import CriterionEval, Finding, Requirement, RequirementReview
from .common import (Loose, RawDefect, coerce_criteria, defect_to_finding, fill, load_prompt, norm_status,
                     requirements_brief, system_prompt)


class RawReview(Loose):
    rid: str
    criteria: dict | list = Field(default_factory=dict)
    defects: list[RawDefect] = Field(default_factory=list)
    rewrite: str = ""
    test_ideas: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)


class ReviewResponse(Loose):
    reviews: list[RawReview] = Field(default_factory=list)


def _batches(reqs: list[Requirement], size: int) -> list[list[Requirement]]:
    return [reqs[i: i + size] for i in range(0, len(reqs), size)]


def _batch_block(batch: list[Requirement]) -> str:
    return "\n\n".join(
        f"[{r.rid}] ID в документе: {r.doc_id or '— (нет)'} | тип: {r.kind} | раздел: {r.section} | "
        f"строки {r.line_start}–{r.line_end}\nЦитата: «{r.quote}»"
        for r in batch
    )


async def review_all(doc: SourceDocument, reqs: list[Requirement], llm: BaseLLM, batch_size: int
                     ) -> tuple[dict[str, RequirementReview], list[Finding], list[str], list[str]]:
    """Возвращает: оценки по требованиям, находки, вопросы-кандидаты, предупреждения."""
    template = load_prompt("review")
    numbered = doc.numbered()
    brief = requirements_brief(reqs)
    by_id = {r.rid: r for r in reqs}

    async def run_batch(idx: int, batch: list[Requirement]):
        user = fill(template, TITLE=doc.title, DOC=numbered, ALL_REQS=brief, BATCH=_batch_block(batch))
        return await llm.generate_json(system_prompt(), user, ReviewResponse, stage=f"review#{idx + 1}")

    batches = _batches(reqs, batch_size)
    results = await asyncio.gather(*(run_batch(i, b) for i, b in enumerate(batches)), return_exceptions=True)

    reviews: dict[str, RequirementReview] = {}
    findings: list[Finding] = []
    questions: list[str] = []
    warnings: list[str] = []
    for batch, res in zip(batches, results):
        if isinstance(res, FatalLLMError):
            raise res
        if isinstance(res, Exception):
            warnings.append(f"Рецензент не обработал {batch[0].rid}–{batch[-1].rid}: {res}")
            continue
        for raw in res.reviews:
            req = by_id.get(raw.rid.strip())
            if req is None:
                continue
            crit = coerce_criteria(raw.criteria)
            evals = []
            for c in C.INDIVIDUAL:
                rc = crit.get(c.code) or next((v for k, v in crit.items() if C.canonical(k) == c.code), None)
                evals.append(CriterionEval(criterion=c.code, status=norm_status(rc.status) if rc else norm_status("na"),
                                           comment=(rc.comment if rc else "").strip()))
            reviews[req.rid] = RequirementReview(rid=req.rid, criteria=evals, rewrite=raw.rewrite.strip(),
                                                 test_ideas=[t.strip() for t in raw.test_ideas if t.strip()][:4])
            for d in raw.defects:
                findings.append(defect_to_finding(doc, d, scope="requirement", source="llm:reviewer", req=req))
            questions += [f"[{req.rid}/{req.doc_id or '—'}] {q.strip()}" for q in raw.questions if q.strip()]
    missing = [r.rid for r in reqs if r.rid not in reviews]
    if missing:
        warnings.append(f"Нет оценки рецензента для: {', '.join(missing)}")
    return reviews, findings, questions, warnings
