"""🐝 Пчела-нарезчик: выделяет отдельные требования с точными цитатами и привязкой к строкам."""

from __future__ import annotations

import re

from pydantic import Field

from ..document import SourceDocument, clean_md
from ..llm.base import BaseLLM, FatalLLMError
from ..models import Requirement
from .common import Loose, fill, load_prompt, system_prompt

KINDS = {"functional", "nonfunctional", "data", "integration", "api", "ui", "process", "architecture",
         "goal", "acceptance", "constraint", "other"}
KIND_RU = {"functional": "Функциональное", "nonfunctional": "Нефункциональное", "data": "Данные",
           "integration": "Интеграция", "api": "API", "ui": "UI", "process": "Процесс", "architecture": "Архитектура",
           "goal": "Цель", "acceptance": "Критерий приёмки", "constraint": "Ограничение", "other": "Прочее"}


class RawReq(Loose):
    doc_id: str | None = None
    section: str = ""
    kind: str = "other"
    line_start: int | None = None
    line_end: int | None = None
    quote: str = ""
    summary: str = ""


class SegmentResponse(Loose):
    requirements: list[RawReq] = Field(default_factory=list)


async def segment(doc: SourceDocument, llm: BaseLLM) -> tuple[list[Requirement], list[str]]:
    """Возвращает требования и предупреждения. При сбое LLM — детерминированная нарезка."""
    warnings: list[str] = []
    user = fill(load_prompt("segment"), TITLE=doc.title, DOC=doc.numbered())
    try:
        resp = await llm.generate_json(system_prompt(), user, SegmentResponse, stage="segment", effort="low")
        reqs = _ground(doc, resp.requirements)
    except FatalLLMError:
        raise
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Нарезка LLM не удалась ({exc}); использована детерминированная нарезка.")
        reqs = []
    if len(reqs) < 3:
        if reqs:
            warnings.append("LLM выделила слишком мало требований; использована детерминированная нарезка.")
        reqs = fallback_segment(doc)
    return reqs, warnings


def _ground(doc: SourceDocument, raws: list[RawReq]) -> list[Requirement]:
    out: list[Requirement] = []
    seen: set[tuple[int, int, str]] = set()
    for raw in raws:
        quote = re.sub(r"^L?\d+\|\s*", "", (raw.quote or "").strip())
        m = doc.find(quote, hint_line=raw.line_start) if quote else None
        if m:
            ls, le = m.line_start, m.line_end
            if not m.exact:
                quote = doc.excerpt(ls, le, limit=600)
        elif raw.line_start and doc.line(raw.line_start):
            ls = raw.line_start
            le = raw.line_end if raw.line_end and raw.line_end >= ls and doc.line(raw.line_end) else ls
            quote = doc.excerpt(ls, le, limit=600)
        else:
            continue  # не нашли ни цитаты, ни строк — не доверяем
        key = (ls, le, quote[:60])
        if key in seen or not quote.strip():
            continue
        seen.add(key)
        kind = (raw.kind or "other").strip().lower()
        doc_id = (raw.doc_id or "").strip() or None
        if doc_id and doc_id.lower() in ("null", "none", "—", "-", "нет"):
            doc_id = None
        out.append(Requirement(
            rid="", doc_id=doc_id, section=raw.section.strip() or doc.display_section(doc.line(ls).section),
            kind=kind if kind in KINDS else "other", quote=quote.strip(),
            summary=(raw.summary or "").strip() or clean_md(quote)[:100],
            line_start=ls, line_end=le, location=doc.location(ls, le), grounded=True,
        ))
    out.sort(key=lambda r: (r.line_start or 0, r.line_end or 0))
    for i, r in enumerate(out, start=1):
        r.rid = f"REQ-{i:02d}"
    return out


_SKIP_SECTIONS = re.compile(r"общие сведения|ревью|согласу|оглавлени|содержани", re.I)
_DOC_ID = re.compile(r"^([A-ZА-ЯЁ]{1,6}[-_.]?\d{1,3}(?:\.\d+)*)\.?\s+")


def fallback_segment(doc: SourceDocument) -> list[Requirement]:
    """Детерминированная нарезка: пункты списков, абзацы (с учётом переносов строк в PDF) и строки таблиц."""
    blocks: list[tuple[int, int, str]] = []
    cur: list = []
    in_code = False
    table_header_lines = {t.header_line for t in doc.tables}

    def flush():
        if cur:
            blocks.append((cur[0].no, cur[-1].no, " ".join(clean_md(x.text) for x in cur)))
            cur.clear()

    for line in doc.lines:
        t = line.text.strip()
        if t.startswith("```"):
            in_code = not in_code
            flush()
            continue
        if in_code or not t or t.startswith("#") or t == "---":
            flush()
            continue
        if t.startswith("|"):
            flush()
            if line.no in table_header_lines or re.fullmatch(r"\|?[\s:|-]+\|?", t):
                continue
            blocks.append((line.no, line.no, clean_md(t.strip("|").replace("|", " | "))))
            continue
        starts_item = bool(re.match(r"^([*\-+•]|\d+[.)])\s", t))
        prev_ends = bool(cur) and clean_md(cur[-1].text).endswith((".", ":", ";", "!", "?"))
        if cur and (starts_item or prev_ends or cur[-1].section != line.section):
            flush()
        cur.append(line)
    flush()

    out: list[Requirement] = []
    for ls, le, text in blocks:
        line = doc.line(ls)
        if any(_SKIP_SECTIONS.search(s) for s in line.section) or len(text) < 15:
            continue
        m = _DOC_ID.match(text)
        sec = doc.display_section(line.section)
        out.append(Requirement(rid="", doc_id=m.group(1) if m else None, section=sec, kind=_guess_kind(sec),
                               quote=text, summary=text[:100], line_start=ls, line_end=le,
                               location=doc.location(ls, le), grounded=True))
    for i, r in enumerate(out, start=1):
        r.rid = f"REQ-{i:02d}"
    return out


def _guess_kind(section: str) -> str:
    # сначала по самому вложенному разделу: «Часть A. Новый функционал › Цели» — это цель, а не функционал
    for part in reversed(section.split(" › ")):
        kind = _kind_of(part.lower())
        if kind != "other":
            return kind
    return "other"


def _kind_of(s: str) -> str:
    for pattern, kind in [(r"нефункцион", "nonfunctional"), (r"функцион", "functional"), (r"данн", "data"),
                          (r"интеграц", "integration"), (r"приемк|приёмк", "acceptance"), (r"цел", "goal"),
                          (r"архитект|sequence", "architecture"), (r"ui|дизайн", "ui"), (r"bpmn|процесс", "process"),
                          (r"api|rest|парамет|статус|ответ|метод|правил|схема|request|path|query", "api")]:
        if re.search(pattern, s):
            return kind
    return "other"
