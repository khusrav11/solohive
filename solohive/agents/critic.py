"""🐝 Пчела-критик: независимая верификация — удаление ложных находок, объединение дубликатов, калибровка критичности."""

from __future__ import annotations

from pydantic import Field
from rapidfuzz import fuzz

from ..document import SourceDocument
from ..llm.base import BaseLLM, FatalLLMError
from ..models import Finding
from .common import Loose, fill, load_prompt, norm_severity, system_prompt


class Decision(Loose):
    fid: str
    action: str = "keep"
    merge_into: str | None = None
    severity: str | None = None
    reason: str = ""


class CriticResponse(Loose):
    decisions: list[Decision] = Field(default_factory=list)


def findings_block(findings: list[Finding], with_text: bool = True) -> str:
    lines = []
    for f in findings:
        if f.status != "active":
            continue
        quotes = " || ".join(f"«{e.quote[:160]}» ({e.location})" for e in f.evidence[:3])
        rids = ",".join(f.rids) or "набор"
        src = ",".join(f.sources)
        text = f" | {f.explanation[:350]}" if with_text and f.explanation else ""
        lines.append(f"{f.fid} | {f.severity.value} | {f.criterion} | {rids} | {src} | {f.title}{text} | {quotes}")
    return "\n".join(lines)


def merge_into(target: Finding, other: Finding, reason: str = "") -> None:
    """Сливает дубликат в основную находку: объединяет доказательства, источники, требования."""
    other.status = "merged"
    other.note = f"объединено с {target.fid}" + (f": {reason}" if reason else "")
    for e in other.evidence:
        if e.verified and not any(x.line == e.line for x in target.evidence):
            target.evidence.append(e)
    target.sources = list(dict.fromkeys(target.sources + other.sources))
    target.rids = list(dict.fromkeys(target.rids + other.rids))
    if other.severity.rank < target.severity.rank:
        target.severity = other.severity
    if not target.recommendation and other.recommendation:
        target.recommendation = other.recommendation


def deterministic_dedupe(findings: list[Finding]) -> int:
    """Объединяет очевидные дубликаты без LLM: та же строка-доказательство и близкий заголовок."""
    merged = 0
    active = [f for f in findings if f.status == "active"]
    for i, a in enumerate(active):
        if a.status != "active":
            continue
        a_lines = {e.line for e in a.evidence if e.verified}
        for b in active[i + 1:]:
            if b.status != "active":
                continue
            b_lines = {e.line for e in b.evidence if e.verified}
            same_place = bool(a_lines & b_lines)
            similar = fuzz.token_set_ratio(a.title.lower(), b.title.lower()) >= 82
            if same_place and similar:
                primary, dup = (a, b) if _richness(a) >= _richness(b) else (b, a)
                merge_into(primary, dup, "совпадает место и формулировка")
                merged += 1
                if dup is a:
                    break
    return merged


def _richness(f: Finding) -> int:
    return len(f.explanation) + len(f.recommendation) + (200 if any(s.startswith("llm") for s in f.sources) else 0)


async def verify(doc: SourceDocument, findings: list[Finding], llm: BaseLLM) -> tuple[int, int, list[str]]:
    """Применяет решения критика. Возвращает (удалено, объединено, предупреждения)."""
    by_id = {f.fid: f for f in findings}
    user = fill(load_prompt("critic"), TITLE=doc.title, DOC=doc.numbered(), FINDINGS=findings_block(findings))
    try:
        resp = await llm.generate_json(system_prompt(), user, CriticResponse, stage="critic", effort="low")
    except FatalLLMError:
        raise
    except Exception as exc:  # noqa: BLE001
        return 0, 0, [f"Критик не выполнен ({exc}); находки оставлены без LLM-верификации."]
    dropped = merged = 0
    for d in resp.decisions:
        f = by_id.get(d.fid.strip())
        if f is None or f.status != "active":
            continue
        action = d.action.strip().lower()
        is_rule = any(s.startswith("rule:") for s in f.sources)
        if action == "drop" and not is_rule:
            f.status = "dropped"
            f.note = d.reason.strip() or "отклонено критиком"
            dropped += 1
        elif action == "merge" and d.merge_into:
            target = by_id.get(d.merge_into.strip())
            if target and target is not f and target.status == "active":
                # правило не растворяем в LLM-находке без следа: сливаем в более полную
                merge_into(target, f, d.reason.strip())
                merged += 1
        elif action == "severity" and d.severity:
            new = norm_severity(d.severity)
            if new != f.severity:
                f.note = f"критичность {f.severity.value} → {new.value}: {d.reason.strip()}"
                f.severity = new
    return dropped, merged, []
