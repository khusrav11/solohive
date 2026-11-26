"""Критерии качества требований: ISO/IEC/IEEE 29148:2018 (п. 5.2.5, 5.2.6) и IEEE Std 830-1998 (п. 4.3)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Criterion:
    code: str
    name: str          # полное русское название
    short: str         # короткое название для таблиц
    source: str        # пункт стандарта
    question: str      # контрольный вопрос для проверки


# Свойства отдельного требования (ISO 29148 п. 5.2.5 + IEEE 830 п. 4.3)
INDIVIDUAL: list[Criterion] = [
    Criterion("necessary", "Необходимость", "Необх.", "ISO 29148 5.2.5 a",
              "Несёт ли требование бизнес-ценность; что сломается, если его убрать?"),
    Criterion("appropriate", "Уместность (без лишних проектных решений)", "Без реш.", "ISO 29148 5.2.5 b",
              "Описано ли ЧТО нужно, а не КАК реализовать, без необоснованных деталей реализации?"),
    Criterion("unambiguous", "Однозначность", "Однозн.", "ISO 29148 5.2.5 c; IEEE 830 4.3.2",
              "Есть ли ровно одно толкование для разработчика, тестировщика и бизнеса?"),
    Criterion("complete", "Полнота", "Полнота", "ISO 29148 5.2.5 d; IEEE 830 4.3.3",
              "Описаны ли все условия, границы, ошибки, единицы, исключения?"),
    Criterion("singular", "Атомарность (единичность)", "Атом.", "ISO 29148 5.2.5 e",
              "Описывает ли формулировка ровно одну возможность/ограничение?"),
    Criterion("feasible", "Реализуемость", "Реализ.", "ISO 29148 5.2.5 f",
              "Можно ли реализовать в заданной архитектуре и ограничениях?"),
    Criterion("verifiable", "Проверяемость", "Провер.", "ISO 29148 5.2.5 g; IEEE 830 4.3.6",
              "Можно ли написать детерминированный тест с однозначным ожидаемым результатом?"),
    Criterion("correct", "Корректность", "Коррект.", "ISO 29148 5.2.5 h; IEEE 830 4.3.1",
              "Верно ли отражена потребность и предметная область (банкинг, платежи)?"),
    Criterion("conforming", "Соответствие стандартам", "Станд.", "ISO 29148 5.2.5 i",
              "Соответствует ли шаблону, стандартам протоколов (HTTP/RFC 9110), нормам ИБ?"),
    Criterion("consistent", "Непротиворечивость", "Непрот.", "IEEE 830 4.3.4",
              "Не противоречит ли другим требованиям, разделам, единицам измерения?"),
]

# Свойства набора требований (ISO 29148 п. 5.2.6 + IEEE 830 п. 4.3)
SET: list[Criterion] = [
    Criterion("set_complete", "Полнота набора", "Полнота", "ISO 29148 5.2.6 a; IEEE 830 4.3.3",
              "Покрыт ли процесс от триггера до финала, позитивные и негативные сценарии, нет ли пустых разделов?"),
    Criterion("set_consistent", "Непротиворечивость набора", "Непротиворечивость", "ISO 29148 5.2.6 b; IEEE 830 4.3.4",
              "Нет ли конфликтов между требованиями, разделами и связанными документами?"),
    Criterion("set_feasible", "Реализуемость набора", "Реализуемость", "ISO 29148 5.2.6 c",
              "Реализуем ли набор целиком: архитектура, интеграции, стандарты протоколов?"),
    Criterion("comprehensible", "Понятность", "Понятность", "ISO 29148 5.2.6 d",
              "Единые ли термины; понятно ли, что ожидается от системы?"),
    Criterion("validatable", "Возможность валидации", "Валидация", "ISO 29148 5.2.6 e",
              "Можно ли доказать, что набор удовлетворяет бизнес-цели (критерии приёмки)?"),
    Criterion("traceable", "Прослеживаемость", "Прослеживаемость", "IEEE 830 4.3.8",
              "Есть ли уникальные ID и связь цель → UI → требование → API/данные → приёмка?"),
    Criterion("ranked", "Ранжированность по важности", "Ранжирование", "IEEE 830 4.3.5",
              "Указаны ли приоритеты и стабильность требований?"),
    Criterion("modifiable", "Модифицируемость и версионность", "Версионность", "IEEE 830 4.3.7",
              "Есть ли версия, дата, история изменений, нет ли дублирования?"),
]

BY_CODE: dict[str, Criterion] = {c.code: c for c in INDIVIDUAL + SET}

# Синонимы, которые может вернуть модель, → канонический код
ALIASES: dict[str, str] = {
    "necessity": "necessary", "необходимость": "necessary",
    "appropriateness": "appropriate", "implementation_free": "appropriate", "design_free": "appropriate",
    "уместность": "appropriate",
    "unambiguity": "unambiguous", "однозначность": "unambiguous", "ambiguity": "unambiguous", "clarity": "unambiguous",
    "completeness": "complete", "полнота": "complete",
    "singularity": "singular", "atomic": "singular", "atomicity": "singular", "атомарность": "singular",
    "feasibility": "feasible", "реализуемость": "feasible",
    "verifiability": "verifiable", "testability": "verifiable", "testable": "verifiable", "проверяемость": "verifiable",
    "correctness": "correct", "корректность": "correct",
    "conformity": "conforming", "conformance": "conforming", "standards": "conforming",
    "consistency": "consistent", "непротиворечивость": "consistent",
    "complete_set": "set_complete", "completeness_set": "set_complete",
    "consistent_set": "set_consistent", "consistency_set": "set_consistent",
    "feasible_set": "set_feasible",
    "comprehensibility": "comprehensible", "understandable": "comprehensible",
    "validation": "validatable", "able_to_be_validated": "validatable",
    "traceability": "traceable", "прослеживаемость": "traceable",
    "ranking": "ranked", "prioritized": "ranked",
    "modifiability": "modifiable", "versioning": "modifiable",
}

SET_FROM_INDIVIDUAL = {
    "complete": "set_complete", "consistent": "set_consistent", "feasible": "set_feasible",
    "unambiguous": "comprehensible", "verifiable": "validatable",
}


def canonical(code: str, scope: str = "requirement") -> str:
    """Приводит код критерия от модели к каноническому виду."""
    c = (code or "").strip().lower().replace(" ", "_").replace("-", "_")
    c = ALIASES.get(c, c)
    if c not in BY_CODE:
        c = "complete" if scope == "requirement" else "set_complete"
    if scope == "set" and c in SET_FROM_INDIVIDUAL:
        c = SET_FROM_INDIVIDUAL[c]
    return c


def name(code: str) -> str:
    crit = BY_CODE.get(code)
    return crit.name if crit else code


SEVERITY_RUBRIC = """\
- CRITICAL (блокер) — только если реализация «как написано» заведомо ошибочна или невозможна:
  прямое логическое противоречие (два утверждения нельзя выполнить одновременно);
  грубое нарушение стандарта протокола, меняющее семантику (например, изменение состояния через HTTP GET);
  документ обещает возможность, которой нет в спецификации (UI или интеграция «читает», а метода чтения нет);
  реализация по тексту приведёт к неверным деньгам или к ложному/пропущенному уведомлению о деньгах.
  Разработку начинать нельзя. Обычно таких дефектов в документе единицы.
- MAJOR (существенный): неполнота и двусмысленность, которые нужно доуточнить до спринта — не описаны
  граничные и негативные сценарии, отказы смежных систем, идемпотентность, транспорт интеграции, текст или
  параметры уведомления, правила валидации; расхождения форматов/типов/единиц; успешный HTTP-код с ошибкой
  в теле; пустой содержательный раздел; неизмеримое НФТ (нет перцентиля, нагрузки, точек измерения) — MAJOR,
  даже если в текущем виде его нельзя проверить. Отсутствующая деталь, которую аналитик может доопределить,
  не пересматривая остальные требования, — это MAJOR, а не CRITICAL.
- MINOR (замечание): стиль, терминологические шероховатости, отсутствие версии документа, незаполненный блок
  согласующих, неатомарность без риска неверного толкования, описательные неточности API (подпись кода ответа,
  Content-Type у запроса без тела, нет Prod URL/Swagger). Не блокирует разработку."""
