"""Извлечение и мягкий ремонт JSON из ответа модели."""

from __future__ import annotations

import json
import re

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


class JSONExtractError(ValueError):
    pass


def extract_json(text: str):
    """Достаёт JSON-объект из ответа: допускает ```json-ограду, текст вокруг, висячие запятые."""
    if text is None:
        raise JSONExtractError("пустой ответ модели")
    candidates: list[str] = []
    stripped = text.strip()
    candidates.append(stripped)
    for m in _FENCE.finditer(text):
        candidates.append(m.group(1).strip())
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        candidates.append(stripped[start : end + 1])
    last_err: Exception | None = None
    for cand in candidates:
        for variant in (cand, _TRAILING_COMMA.sub(r"\1", cand)):
            try:
                return json.loads(variant)
            except json.JSONDecodeError as exc:
                last_err = exc
    raise JSONExtractError(f"ответ не является корректным JSON: {last_err}")
