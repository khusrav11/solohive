"""Общие части агентов: промпты, «сырые» схемы ответов LLM, привязка цитат к документу."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .. import criteria as C
from rapidfuzz import fuzz

from ..document import SourceDocument, normalize
from ..models import Evidence, Finding, Requirement, Severity, Status

# --- промпты ---------------------------------------------------------------------------------------

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def system_prompt() -> str:
    def fmt(items):
        return "\n".join(f"- {c.code} — {c.name} ({c.source}): {c.question}" for c in items)

    return (load_prompt("system")
            .replace("{INDIVIDUAL}", fmt(C.INDIVIDUAL))
            .replace("{SET}", fmt(C.SET))
            .replace("{SEVERITY}", C.SEVERITY_RUBRIC))


def fill(template: str, **values: str) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{" + key + "}", value)
    return out


def requirements_brief(reqs: list[Requirement]) -> str:
    return "\n".join(
        f"{r.rid} | {r.doc_id or '—'} | {r.kind} | строки {r.line_start}–{r.line_end} | {r.summary}"
        for r in reqs
    )


# --- «сырые» ответы модели (мягкая валидация) ---------------------------------------------------------

_SEV_MAP = {"critical": "CRITICAL", "blocker": "CRITICAL", "критичный": "CRITICAL", "критический": "CRITICAL",
            "major": "MAJOR", "high": "MAJOR", "существенный": "MAJOR", "серьёзный": "MAJOR",
            "minor": "MINOR", "low": "MINOR", "замечание": "MINOR", "незначительный": "MINOR", "medium": "MAJOR"}
_STATUS_MAP = {"ok": "ok", "pass": "ok", "passed": "ok", "✅": "ok", "good": "ok",
               "warn": "warn", "warning": "warn", "partial": "warn", "⚠️": "warn",
               "fail": "fail", "failed": "fail", "violation": "fail", "❌": "fail", "bad": "fail",
               "na": "na", "n/a": "na", "none": "na", "not_applicable": "na"}


def norm_severity(value: Any) -> Severity:
    v = str(value or "").strip().lower()
    return Severity(_SEV_MAP.get(v, v.upper() if v.upper() in Severity.__members__ else "MAJOR"))


def norm_status(value: Any) -> Status:
    v = str(value or "").strip().lower()
    return Status(_STATUS_MAP.get(v, "na"))


class Loose(BaseModel):
    model_config = ConfigDict(extra="ignore")


class RawCriterion(Loose):
    status: str = "na"
    comment: str = ""


class RawDefect(Loose):
    criterion: str = "complete"
    severity: str = "MAJOR"
    title: str = ""
    quote: str = ""
    quotes: list[str] = Field(default_factory=list)
    related_quotes: list[str] = Field(default_factory=list)
    explanation: str = ""
    recommendation: str = ""

    @field_validator("quotes", "related_quotes", mode="before")
    @classmethod
    def _listify(cls, v):
        if v is None:
            return []
        return [v] if isinstance(v, str) else [str(x) for x in v if x]

    def all_quotes(self) -> list[str]:
        out = []
        for q in [self.quote, *self.quotes, *self.related_quotes]:
            q = (q or "").strip()
            if q and q not in out:
                out.append(q)
        return out


def coerce_criteria(raw: Any) -> dict[str, RawCriterion]:
    out: dict[str, RawCriterion] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(v, dict):
                out[k] = RawCriterion(**{kk: str(vv) for kk, vv in v.items() if kk in ("status", "comment")})
            else:
                out[k] = RawCriterion(status=str(v))
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and ("criterion" in item or "code" in item):
                out[str(item.get("criterion") or item.get("code"))] = RawCriterion(
                    status=str(item.get("status", "na")), comment=str(item.get("comment", "")))
    return out


# --- превращение сырого дефекта в доказательную находку ----------------------------------------------------

def ground_evidence(doc: SourceDocument, quotes: list[str], hint_line: int | None = None) -> list[Evidence]:
    evidence: list[Evidence] = []
    for q in quotes:
        q_clean = re.sub(r"^L?\d+\|\s*", "", q.strip()).strip(" «»\"")
        m = doc.find(q_clean, hint_line=hint_line)
        if m:
            quote = q_clean if m.exact else doc.excerpt(m.line_start, m.line_end)
            evidence.append(Evidence(quote=quote, line=m.line_start, line_end=m.line_end,
                                     location=doc.location(m.line_start, m.line_end), verified=True,
                                     match_score=m.score))
        else:
            evidence.append(Evidence(quote=q_clean, verified=False, location="не найдено в документе"))
    # подтверждённые — вперёд, дубликаты по строке — убираем
    uniq, seen = [], set()
    for e in sorted(evidence, key=lambda e: not e.verified):
        key = (e.line, e.quote[:40]) if e.verified else (None, e.quote)
        if key not in seen:
            seen.add(key)
            uniq.append(e)
    return uniq


def defect_to_finding(doc: SourceDocument, raw: RawDefect, *, scope: str, source: str,
                      req: Requirement | None = None) -> Finding:
    quotes = raw.all_quotes()
    evidence = ground_evidence(doc, quotes, hint_line=req.line_start if req else None)
    if req and req.grounded and not any(e.verified for e in evidence):
        # Модель слегка перефразировала цитату. Принимаем, только если она близка к тексту самого
        # требования (≥ 70%); выдуманный текст останется неподтверждённым и будет отброшен.
        rq = normalize(req.quote)
        best = max((fuzz.partial_ratio(normalize(x), rq) for x in quotes), default=0.0)
        if best >= 70:
            evidence.insert(0, Evidence(quote=req.quote, line=req.line_start, line_end=req.line_end,
                                        location=req.location, verified=True, match_score=float(best)))
    title = raw.title.strip() or (raw.explanation.strip()[:90] if raw.explanation else "Дефект требования")
    return Finding(
        scope="requirement" if scope == "requirement" else "set",
        rids=[req.rid] if req else [],
        criterion=C.canonical(raw.criterion, scope),
        severity=norm_severity(raw.severity),
        title=title.rstrip("."),
        explanation=raw.explanation.strip(),
        recommendation=raw.recommendation.strip(),
        evidence=evidence,
        sources=[source],
    )


def rule_hints(findings: list[Finding]) -> str:
    """Короткий список находок правил для LLM-агентов: не дублировать, а углублять."""
    rows = [f for f in findings if f.status == "active" and any(x.startswith("rule:") for x in f.sources)]
    if not rows:
        return ""
    lines = [f"- {f.severity.value} | строка {f.primary.line if f.primary else '—'} | {f.title}" for f in rows]
    return ("Детерминированные правила уже нашли эти дефекты (они попадут в отчёт автоматически). Не повторяй их; "
            "сосредоточься на смысловых дефектах, которые правилами не ловятся. Упоминай их, только если "
            "добавляешь новый смысл (последствия, связь с другими требованиями):\n" + "\n".join(lines))
