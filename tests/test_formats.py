"""Форматы отчёта: PDF и DOCX с кириллицей, формат по умолчанию как у входа, перевыпуск из JSON."""

import asyncio
import json

import pdfplumber
import pytest
from conftest import ROOT, SAMPLE

from solohive import cli
from solohive.pipeline import run_pipeline
from solohive.report import default_report_ext, write_report
from solohive.report.mdblocks import parse, slug, spans, split_row

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
    assert d.tables[0].rows[0].cells[1].text == "Вердикт"              # первым идёт «Кратко»
    assert any(t.rows[0].cells[0].text == "Параметр" for t in d.tables)


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
    assert kinds == ["anchor", "heading", "quote", "table", "list", "hr"]
    table = parse(md)[3]
    assert table.rows[0][0] == "«\\| GET \\|»" and table.align == ["left", "center"]
    assert "".join(s.text for s in spans(table.rows[0][0])) == "«| GET |»"
    assert split_row("| a | b \\| c |") == ["a", "b \\| c"]
    assert [s.link for s in spans("[ссылкой](#x)")] == ["#x"]
    assert slug("4.2. Системные дефекты набора") == "42-системные-дефекты-набора"
    assert [b.kind for b in parse("текст\n<!-- pagebreak -->\nещё")] == ["para", "pagebreak", "para"]


def test_brief_summary_opens_the_report(report, tmp_path):
    md = write_report(report, tmp_path / "r.md")[0].read_text(encoding="utf-8")
    brief = md.split("## Кратко", 1)[1].split("## 1. Паспорт прогона", 1)[0]
    assert md.index("## Кратко") < md.index("## 1. Паспорт прогона")
    assert "нельзя отдавать в разработку" in brief and "### Критичные дефекты (блокеры)" in brief
    crit = [f for f in report.active_findings if f.severity.value == "CRITICAL"]
    assert all(f"[{f.fid}](#" in brief for f in crit)              # блокеры — ссылки на подробную карточку
    assert "### Что исправить в первую очередь" in brief and "Каждая из" in brief


def test_brief_summary_shows_before_and_after_clarification(report, mock_llm, tmp_path):
    from solohive.agents.reviser import revise
    report.clarification = asyncio.run(revise(report, {"Q-01": "а"}, mock_llm))
    md = write_report(report, tmp_path / "r.md")[0].read_text(encoding="utf-8")
    brief = md.split("## Кратко", 1)[1].split("## 1. Паспорт прогона", 1)[0]
    assert "| | До уточнения | После уточнения |" in brief and "После ответов на 1 вопрос устранено" in brief
    left = [f for f in report.active_findings if f.fid not in {r.fid for r in report.clarification.resolutions}]
    if left:
        assert "### Что остаётся открытым" in md and f"[{left[0].fid}](#" in md


def test_pdf_has_outline_and_working_internal_links(report, tmp_path):
    from pdfminer.utils import decode_text
    pdf = write_report(report, tmp_path / "r.pdf")[0]
    with pdfplumber.open(pdf) as doc:
        toc = [t if isinstance(t, str) else decode_text(t) for _, t, *_ in doc.doc.get_outlines()]
        assert toc[0] == "Кратко" and "1. Паспорт прогона" in toc and any(t.startswith("REQ-01") for t in toc)
        assert "Кратко" in (doc.pages[0].extract_text() or "")
        pages = {p.page_obj.pageid for p in doc.pages}
        internal = [a["data"]["Dest"] for a in doc.pages[0].annots if a["data"].get("Dest")]
        assert len(internal) >= 5                                   # содержание и блокеры — ссылки внутрь отчёта
        assert all(d[0].objid in pages for d in internal)
        assert len({d[0].objid for d in internal}) > 1              # ведут на разные страницы


def test_docx_internal_links_point_to_bookmarks(report, tmp_path):
    import docx
    from docx.oxml.ns import qn
    d = docx.Document(str(write_report(report, tmp_path / "r.docx")[0]))
    body = d.element.body
    marks = {b.get(qn("w:name")) for b in body.iter(qn("w:bookmarkStart"))}
    links = [h.get(qn("w:anchor")) for h in body.iter(qn("w:hyperlink"))]
    assert links and marks and set(links) <= marks
