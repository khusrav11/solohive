"""Форматы отчёта: PDF и DOCX с кириллицей, формат по умолчанию как у входа, перевыпуск из JSON."""

import asyncio
import json

import pdfplumber
import pytest
from conftest import ROOT, SAMPLE

from solohive import cli
from solohive.pipeline import run_pipeline
from solohive.report import default_report_ext, write_report
from solohive.report.mdblocks import parse, spans, split_row

SECTIONS = ["1. Паспорт прогона", "2. Как агент нарезал требования", "7. Итог"]


@pytest.fixture
def report(settings, mock_llm):
    return asyncio.run(run_pipeline(SAMPLE, settings, llm=mock_llm))


def test_pdf_report_is_real_pdf_with_cyrillic(report, tmp_path):
    paths = write_report(report, tmp_path / "report.pdf")
    pdf = paths[0]
    assert pdf.suffix == ".pdf" and pdf.read_bytes()[:5] == b"%PDF-"
    assert {p.suffix for p in paths} == {".pdf", ".md", ".html", ".json"}
    with pdfplumber.open(pdf) as doc:
        text = "\n".join(page.extract_text() or "" for page in doc.pages)
    for s in SECTIONS:
        assert s in text, s
    assert "Уведомление о расходах по карте" in text and "₸" in text


def test_docx_report_has_headings_and_tables(report, tmp_path):
    import docx
    paths = write_report(report, tmp_path / "report.docx")
    d = docx.Document(str(paths[0]))
    text = "\n".join(p.text for p in d.paragraphs)
    for s in SECTIONS:
        assert s in text, s
    assert len(d.tables) >= 5
    assert d.tables[0].rows[0].cells[0].text == "Параметр"


@pytest.mark.parametrize("src,ext", [("a.pdf", ".pdf"), ("a.DOCX", ".docx"), ("a.md", ".md"), ("a.txt", ".md"),
                                     ("a.html", ".html")])
def test_default_report_format_follows_input(src, ext):
    assert default_report_ext(src) == ext


def test_cli_offline_writes_pdf_for_pdf_input(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code = cli.main(["--offline", "-q", "--input", str(ROOT / "examples" / "requirements.pdf")])
    assert code == 0
    out = tmp_path / "reports" / "requirements_report.pdf"
    assert out.read_bytes()[:5] == b"%PDF-"
    assert (tmp_path / "reports" / "requirements_report.json").exists()


def test_render_from_json_without_llm(report, tmp_path):
    from solohive.render import main as render_main
    js = write_report(report, tmp_path / "r.json")[0]
    assert render_main([str(js), str(tmp_path / "again.docx")]) == 0
    assert (tmp_path / "again.docx").exists() and (tmp_path / "again.md").exists()
    assert json.loads(js.read_text(encoding="utf-8"))["passport"]["document_title"]


def test_markdown_parser_handles_report_constructs():
    md = ('<a id="x"></a>\n## Заголовок\n\n> **Жирный:** текст  \n> вторая строка\n\n'
          '| A | B |\n| :--- | :---: |\n| «\\| GET \\|» | **да** |\n\n- пункт с `кодом` и [ссылкой](#x)\n\n---\n')
    kinds = [b.kind for b in parse(md)]
    assert kinds == ["heading", "quote", "table", "list", "hr"]
    table = parse(md)[2]
    assert table.rows[0][0] == "«\\| GET \\|»" and table.align == ["left", "center"]
    assert "".join(s.text for s in spans(table.rows[0][0])) == "«| GET |»"
    assert split_row("| a | b \\| c |") == ["a", "b \\| c"]
