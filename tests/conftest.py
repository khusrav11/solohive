"""Общие фикстуры: mock-модель, которая отвечает по сценарию для каждого этапа роя."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from solohive.config import Settings  # noqa: E402
from solohive.llm.mock import MockLLM  # noqa: E402

SAMPLE = ROOT / "examples" / "requirements.md"


def _segment(stage, system, user):
    """Нарезка: берём номерные пункты Ф-N и строки критериев приёмки из пронумерованного текста."""
    reqs = []
    for m in re.finditer(r"^L(\d+)\| \* \*\*(Ф-\d)\.\*\* (.+)$", user, re.M):
        reqs.append({"doc_id": m.group(2), "section": "Часть A › Функциональные требования", "kind": "functional",
                     "line_start": int(m.group(1)), "line_end": int(m.group(1)), "quote": m.group(3),
                     "summary": m.group(3)[:60]})
    for m in re.finditer(r"^L(\d+)\| (Сервис передаёт push.+)$", user, re.M):
        reqs.append({"doc_id": None, "section": "Нефункциональные требования", "kind": "nonfunctional",
                     "line_start": int(m.group(1)), "line_end": int(m.group(1)), "quote": m.group(2), "summary": "НФТ"})
    for m in re.finditer(r"^L(\d+)\| \| REST type \| GET \|$", user, re.M):
        reqs.append({"doc_id": None, "section": "Часть B › Общие параметры", "kind": "api",
                     "line_start": int(m.group(1)), "line_end": int(m.group(1)), "quote": "REST type | GET",
                     "summary": "Метод GET"})
    # одна «галлюцинация»: цитаты нет в документе и строки нет — должна отброситься
    reqs.append({"doc_id": "Ф-99", "section": "?", "kind": "functional", "line_start": 9999, "line_end": 9999,
                 "quote": "Система отправляет SMS каждому клиенту банка", "summary": "выдумка"})
    return {"requirements": reqs}


def _review(stage, system, user):
    batch = user.split("Требования для проверки")[-1]
    rids = re.findall(r"\[(REQ-\d+)\]", batch)
    reviews = []
    for rid in rids:
        crit = {c: {"status": "ok", "comment": "ок"} for c in
                ["necessary", "appropriate", "unambiguous", "complete", "singular", "feasible", "verifiable",
                 "correct", "conforming", "consistent"]}
        defects = []
        if "Ф-3" in batch.split(f"[{rid}]")[1].split("\n")[0]:
            crit["consistent"] = {"status": "fail", "comment": "конфликт с Ф-4"}
            defects.append({"criterion": "consistent", "severity": "critical",
                            "title": "Лимит одного push в месяц противоречит Ф-4",
                            "quote": "На карту приходится максимум один такой push за календарный месяц",
                            "related_quotes": ["следующее достижение вызывает push независимо от истории предыдущей настройки"],
                            "explanation": "Ф-4 допускает второй push.", "recommendation": "Определить приоритет правил."})
            defects.append({"criterion": "complete", "severity": "MAJOR", "title": "Выдуманная цитата",
                            "quote": "клиенту приходит SMS и email одновременно", "explanation": "x"})
        reviews.append({"rid": rid, "criteria": crit, "defects": defects, "rewrite": "",
                        "test_ideas": ["Дано порог 50 000 — Действие покупка 1 500 — Ожидается push"],
                        "questions": ["Какой часовой пояс?"]})
    return {"reviews": reviews}


def _set(stage, system, user):
    return {"set_criteria": {"set_consistent": {"status": "fail", "comment": "есть противоречия"},
                             "set_complete": {"status": "warn", "comment": "пробелы"}},
            "defects": [{"criterion": "set_consistent", "severity": "CRITICAL",
                         "title": "Критерий приёмки противоречит Ф-2 по месяцу учёта",
                         "quotes": ["Месяц определяется по дате совершения покупки в Алматы",
                                    "Февральский счётчик вырос на 4 000 ₸"],
                         "explanation": "Покупка 31 января должна учитываться в январе.",
                         "recommendation": "Исправить критерий или правило."},
                        {"criterion": "set_complete", "severity": "MAJOR", "title": "Нет метода чтения настроек",
                         "quotes": ["Приложение читает и сохраняет настройки через дочерний документ"],
                         "explanation": "В части B описано только сохранение.", "recommendation": "Добавить GET."}],
            "questions": ["Нужен ли метод чтения настроек?"]}


def _critic(stage, system, user):
    ids = re.findall(r"^(F-\d{3}) \|", user, re.M)
    return {"decisions": [{"fid": ids[-1], "action": "severity", "severity": "MINOR", "reason": "тест"}] if ids else []}


def _interview(stage, system, user):
    ids = re.findall(r"^(F-\d{3}) \|", user, re.M)
    return {"questions": [{"addressee": "Бизнес / Product owner", "question": "Какой месяц учитывать?",
                           "why": "Противоречие Ф-2 и приёмки", "options": ["месяц покупки", "месяц проведения"],
                           "related": ids[:2]}],
            "verdict": {"summary": "Документ не готов.", "dev_comment": "Противоречия.", "test_comment": "Нет ожиданий.",
                        "priorities": [{"fids": ids[:1], "action": "Устранить противоречия Ф-3/Ф-4"}]}}


def _scenarios(stage, system, user):
    return {"scenarios": [
        {"title": "Покупка ровно до порога", "given_when": "Дано: расходы 48 500, порог 50 000; Когда: покупка 1 500",
         "spec_says": "Ф-3: push при достижении", "verdict": "defined",
         "quotes": ["равной порогу или выше, клиент получает push"], "defect": None},
        {"title": "Возврат январской покупки в феврале", "given_when": "Дано: push за январь отправлен; Когда: 5 февраля возврат",
         "spec_says": "документ не определяет повторный push", "verdict": "undefined",
         "quotes": ["Возврат уменьшает расходы месяца исходной покупки"],
         "defect": {"criterion": "complete", "severity": "MAJOR", "title": "Не определён повторный push после возврата",
                    "explanation": "После возврата сумма снова ниже порога.", "recommendation": "Описать правило."}},
    ]}


RESPONDERS = {"segment": _segment, "review": _review, "set:": _set, "scenarios": _scenarios, "critic": _critic,
              "interview": _interview}


@pytest.fixture
def settings(tmp_path):
    return Settings(provider="mock", model="mock-model", cache_dir=None, review_batch_size=2)


@pytest.fixture
def mock_llm(settings):
    return MockLLM(settings, RESPONDERS)
