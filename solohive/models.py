"""Модели данных агента: требования, находки, вопросы, вердикт, отчёт."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    MAJOR = "MAJOR"
    MINOR = "MINOR"

    @property
    def rank(self) -> int:
        return {"CRITICAL": 0, "MAJOR": 1, "MINOR": 2}[self.value]

    @property
    def icon(self) -> str:
        return {"CRITICAL": "🔴", "MAJOR": "🟡", "MINOR": "🟢"}[self.value]

    @property
    def ru(self) -> str:
        return {"CRITICAL": "Критичный", "MAJOR": "Существенный", "MINOR": "Замечание"}[self.value]


class Status(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    NA = "na"

    @property
    def icon(self) -> str:
        return {"ok": "✅", "warn": "⚠️", "fail": "❌", "na": "➖"}[self.value]


class Evidence(BaseModel):
    """Цитата из документа с привязкой к месту."""

    quote: str
    line: int | None = None
    line_end: int | None = None
    location: str = ""
    verified: bool = False
    match_score: float = 0.0


class Requirement(BaseModel):
    rid: str                      # идентификатор в отчёте: REQ-01
    doc_id: str | None = None     # идентификатор в документе: Ф-1 (или None)
    section: str = ""             # раздел документа
    kind: str = "other"           # functional / nonfunctional / data / api / ...
    quote: str                    # точная цитата из документа
    summary: str = ""             # краткая формулировка
    line_start: int | None = None
    line_end: int | None = None
    location: str = ""
    grounded: bool = False        # цитата найдена в исходном тексте


class CriterionEval(BaseModel):
    criterion: str
    status: Status = Status.NA
    comment: str = ""


class Finding(BaseModel):
    """Дефект (нарушение критерия качества) с доказательством."""

    fid: str = ""
    scope: Literal["requirement", "set"] = "requirement"
    rids: list[str] = Field(default_factory=list)
    criterion: str
    severity: Severity
    title: str
    explanation: str = ""
    recommendation: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)   # llm:reviewer, rule:R-HTTP-SAFE ...
    status: Literal["active", "dropped", "merged"] = "active"
    note: str = ""                                     # причина удаления/объединения
    scenario: str = ""                                 # S-xx, если найдено сценарным анализом

    @property
    def primary(self) -> Evidence | None:
        return self.evidence[0] if self.evidence else None

    @property
    def verified(self) -> bool:
        return any(e.verified for e in self.evidence)


class RequirementReview(BaseModel):
    rid: str
    criteria: list[CriterionEval] = Field(default_factory=list)
    rewrite: str = ""
    test_ideas: list[str] = Field(default_factory=list)


class Scenario(BaseModel):
    """Сценарий, «проигранный» по тексту ТЗ: определяет ли документ результат."""

    sid: str = ""
    title: str
    given_when: str = ""
    spec_says: str = ""
    verdict: Literal["defined", "undefined", "contradictory"] = "undefined"
    evidence: list[Evidence] = Field(default_factory=list)
    finding: str = ""      # fid связанной находки

    @property
    def icon(self) -> str:
        return {"defined": "✅", "undefined": "❓", "contradictory": "⚔️"}[self.verdict]


class Question(BaseModel):
    qid: str = ""
    addressee: str = "Аналитик"
    question: str
    why: str = ""
    options: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)   # fid-ы находок


class Priority(BaseModel):
    fids: list[str] = Field(default_factory=list)
    action: str


class Verdict(BaseModel):
    dev_status: Literal["no", "conditional", "yes"] = "no"
    test_status: Literal["no", "conditional", "yes"] = "no"
    score: int = 0
    summary: str = ""
    dev_comment: str = ""
    test_comment: str = ""
    priorities: list[Priority] = Field(default_factory=list)


class CallRecord(BaseModel):
    stage: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency: float = 0.0
    cached: bool = False
    attempts: int = 1
    ok: bool = True
    error: str = ""


class Passport(BaseModel):
    document_title: str
    document_file: str
    document_format: str
    document_sha256: str
    document_lines: int
    run_started: str
    duration_sec: float = 0.0
    provider: str
    models: list[str] = Field(default_factory=list)
    agent_name: str
    agent_version: str
    agent_commit: str
    temperature: float
    llm_calls: int = 0
    llm_cached_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class Report(BaseModel):
    passport: Passport
    requirements: list[Requirement]
    reviews: dict[str, RequirementReview] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
    set_evals: list[CriterionEval] = Field(default_factory=list)
    scenarios: list[Scenario] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    verdict: Verdict = Field(default_factory=Verdict)
    calls: list[CallRecord] = Field(default_factory=list)
    stage_times: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @property
    def active_findings(self) -> list[Finding]:
        return [f for f in self.findings if f.status == "active"]
