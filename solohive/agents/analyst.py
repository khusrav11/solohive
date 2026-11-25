"""🐝 Пчёлы-аналитики набора: непротиворечивость/прослеживаемость и полнота/реализуемость (два параллельных взгляда)."""

from __future__ import annotations

import asyncio

from pydantic import Field

from .. import criteria as C
from ..document import SourceDocument
from ..llm.base import BaseLLM, FatalLLMError
from ..models import CriterionEval, Finding
from .common import Loose, RawDefect, coerce_criteria, defect_to_finding, fill, load_prompt, norm_status, system_prompt


class SetResponse(Loose):
    set_criteria: dict | list = Field(default_factory=dict)
    defects: list[RawDefect] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)


VIEWS = {
    "consistency": ("system_consistency", "llm:system-consistency"),
    "completeness": ("system_completeness", "llm:system-completeness"),
}


async def analyze_set(doc: SourceDocument, llm: BaseLLM
                      ) -> tuple[list[CriterionEval], list[Finding], list[str], list[str]]:
    numbered = doc.numbered()

    async def run(view: str):
        prompt, _ = VIEWS[view]
        user = fill(load_prompt(prompt), TITLE=doc.title, DOC=numbered)
        return await llm.generate_json(system_prompt(), user, SetResponse, stage=f"set:{view}")

    results = await asyncio.gather(*(run(v) for v in VIEWS), return_exceptions=True)
    evals: dict[str, CriterionEval] = {}
    findings: list[Finding] = []
    questions: list[str] = []
    warnings: list[str] = []
    for view, res in zip(VIEWS, results):
        if isinstance(res, FatalLLMError):
            raise res
        if isinstance(res, Exception):
            warnings.append(f"Системный анализ ({view}) не выполнен: {res}")
            continue
        for code, rc in coerce_criteria(res.set_criteria).items():
            canon = C.canonical(code, "set")
            evals[canon] = CriterionEval(criterion=canon, status=norm_status(rc.status), comment=rc.comment.strip())
        for d in res.defects:
            findings.append(defect_to_finding(doc, d, scope="set", source=VIEWS[view][1]))
        questions += [q.strip() for q in res.questions if q.strip()]
    ordered = [evals[c.code] for c in C.SET if c.code in evals]
    return ordered, findings, questions, warnings
