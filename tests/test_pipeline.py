"""Сквозной прогон роя на mock-модели: структура отчёта, привязка цитат, отсев галлюцинаций."""

import asyncio
import json

from conftest import SAMPLE

from solohive.models import Severity
from solohive.pipeline import run_pipeline
from solohive.report import write_report

REQUIRED_SECTIONS = [
    "## 1. Паспорт прогона", "## 2. Как агент нарезал требования", "## 3. Проверка каждого требования",
    "## 4. Проверка всего набора требований", "## 5. Сводка", "## 6. Уточняющие вопросы", "## 7. Итог",
]


def run(settings, llm):
    return asyncio.run(run_pipeline(SAMPLE, settings, llm=llm))


def test_report_has_all_required_sections(settings, mock_llm, tmp_path):
    report = run(settings, mock_llm)
    paths = write_report(report, tmp_path / "report.md")
    md = paths[0].read_text(encoding="utf-8")
    for s in REQUIRED_SECTIONS:
        assert s in md, s
    # паспорт: документ, модель, версия агента
    assert "Уведомление о расходах по карте" in md
    assert "mock-model" in md
    assert "Версия агента" in md
    data = json.loads(paths[1].read_text(encoding="utf-8"))
    assert data["passport"]["document_lines"] > 100


def test_hallucinated_requirement_and_quote_are_rejected(settings, mock_llm):
    report = run(settings, mock_llm)
    assert all(r.doc_id != "Ф-99" for r in report.requirements)
    assert all(r.grounded for r in report.requirements)
    # находка с выдуманной цитатой привязана к требованию, а не к несуществующему тексту
    for f in report.active_findings:
        assert f.evidence and f.evidence[0].verified, f.title


def test_every_active_finding_points_to_a_place(settings, mock_llm):
    report = run(settings, mock_llm)
    assert report.active_findings
    for f in report.active_findings:
        assert f.fid.startswith("D-")
        assert f.primary.line is not None
        assert "строк" in f.primary.location


def test_contradiction_and_rules_reach_report(settings, mock_llm):
    report = run(settings, mock_llm)
    titles = " ".join(f.title for f in report.active_findings)
    assert "HTTP GET" in titles                     # правило
    assert "противоречит" in titles                 # LLM-анализ набора
    assert any(f.severity == Severity.CRITICAL for f in report.active_findings)
    assert report.verdict.dev_status == "no"
    assert report.questions and report.questions[0].related[0].startswith("D-")


def test_requirement_ids_preserved(settings, mock_llm):
    report = run(settings, mock_llm)
    ids = [r.doc_id for r in report.requirements if r.doc_id]
    assert ids[:4] == ["Ф-1", "Ф-2", "Ф-3", "Ф-4"]
    assert [r.rid for r in report.requirements][:2] == ["REQ-01", "REQ-02"]


def test_fabricated_quote_is_dropped_not_attached(settings, mock_llm):
    report = run(settings, mock_llm)
    fake = [f for f in report.findings if f.title == "Выдуманная цитата"]
    assert fake and fake[0].status == "dropped"
    assert "цитат" in fake[0].note
