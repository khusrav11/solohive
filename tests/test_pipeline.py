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
    data = json.loads(next(p for p in paths if p.suffix == ".json").read_text(encoding="utf-8"))
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


def test_scenarios_reported_and_linked(settings, mock_llm, tmp_path):
    report = run(settings, mock_llm)
    assert [s.verdict for s in report.scenarios] == ["defined", "undefined"]
    assert report.scenarios[0].evidence and report.scenarios[0].evidence[0].verified
    assert report.scenarios[1].finding.startswith("D-")
    md = write_report(report, tmp_path / "r.md")[0].read_text(encoding="utf-8")
    assert "### 4.3. Сценарный анализ" in md and "S-02" in md


def test_rule_hints_passed_to_llm_agents(settings, mock_llm):
    run(settings, mock_llm)
    review_prompts = [u for stage, _, u in mock_llm.prompts if stage.startswith(("review", "set:", "scenarios"))]
    assert review_prompts and all("Детерминированные правила уже нашли" in u for u in review_prompts)
    assert all("HTTP GET" in u for u in review_prompts)


def test_html_report_is_selfcontained_and_linked(settings, mock_llm, tmp_path):
    report = run(settings, mock_llm)
    paths = write_report(report, tmp_path / "r.md")
    assert [p.suffix for p in paths] == [".md", ".html", ".json"]
    page = paths[1].read_text(encoding="utf-8")
    assert "<script src" not in page and "<link" not in page          # без внешних ресурсов
    first = report.active_findings[0]
    assert f'id="{first.fid}"' in page and f'data-fid="{first.fid}"' in page   # дефект ↔ пометка на полях
    assert 'id="L39"' in page
