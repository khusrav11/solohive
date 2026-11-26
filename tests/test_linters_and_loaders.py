"""Правила и загрузчики: находят дефекты в любых форматах и не шумят на качественном тексте."""

from pathlib import Path

import pytest
from conftest import ROOT, SAMPLE

from solohive.document import SourceDocument, normalize
from solohive.linters import run_linters
from solohive.loaders import LoadError, load_document

EXAMPLES = ROOT / "examples"


def rules_fired(doc) -> set[str]:
    return {f.sources[0].split(":", 1)[1] for f in run_linters(doc) if f.status == "active"}


@pytest.mark.parametrize("name", ["requirements.md", "requirements.pdf", "requirements.docx"])
def test_all_formats_produce_same_structure(name):
    doc = load_document(EXAMPLES / name)
    titles = [s.title for s in doc.sections]
    assert doc.title == "Уведомление о расходах по карте"
    for t in ["Функциональные требования", "Часть B. REST API", "Критерии приемки", "Статусы"]:
        assert t in titles
    m = doc.find("Повторная доставка одного события процессинга сохраняет исходный итог")
    assert m and m.exact


@pytest.mark.parametrize("name", ["requirements.md", "requirements.pdf", "requirements.docx"])
def test_key_rules_fire_on_sample_in_every_format(name):
    fired = rules_fired(load_document(EXAMPLES / name))
    for rule in ["R-HTTP-SAFE-METHOD", "R-HTTP-SUCCESS-ERROR", "R-MONEY-UNITS", "R-TYPE-MISMATCH",
                 "R-TIMEZONE", "R-PLACEHOLDER", "R-NFR-METRIC", "R-NFR-ENDPOINT", "R-VERSION"]:
        assert rule in fired, f"{rule} не сработало на {name}"


def test_rule_evidence_is_always_exact():
    doc = load_document(SAMPLE)
    for f in run_linters(doc):
        for e in f.evidence:
            assert e.verified and e.line, f.title
            assert normalize(e.quote)[:30] in normalize(doc.line(e.line).text) or e.line_end, f.title


CLEAN_SPEC = """# Сброс пароля

## Функциональные требования
* **FR-1.** Пользователь запрашивает сброс пароля по email; система отправляет ссылку, действующую 30 минут.
* **FR-2.** Ссылка одноразовая: повторный переход возвращает ошибку LINK_USED.

## Нефункциональные требования
* **NFR-1.** p95 времени отправки письма ≤ 5 с при нагрузке до 50 запросов в секунду.

## API
| Parameter | Format | Description |
| :--- | :--- | :--- |
| email | string | Адрес пользователя |

REST type: POST /password/reset

## Критерии приёмки
| Требование | Действие | Ожидаемый результат |
| :--- | :--- | :--- |
| FR-1 | Запросить сброс | Письмо со ссылкой |
| FR-2 | Перейти по ссылке повторно | Ошибка LINK_USED |

Версия документа: 1.2, дата утверждения 01.11.2026.
"""


def test_no_false_positives_on_clean_spec(tmp_path: Path):
    p = tmp_path / "clean.md"
    p.write_text(CLEAN_SPEC, encoding="utf-8")
    fired = rules_fired(load_document(p))
    assert fired == set(), fired


def test_nfr_endpoint_only_for_internal_handoff(tmp_path: Path):
    p = tmp_path / "nfr.md"
    p.write_text("# Т\n\n## Нефункциональные требования\n"
                 "* НФТ-1. Сервис передаёт отчёт в очередь не позднее 5 секунд после запроса.\n", encoding="utf-8")
    assert "R-NFR-ENDPOINT" in rules_fired(load_document(p))
    p.write_text("# Т\n\n## Нефункциональные требования\n"
                 "* НФТ-1. Клиент получает уведомление не позднее 60 секунд после покупки для 95% событий.\n",
                 encoding="utf-8")
    assert "R-NFR-ENDPOINT" not in rules_fired(load_document(p))


def test_vague_words_detected(tmp_path: Path):
    p = tmp_path / "vague.md"
    p.write_text("# Т\n\n## Нефункциональные требования\n* Отчёт должен формироваться быстро и удобно.\n",
                 encoding="utf-8")
    fired = rules_fired(load_document(p))
    assert "R-VAGUE" in fired


def test_unsupported_and_missing_files(tmp_path: Path):
    with pytest.raises(LoadError):
        load_document(tmp_path / "nope.md")
    bad = tmp_path / "x.xyz"
    bad.write_text("x")
    with pytest.raises(LoadError):
        load_document(bad)


def test_fuzzy_quote_search_tolerates_formatting():
    doc = SourceDocument.from_markdown(Path("x.md"), "markdown", "0",
                                       "# Д\n\n* **Ф-1.** Порог от 1 000 до 1 000 000 ₸ включительно.\n")
    assert doc.find("Ф-1. Порог от 1000 до 1000000 ₸ включительно").exact
    assert doc.find("порог от 1 000 до 1 000 000 тенге включительно") is not None   # близкая цитата
    assert doc.find("клиенту отправляется SMS и письмо на почту") is None             # выдумка
