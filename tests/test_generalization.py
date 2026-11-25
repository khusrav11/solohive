"""Обобщение на незнакомые ТЗ: другой стиль разделов, нумерации и форматов (MD, PDF из Word, TXT без разметки)."""

import pytest
from conftest import ROOT

from solohive.agents.segmenter import fallback_segment
from solohive.linters import run_linters
from solohive.loaders import load_document

FIX = ROOT / "tests" / "fixtures"


def fired(path):
    return {f.sources[0][5:] for f in run_linters(load_document(path)) if f.status == "active"}


@pytest.mark.parametrize("name", ["p2p_transfer.md", "p2p_transfer_word.pdf"])
def test_planted_defects_found_in_unseen_spec(name):
    got = fired(FIX / name)
    for rule in ["R-HTTP-SAFE-METHOD", "R-HTTP-SUCCESS-ERROR", "R-MONEY-UNITS", "R-TIMEZONE",
                 "R-NFR-METRIC", "R-VAGUE", "R-PLACEHOLDER"]:
        assert rule in got, f"{rule} не найдено в {name}"
    assert "R-VERSION" not in got          # версия в документе есть — ложного срабатывания нет


def test_plain_text_headings_and_defects():
    doc = load_document(FIX / "loan_plain.txt")
    titles = [s.title for s in doc.sections]
    assert "2. ФУНКЦИОНАЛЬНЫЕ ТРЕБОВАНИЯ" in titles and "4. ТЕСТИРОВАНИЕ" in titles
    got = fired(FIX / "loan_plain.txt")
    assert {"R-PLACEHOLDER", "R-VERSION", "R-VAGUE"} <= got
    # «2.1 Клиент заполняет анкету…» — требование, а не заголовок
    assert not any(t.startswith("2.1") for t in titles)


def test_fallback_segmentation_works_without_llm_on_any_format():
    for name in ["p2p_transfer.md", "p2p_transfer_word.pdf", "loan_plain.txt"]:
        reqs = fallback_segment(load_document(FIX / name))
        assert len(reqs) >= 5, name
        assert all(r.line_start and r.quote for r in reqs)
