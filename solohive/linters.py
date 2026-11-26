"""🐝 Пчела-инспектор: детерминированные проверки без LLM.

Правила общие для любых ТЗ (не подогнаны под конкретный документ). Они дают находки
со 100% подтверждённой цитатой и страхуют LLM от пропусков: заглушки, пустые разделы,
семантика HTTP, коды ответов, единицы измерения, типы данных, часовой пояс, НФТ без метрик,
прослеживаемость, терминология, размытые формулировки.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Callable, Iterable

from .document import Line, SourceDocument, clean_md
from .models import Evidence, Finding, Severity

RuleFn = Callable[[SourceDocument], list[Finding]]
RULES: list[tuple[str, RuleFn]] = []


def rule(rule_id: str):
    def deco(fn: RuleFn) -> RuleFn:
        RULES.append((rule_id, fn))
        fn.rule_id = rule_id  # type: ignore[attr-defined]
        return fn
    return deco


def run_linters(doc: SourceDocument) -> list[Finding]:
    findings: list[Finding] = []
    for rule_id, fn in RULES:
        try:
            for f in fn(doc):
                f.sources = [f"rule:{rule_id}"]
                findings.append(f)
        except Exception as exc:  # правило не должно ронять весь прогон
            findings.append(Finding(scope="set", criterion="set_complete", severity=Severity.MINOR,
                                    title=f"Правило {rule_id} не выполнено: {exc}", sources=[f"rule:{rule_id}"],
                                    status="dropped", note="внутренняя ошибка правила"))
    return findings


# --- помощники ----------------------------------------------------------------------------------

_HEADING = re.compile(r"^\s*#{1,6}\s")
_ID = re.compile(r"(?<![\w-])[A-ZА-ЯЁ]{1,6}[-_.]?\d{1,3}(?:\.\d+)*(?![\w-])")


def ev(doc: SourceDocument, line_no: int, quote: str | None = None) -> Evidence:
    q = quote or doc.excerpt(line_no)
    return Evidence(quote=q, line=line_no, location=doc.location(line_no), verified=True, match_score=100.0)


def content_lines(doc: SourceDocument, skip_code: bool = True) -> Iterable[tuple[Line, bool]]:
    """Строки документа с признаком «внутри блока кода»."""
    in_code = False
    for line in doc.lines:
        if line.text.strip().startswith("```"):
            in_code = not in_code
            continue
        if skip_code and in_code:
            continue
        yield line, in_code


def section_has(line: Line, pattern: str) -> bool:
    return any(re.search(pattern, s, re.I) for s in line.section)


def top_part(line: Line) -> tuple[str, ...]:
    """Часть документа (первые два уровня заголовков) — граница поиска связанных фактов."""
    return line.section[:2]


# --- 1. Заглушки и пустые разделы ---------------------------------------------------------------

PLACEHOLDER = re.compile(
    r"\b(TBD|TODO|FIXME|TBA)\b|\?\?\?|<\s*заполн\w*\s*>|\[\s*заполн\w*\s*\]|"
    r"заполня\w*\s+(позже|потом|тестировщик\w*|аналитик\w*|разработчик\w*|командой)|"
    r"будет\s+(описан\w*|заполнен\w*|уточнен\w*|добавлен\w*)(\s+позже)?|lorem ipsum",
    re.I,
)


@rule("R-PLACEHOLDER")
def placeholders(doc: SourceDocument) -> list[Finding]:
    out = []
    for line, _ in content_lines(doc):
        if _HEADING.match(line.text) or "верси" in line.text.lower():
            continue
        m = PLACEHOLDER.search(line.text)
        if m:
            sec = doc.display_section(line.section)
            out.append(Finding(
                scope="set", criterion="set_complete", severity=Severity.MAJOR,
                title=f"Раздел «{line.section[-1] if line.section else sec}» содержит заглушку вместо содержания",
                explanation=f"Вместо требований в разделе стоит текст-заглушка «{m.group(0)}». "
                            "Раздел формально есть, но проверять/реализовывать по нему нечего.",
                recommendation="Заполнить раздел до передачи в разработку: тест-стратегия, тестовые данные, "
                               "окружения, критерии входа/выхода, ответственные.",
                evidence=[ev(doc, line.no)],
            ))
    return out


@rule("R-EMPTY-SECTION")
def empty_sections(doc: SourceDocument) -> list[Finding]:
    out = []
    for i, sec in enumerate(doc.sections):
        if sec.level == 1:
            continue
        has_child = i + 1 < len(doc.sections) and doc.sections[i + 1].level > sec.level \
            and doc.sections[i + 1].line_start <= sec.line_end
        own = [ln for ln in doc.section_text(sec, own_only=True) if ln.text.strip() and ln.text.strip() != "---"]
        if not own and not has_child:
            out.append(Finding(
                scope="set", criterion="set_complete", severity=Severity.MAJOR,
                title=f"Пустой раздел «{sec.title}»",
                explanation="Заголовок раздела есть, содержания нет.",
                recommendation="Заполнить раздел или явно указать «Не применимо» с обоснованием.",
                evidence=[ev(doc, sec.line_start)],
            ))
    return out


_EMPTY_CELL = re.compile(r"^\s*(|-|—|–|n/a|нет данных)\s*$", re.I)
_OPTIONS = re.compile(r"\bДа\s*/\s*Нет\b", re.I)


@rule("R-EMPTY-TABLE")
def empty_tables(doc: SourceDocument) -> list[Finding]:
    out = []
    for t in doc.tables:
        if t.rows and all(all(_EMPTY_CELL.match(c) for c in row) for row in t.rows):
            head = " / ".join(h for h in t.header if h)
            is_approval = re.search(r"подпис|фио|согласу|утвержд|approv", head, re.I)
            out.append(Finding(
                scope="set", criterion="modifiable" if is_approval else "set_complete", severity=Severity.MINOR,
                title="Блок согласования не заполнен (нет подразделений, ФИО, подписей)" if is_approval
                else f"Таблица «{head}» не заполнена",
                explanation="Таблица содержит только заголовок и пустые строки.",
                recommendation="Указать согласующие подразделения и ответственных; без согласования требования "
                               "нельзя считать утверждёнными." if is_approval else "Заполнить таблицу.",
                evidence=[ev(doc, t.header_line, " | ".join(t.header))],
            ))
    return out


@rule("R-UNCHECKED-CHECKLIST")
def unchecked_checklist(doc: SourceDocument) -> list[Finding]:
    out = []
    for t in doc.tables:
        unchecked = [(ln, row) for ln, row in zip(t.row_lines, t.rows) if any(_OPTIONS.search(c) for c in row)]
        if len(unchecked) >= 2:
            out.append(Finding(
                scope="set", criterion="validatable", severity=Severity.MINOR,
                title=f"Чек-лист ревью не заполнен: в {len(unchecked)} пунктах оставлены шаблонные «Да / Нет»",
                explanation="Ни один пункт чек-листа не отмечен — нет подтверждения, что ревью требований "
                            "(тестируемость, откат, ИБ, архитектура) проводилось.",
                recommendation="Пройти чек-лист, выбрать значения и заполнить примечания; для «Нет» — план устранения.",
                evidence=[ev(doc, unchecked[0][0]), ev(doc, unchecked[-1][0])],
            ))
    return out


_KV_FIELD = re.compile(r"^(поле|параметр|field|property|атрибут)$", re.I)
_MISSING_VALUE = re.compile(r"^\s*(—|–|-|tbd|n/a|нет|не указан\w*)?\s*$|\b(prod|production|swagger|url)\s*:\s*(—|–|-)\s*$", re.I)


@rule("R-EMPTY-FIELD")
def empty_fields(doc: SourceDocument) -> list[Finding]:
    """Пустые значения в таблицах-паспортах «Поле | Значение» (например, Prod URL: —, Swagger: —)."""
    out = []
    for t in doc.tables:
        if len(t.header) != 2 or not _KV_FIELD.match(t.header[0].strip()):
            continue
        empty = [(ln, row) for ln, row in zip(t.row_lines, t.rows) if len(row) == 2 and _MISSING_VALUE.search(row[1])]
        if empty:
            names = ", ".join(f"«{row[0]}»" for _, row in empty)
            out.append(Finding(
                scope="set", criterion="set_complete", severity=Severity.MINOR,
                title=f"Не заполнены поля паспорта: {names}",
                explanation="Значение поля отсутствует или стоит прочерк: без адреса продуктивного контура и "
                            "спецификации (Swagger/OpenAPI) интеграцию нельзя реализовать и проверить на стенде.",
                recommendation="Заполнить поля или явно указать срок и ответственного за их появление.",
                evidence=[ev(doc, ln) for ln, _ in empty[:3]],
            ))
    return out


_VERSION_OK = re.compile(r"(верси[яи]|version|ревизи[яи])\W{0,12}(документа\W{0,3})?(:|№)?\s*v?\d+(\.\d+)*|"
                         r"\bv\d+\.\d+\b|дата\s+(создания|изменения|утверждения|документа)\W{0,3}\d", re.I)


@rule("R-VERSION")
def version(doc: SourceDocument) -> list[Finding]:
    if _VERSION_OK.search(doc.text):
        return []
    mention = next((ln for ln, _ in content_lines(doc) if re.search(r"верси|version", ln.text, re.I)), None)
    line_no = mention.no if mention else 1
    return [Finding(
        scope="set", criterion="modifiable", severity=Severity.MINOR,
        title="Не указаны версия и дата документа",
        explanation="Без версии невозможно понять, по какой редакции требований велась разработка и тестирование.",
        recommendation="Добавить номер версии, дату, автора и историю изменений.",
        evidence=[ev(doc, line_no)],
    )]


# --- 2. HTTP / REST ------------------------------------------------------------------------------

_GET_DECL = re.compile(r"(\b(REST\s*type|HTTP[- ]?(method|метод)|Method|Метод|Тип запроса)\b[^\n]*\bGET\b)|"
                       r"(\bGET\s+/[\w/{}.$-]+)|(curl\s+(-G|--get)\b)", re.I)
_MUTATION = re.compile(r"(save|update|create|delete|remove|set|add|edit|change|modify|upsert)[A-Z_]?\w*|"
                       r"сохран\w*|изменени\w*|измен[яи]\w*|созда\w*|удал\w*|замен\w*|запис\w*|обнов\w*", re.I)


@rule("R-HTTP-SAFE-METHOD")
def get_mutation(doc: SourceDocument) -> list[Finding]:
    out = []
    seen_parts = set()
    lines = [ln for ln, _ in content_lines(doc, skip_code=False)]
    for line in lines:
        if not _GET_DECL.search(line.text):
            continue
        part = top_part(line)
        if part in seen_parts:
            continue
        same = [ln for ln in lines if top_part(ln) == part and ln.no != line.no]
        # сильнее всего — глагол изменения в URL, затем в описании, затем в логике метода
        url = next((ln for ln in same if re.search(r"/[\w/]*(save|update|create|delete|set|add|edit|change)", ln.text, re.I)), None)
        desc = next((ln for ln in same if re.search(r"description|описание", ln.text, re.I) and _MUTATION.search(ln.text)), None)
        logic = next((ln for ln in same if _MUTATION.search(ln.text) and not _HEADING.match(ln.text)), None)
        proof = [x for x in (url, desc, logic) if x]
        if not proof:
            continue
        seen_parts.add(part)
        out.append(Finding(
            scope="requirement", criterion="conforming", severity=Severity.CRITICAL,
            title="Операция изменения данных объявлена через HTTP GET (нарушение RFC 9110)",
            explanation="GET по стандарту — безопасный метод без побочных эффектов. Здесь он сохраняет/заменяет "
                        "настройку: запрос могут повторить браузер, прокси, CDN или prefetch, ответ может "
                        "кэшироваться, параметры попадают в логи и историю.",
            recommendation="Использовать POST (создание) или PUT/PATCH (замена/изменение) с телом JSON; "
                           "GET оставить только для чтения настроек.",
            evidence=[ev(doc, line.no)] + [ev(doc, x.no) for x in proof[:2]],
        ))
    return out


@rule("R-GET-BODY")
def get_with_body(doc: SourceDocument) -> list[Finding]:
    out = []
    lines = [ln for ln, _ in content_lines(doc)]
    for line in lines:
        if re.search(r"Content-Type\W+application/json", line.text, re.I):
            part = top_part(line)
            get = next((ln for ln in lines if top_part(ln) == part and _GET_DECL.search(ln.text)), None)
            if get:
                out.append(Finding(
                    scope="requirement", criterion="consistent", severity=Severity.MINOR,
                    title="Для GET-запроса объявлен Content-Type тела application/json",
                    explanation="У GET-запроса тело не используется, поэтому Content-Type запроса не имеет смысла "
                                "и вводит разработчика в заблуждение о способе передачи параметров.",
                    recommendation="Привести в соответствие: либо POST/PUT с JSON-телом, либо убрать Content-Type.",
                    evidence=[ev(doc, line.no), ev(doc, get.no)],
                ))
    return out


_SENSITIVE_PARAM = re.compile(r"(card|pan|account|iban|iin|phone|token|password|passw|cvv|cvc)", re.I)


@rule("R-SENSITIVE-QUERY")
def sensitive_in_query(doc: SourceDocument) -> list[Finding]:
    out = []
    for t in doc.tables:
        if not any(re.search(r"query", s, re.I) for s in t.section):
            continue
        for ln, row in zip(t.row_lines, t.rows):
            name = row[0] if row else ""
            if _SENSITIVE_PARAM.search(name):
                out.append(Finding(
                    scope="requirement", criterion="conforming", severity=Severity.MAJOR,
                    title=f"Идентификатор «{name}» передаётся в строке запроса (query)",
                    explanation="Параметры URL сохраняются в логах веб-серверов, прокси, балансировщиков и истории; "
                                "для платёжных данных это риск ИБ (PCI DSS требует не раскрывать данные карты).",
                    recommendation="Передавать идентификатор в теле запроса или в path как непрозрачный токен карты; "
                                   "явно указать, что cardId — не PAN.",
                    evidence=[ev(doc, ln)],
                ))
                break
    return out


_STATUS_START = re.compile(r"^\s*(?:#{1,6}\s*|\*\*)\s*(\d{3})\b")
_STATUS_LOOSE = re.compile(r"^\s*(?:HTTP\s*|код\s*|статус\s*)?(\d{3})(?:\s*[.:—–-]|\s+[A-ZА-ЯЁ])", re.I)


def _status_code_at(line: Line) -> int | None:
    """Код HTTP в начале строки-заголовка статуса (markdown-заголовок, жирный текст или строка в разделе статусов)."""
    m = _STATUS_START.match(line.text)
    if not m and len(line.text) < 120 and section_has(line, r"статус|код\w* ответ|status|response|ответ"):
        m = _STATUS_LOOSE.match(clean_md(line.text))
    if m and 100 <= int(m.group(1)) <= 599:
        return int(m.group(1))
    return None
_REASONS = {
    "ok": 200, "created": 201, "accepted": 202, "no content": 204, "bad request": 400, "unauthorized": 401,
    "forbidden": 403, "not found": 404, "conflict": 409, "unprocessable entity": 422,
    "too many requests": 429, "internal server error": 500, "service unavailable": 503, "gateway timeout": 504,
}


def _status_blocks(doc: SourceDocument) -> list[tuple[int, Line, list[Line]]]:
    """Блоки описания HTTP-статусов: (код, строка-заголовок, строки блока)."""
    lines = list(doc.lines)
    starts = [(i, code) for i, ln in enumerate(lines) if (code := _status_code_at(ln)) is not None]
    blocks = []
    for k, (i, code) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
        body = []
        for ln in lines[i + 1: end]:
            if _HEADING.match(ln.text) and _status_code_at(ln) is None:
                break
            body.append(ln)
        blocks.append((code, lines[i], body))
    return blocks


@rule("R-HTTP-SUCCESS-ERROR")
def success_with_error(doc: SourceDocument) -> list[Finding]:
    out = []
    for code, head, body in _status_blocks(doc):
        if 200 <= code < 300:
            bad = next((ln for ln in body if re.search(r'"(result|status)"\s*:\s*"(error|fail\w*|ошибка)"', ln.text, re.I)), None)
            if bad:
                out.append(Finding(
                    scope="requirement", criterion="consistent", severity=Severity.MAJOR,
                    title=f"Успешный статус HTTP {code} возвращает тело с ошибкой",
                    explanation="Клиент и мониторинг по коду 2xx считают запрос успешным, а в теле — ошибка. "
                                "Ошибка валидации теряется в метриках и обрабатывается по-разному в клиентах.",
                    recommendation="Для отклонённой суммы возвращать 400 (или 422) с кодом ошибки; 200 — только при успехе.",
                    evidence=[ev(doc, head.no), ev(doc, bad.no)],
                ))
    return out


@rule("R-HTTP-REASON")
def status_reason(doc: SourceDocument) -> list[Finding]:
    out = []
    for code, head, _ in _status_blocks(doc):
        text = clean_md(head.text).lower()
        for phrase, std in _REASONS.items():
            if re.search(rf"\b{phrase}\b", text) and std != code and not re.search(rf"\b{std}\b", text):
                out.append(Finding(
                    scope="requirement", criterion="consistent", severity=Severity.MINOR,
                    title=f"Код {code} подписан как «{phrase.title()}» (это фраза кода {std})",
                    explanation=f"По RFC 9110 «{phrase.title()}» соответствует коду {std}, а не {code}.",
                    recommendation=f"Исправить подпись или код ответа (если ресурс создаётся — {std}).",
                    evidence=[ev(doc, head.no)],
                ))
                break
    return out


_CODE_REF = re.compile(r"(?:возвраща\w*|return\w*|результат\w*|код\w*|статус\w*|ответ\w*|HTTP)\D{0,25}?"
                       r"\b([1-5]\d\d)\b(?:\s*(?:или|,|и|or)\s*\b([1-5]\d\d)\b)*", re.I)


@rule("R-HTTP-STATUS-UNDECLARED")
def undeclared_status(doc: SourceDocument) -> list[Finding]:
    blocks = _status_blocks(doc)
    if not blocks:
        return []
    declared = {code for code, _, _ in blocks}
    out = []
    reported = set()
    for line, _ in content_lines(doc):
        if _status_code_at(line) is not None:
            continue
        for m in re.finditer(r"\b([1-5]\d\d)\b", line.text):
            code = int(m.group(1))
            ctx = line.text[max(0, m.start() - 40): m.end() + 5]
            if code in declared or code in reported or not re.search(r"возвраща|return|результат|код|статус|ответ|HTTP", ctx, re.I):
                continue
            reported.add(code)
            out.append(Finding(
                scope="requirement", criterion="complete", severity=Severity.MAJOR,
                title=f"Код ответа {code} упомянут, но не описан среди статусов",
                explanation=f"В тексте есть ссылка на {code}, но нет описания, когда он возвращается и какое у него тело.",
                recommendation=f"Описать {code} (условие, тело ответа) или убрать упоминание.",
                evidence=[ev(doc, line.no)],
            ))
    # описание статуса X утверждает, что возвращается другой код Y
    for code, head, body in blocks:
        for ln in body:
            m = re.search(r"возвраща\w*\s+(\d{3})|returns?\s+(\d{3})", ln.text, re.I)
            if m:
                other = int(m.group(1) or m.group(2))
                if other != code:
                    out.append(Finding(
                        scope="requirement", criterion="consistent", severity=Severity.MAJOR,
                        title=f"В описании статуса {code} сказано, что возвращается {other}",
                        explanation=f"Статус {code} заявлен в списке ответов, но его описание утверждает, что вместо него "
                                    f"возвращается {other}. Непонятно, возможен ли {code} вообще и какой код проверять в тестах.",
                        recommendation=f"Оставить один код для сценария и синхронизировать список статусов.",
                        evidence=[ev(doc, head.no), ev(doc, ln.no)],
                    ))
    return out


@rule("R-ERROR-FORMAT")
def error_format(doc: SourceDocument) -> list[Finding]:
    only_message = []
    for code, head, body in _status_blocks(doc):
        if code < 400:
            continue
        for ln in body:
            t = ln.text.strip()
            if t.startswith("{") and t.endswith("}"):
                try:
                    keys = set(json.loads(t).keys())
                except Exception:  # noqa: BLE001
                    continue
                if keys and keys <= {"message", "error", "description", "msg"}:
                    only_message.append(ln)
    if len(only_message) < 2:
        return []
    return [Finding(
        scope="set", criterion="conforming", severity=Severity.MINOR,
        title="Ответы об ошибках содержат только текст message без машинно-читаемого кода",
        explanation="Клиент вынужден разбирать человекочитаемый текст, чтобы отличить ошибки; локализация текста "
                    "сломает обработку.",
        recommendation="Ввести поле code (например, LIMIT_OUT_OF_RANGE, CARD_NOT_FOUND) и traceId.",
        evidence=[ev(doc, only_message[0].no), ev(doc, only_message[-1].no)],
    )]


# --- 3. Данные, единицы, типы ---------------------------------------------------------------------

_MINOR_UNIT = re.compile(r"тиын\w*|копе[йе]\w*|цент\w*|tiyn", re.I)
_MAJOR_UNIT = re.compile(r"тенге|₸|\bKZT\b|kzt|рубл\w*|₽|доллар\w*|\bUSD\b", re.I)
_CONVERSION = re.compile(r"1\s*(₸|тенге|рубл\w*|доллар\w*)\s*=\s*100|100\s*(тиын|копе\w+|цент\w*)|[×x*]\s*100\b|"
                         r"/\s*100\b|(делени|умножени)\w*\s+на\s+100|пересч[её]т\w*\s+(из|в)\s+(тиын|копе)", re.I)


@rule("R-MONEY-UNITS")
def money_units(doc: SourceDocument) -> list[Finding]:
    lines = [ln for ln, _ in content_lines(doc)]
    minor = [ln for ln in lines if _MINOR_UNIT.search(ln.text)]
    if not minor or _CONVERSION.search(doc.text):
        return []
    def weight(ln: Line) -> int:
        t = ln.text
        return (3 if re.search(r"integer|\bint\b|целое|\bdb\b|_kzt|limit|amount", t, re.I) else 0) \
            + (2 if t.lstrip().startswith("|") else 0) + (1 if re.search(r"храни|field|поле", t, re.I) else 0)
    major_defs = [ln for ln in lines if _MAJOR_UNIT.search(ln.text) and ln.no != minor[0].no and weight(ln) >= 2]
    major_defs.sort(key=lambda ln: (-weight(ln), ln.no))
    if not major_defs and not _MAJOR_UNIT.search(minor[0].text):
        return []
    evidence = [ev(doc, minor[0].no)] + [ev(doc, ln.no) for ln in major_defs[:2]]
    return [Finding(
        scope="set", criterion="set_consistent", severity=Severity.MAJOR,
        title="Денежные суммы в разных единицах (минорные и основные) без правила пересчёта",
        explanation="Часть сумм определена в минорных единицах (тиын), часть — в основных (тенге). Правила "
                    "пересчёта и округления нет: при сравнении расходов с порогом легко ошибиться в 100 раз.",
        recommendation="Хранить и передавать все суммы в одной единице (рекомендуется тиын, integer) либо явно "
                       "зафиксировать: 1 ₸ = 100 тиын, где и как выполняется пересчёт и округление.",
        evidence=evidence,
    )]


_PARAM_COL = re.compile(r"^(parameter|параметр|поле|field|name|имя|атрибут)$", re.I)
_TYPE_COL = re.compile(r"^(format|type|тип|формат|тип данных)$", re.I)


def _norm_type(t: str) -> str:
    t = t.lower()
    if re.search(r"int|long|number|decimal|числ|float|double", t):
        return "number"
    if re.search(r"str|text|varchar|строк|char", t):
        return "string"
    if re.search(r"bool", t):
        return "boolean"
    return t.strip()


@rule("R-TYPE-MISMATCH")
def type_mismatch(doc: SourceDocument) -> list[Finding]:
    seen: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    for t in doc.tables:
        pi = next((i for i, h in enumerate(t.header) if _PARAM_COL.match(h.strip())), None)
        ti = next((i for i, h in enumerate(t.header) if _TYPE_COL.match(h.strip())), None)
        if pi is None or ti is None:
            continue
        for ln, row in zip(t.row_lines, t.rows):
            if len(row) > max(pi, ti) and row[pi].strip():
                seen[row[pi].strip().lower()].append((row[ti].strip(), _norm_type(row[ti]), ln))
    out = []
    for name, uses in seen.items():
        kinds = {k for _, k, _ in uses if k}
        if len(kinds) > 1:
            desc = ", ".join(f"{raw} (строка {ln})" for raw, _, ln in uses)
            out.append(Finding(
                scope="set", criterion="set_consistent", severity=Severity.MAJOR,
                title=f"Параметр «{name}» описан с разными типами: {desc}",
                explanation="Один и тот же параметр в запросе и ответе имеет разные типы данных — клиенту "
                            "придётся конвертировать, растёт риск ошибок сериализации и сравнения.",
                recommendation="Унифицировать тип (например, integer для сумм и 0/1 или boolean для признаков).",
                evidence=[ev(doc, ln) for _, _, ln in uses[:3]],
            ))
    return out


# --- 4. Время, НФТ ------------------------------------------------------------------------------------

_TIME_LOGIC = re.compile(r"календарн\w*\s+(месяц|день|сутк)|месяц\w*\s+определя|по\s+дате|дат[аеуы]\s+"
                         r"(совершения|операции|проведения|покупки)|полноч|00:0\d|23:5\d|конец\s+(дня|месяца)|"
                         r"начал\w*\s+(дня|месяца)", re.I)
_TZ = re.compile(r"\bUTC\b|\bGMT\b|часов\w*\s+пояс\w*|time\s?zone|Asia/\w+|UTC?\s*[+−-]\s*\d", re.I)
_CITY_TIME = re.compile(r"\b(в|по)\s+([А-ЯЁ][а-яё]+)\b")


@rule("R-TIMEZONE")
def timezone(doc: SourceDocument) -> list[Finding]:
    if _TZ.search(doc.text):
        return []
    hits = [ln for ln, _ in content_lines(doc) if _TIME_LOGIC.search(ln.text)]
    if not hits:
        return []
    city_lines = [ln for ln in hits if _CITY_TIME.search(clean_md(ln.text)[1:])] or \
                 [ln for ln, _ in content_lines(doc) if re.search(r"по\s+[А-ЯЁ][а-яё]+\s*$|\d{2}:\d{2}\s+по\s+[А-ЯЁ]", ln.text)]
    evidence = [ev(doc, ln.no) for ln in (city_lines + [h for h in hits if h not in city_lines])[:3]]
    city = ""
    for ln in city_lines:
        m = _CITY_TIME.search(clean_md(ln.text)[1:])
        if m:
            city = f" через город («{m.group(0)}»)"
            break
    return [Finding(
        scope="requirement", criterion="unambiguous", severity=Severity.MAJOR,
        title=f"Границы периодов привязаны ко времени{city}, но часовой пояс явно не задан",
        explanation="Не указано, в каком часовом поясе считается дата/месяц (UTC, Asia/Almaty UTC+5, время "
                    "терминала, время процессинга). Формулировку через город можно прочитать и как место покупки. "
                    "Операции около полуночи последнего дня месяца попадут в разные месяцы.",
        recommendation="Явно указать: «месяц определяется по дате авторизации в часовом поясе Asia/Almaty (UTC+5)», "
                       "и какой именно момент операции (авторизация или проведение) берётся.",
        evidence=evidence,
    )]


_PERF_TIME = re.compile(r"\b(в течение|не более|не позднее|не дольше|за|до)\s+(\d+[\s,.]*)?"
                        r"(мс|миллисекунд\w*|секунд\w*|сек\b|минут\w*|мин\b|час\w*)", re.I)
_PERF_STATS = re.compile(r"перцентил\w*|\bp9\d\b|9\d(?:[.,]\d+)?\s*%|процент\w*\s+(запросов|событий)|при\s+нагрузк\w*|"
                         r"\bTPS\b|\bRPS\b|в\s+секунду|одновременн\w*|пиков\w*", re.I)


@rule("R-NFR-METRIC")
def nfr_metric(doc: SourceDocument) -> list[Finding]:
    out = []
    for line, _ in content_lines(doc):
        if not section_has(line, r"нефункциональн|производительн|performance|non-?functional|\bНФТ\b|SLA"):
            continue
        if _PERF_TIME.search(line.text) and not _PERF_STATS.search(line.text):
            out.append(Finding(
                scope="requirement", criterion="verifiable", severity=Severity.MAJOR,
                title="Временное НФТ без перцентиля, профиля нагрузки и точки измерения",
                explanation="Не указано: для какой доли событий (p95/p99 или 100%), при какой нагрузке (событий/с), "
                            "от какого до какого момента меряется время и что делать при превышении. "
                            "Нагрузочный тест с однозначным pass/fail написать нельзя.",
                recommendation="Например: «p99 ≤ 60 с от получения события процессинга до передачи в push-сервис "
                               "при нагрузке до N событий/с; измеряется метрикой X».",
                evidence=[ev(doc, line.no)],
            ))
    return out


# --- 5. Прослеживаемость, атомарность, терминология, размытость --------------------------------------

@rule("R-MISSING-IDS")
def missing_ids(doc: SourceDocument) -> list[Finding]:
    gaps = []
    for i, sec in enumerate(doc.sections):
        if not re.search(r"требовани", sec.title, re.I) or re.search(r"тестир|ревью|review|согласу", sec.title, re.I):
            continue
        has_child = i + 1 < len(doc.sections) and doc.sections[i + 1].level > sec.level \
            and doc.sections[i + 1].line_start <= sec.line_end
        own = [ln for ln in doc.section_text(sec, own_only=True) if ln.text.strip()]
        if has_child or not own:
            continue
        if not any(_ID.search(ln.text) for ln in own):
            gaps.append((sec, own[0]))
    if not gaps:
        return []
    names = ", ".join(f"«{s.title}»" for s, _ in gaps)
    return [Finding(
        scope="set", criterion="traceable", severity=Severity.MINOR,
        title=f"Требования без уникальных идентификаторов: {names}",
        explanation="На требования без ID нельзя сослаться из тест-кейсов, задач и критериев приёмки; "
                    "прослеживаемость цель → требование → тест разрывается.",
        recommendation="Присвоить ID (например, НФТ-1, ДАН-1, ИНТ-1) и использовать их в критериях приёмки.",
        evidence=[ev(doc, ln.no) for _, ln in gaps[:4]],
    )]


@rule("R-AC-TRACE")
def acceptance_trace(doc: SourceDocument) -> list[Finding]:
    ac = [s for s in doc.sections if re.search(r"критери\w*\s+при[её]мк|acceptance", s.title, re.I)]
    if not ac:
        return []
    sec = ac[0]
    ids = set()
    for ln, _ in content_lines(doc):
        if ln.no < sec.line_start or ln.no > sec.line_end:
            if section_has(ln, r"функциональн|требовани") and ln.text.lstrip().startswith(("*", "-", "+")):
                ids.update(m.group(0) for m in _ID.finditer(clean_md(ln.text)[:20]))
    if not ids:
        return []
    body = "\n".join(ln.text for ln in doc.lines[sec.line_start: sec.line_end])
    if any(i in body for i in ids):
        return []
    return [Finding(
        scope="set", criterion="traceable", severity=Severity.MINOR,
        title="Критерии приёмки не ссылаются на идентификаторы требований",
        explanation=f"В документе есть требования {', '.join(sorted(ids))}, но ни один критерий приёмки не указывает, "
                    "какое требование он проверяет. Покрытие требований тестами не прослеживается.",
        recommendation="Добавить в таблицу приёмки колонку «Требование» и покрыть каждое требование "
                       "позитивным и негативным сценарием.",
        evidence=[ev(doc, sec.line_start)],
    )]


_SENT = re.compile(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z])")


def _id_paragraphs(doc: SourceDocument) -> list[tuple[Line, str]]:
    """Пункты с ID вместе со строками-продолжениями (в PDF один пункт занимает несколько строк)."""
    items: list[tuple[Line, str]] = []
    lines = [ln for ln, _ in content_lines(doc)]
    i = 0
    while i < len(lines):
        text = clean_md(lines[i].text)
        if re.match(r"^[A-ZА-ЯЁ]{1,6}[-_.]?\d{1,3}(?:\.\d+)*\.?\s", text):
            parts, j = [text], i + 1
            while j < len(lines):
                nxt = lines[j].text.strip()
                if (not nxt or nxt.startswith(("#", "|", "*", "-")) or lines[j].no != lines[j - 1].no + 1
                        or re.match(r"^[A-ZА-ЯЁ]{1,6}[-_.]?\d{1,3}(?:\.\d+)*\.?\s", clean_md(nxt))):
                    break
                parts.append(clean_md(nxt))
                j += 1
            items.append((lines[i], " ".join(parts)))
            i = j
        else:
            i += 1
    return items


@rule("R-NON-ATOMIC")
def non_atomic(doc: SourceDocument) -> list[Finding]:
    out = []
    for line, text in _id_paragraphs(doc):
        m = re.match(r"^([A-ZА-ЯЁ]{1,6}[-_.]?\d{1,3}(?:\.\d+)*)\.?\s+(.*)$", text)
        if not m:
            continue
        sentences = [s for s in _SENT.split(m.group(2)) if len(s) > 8]
        if len(sentences) >= 3:
            out.append(Finding(
                scope="requirement", criterion="singular", severity=Severity.MINOR,
                title=f"Требование {m.group(1)} неатомарно: {len(sentences)} самостоятельных правила в одном пункте",
                explanation="Несколько правил под одним ID нельзя независимо реализовать, протестировать и "
                            "отследить: при изменении одного правила меняется весь пункт.",
                recommendation=f"Разбить {m.group(1)} на подпункты ({m.group(1)}.1, {m.group(1)}.2, …) — по одному правилу.",
                evidence=[ev(doc, line.no)],
            ))
    return out


_TERM_GROUPS = [
    ("держатель/владелец карты", r"держател\w*\s+(\w+\s+)?карт", r"владел\w*\s+(\w+\s+)?карт",
     "«Держатель» карты и «владелец» карты в банке — разные роли (держатель дополнительной карты ≠ владелец счёта)."),
    ("порог/лимит", r"порог\w*", r"лимит\w*|limit\w*",
     "В банковской терминологии «лимит» — ограничение, блокирующее операции, а «порог» уведомления ничего не блокирует."),
]


@rule("R-TERMS")
def terminology(doc: SourceDocument) -> list[Finding]:
    out = []
    lines = [ln for ln, _ in content_lines(doc, skip_code=False)]
    # сначала содержательные разделы, метаданные документа — в последнюю очередь
    lines.sort(key=lambda ln: (section_has(ln, r"общие сведения|general|паспорт"), ln.no))
    for name, a, b, why in _TERM_GROUPS:
        la = next((ln for ln in lines if re.search(a, ln.text, re.I)), None)
        lb = next((ln for ln in lines if re.search(b, ln.text, re.I)), None)
        if la and lb:
            out.append(Finding(
                scope="set", criterion="comprehensible", severity=Severity.MINOR,
                title=f"Непоследовательная терминология: {name}",
                explanation=f"Одно понятие названо по-разному в разных местах. {why}",
                recommendation="Ввести глоссарий и использовать один термин во всех частях документа и в API.",
                evidence=[ev(doc, la.no), ev(doc, lb.no)],
            ))
    return out


_VAGUE = re.compile(
    r"\b(быстр\w*|медленн\w*|удобн\w*|интуитивн\w*|достаточн\w*|оптимальн\w*|эффективн\w*|над[её]жн\w*|"
    r"своевременн\w*|приемлем\w*|разумн\w*|современн\w*|гибк\w*|масштабируем\w*|мгновенн\w*|немедленн\w*|"
    r"по\s+возможности|при\s+необходимости|если\s+возможно|как\s+правило|и\s+т\.\s?[пд]\.|и\s+др\.|"
    r"в\s+реальном\s+времени|user[- ]friendly|в\s+момент\s+(покупки|операции|события|платежа))",
    re.I,
)


@rule("R-VAGUE")
def vague(doc: SourceDocument) -> list[Finding]:
    out = []
    for line, _ in content_lines(doc):
        if _HEADING.match(line.text):
            continue
        words = sorted({m.group(0).lower() for m in _VAGUE.finditer(line.text)})
        if words:
            out.append(Finding(
                scope="requirement", criterion="unambiguous", severity=Severity.MAJOR,
                title=f"Размытая формулировка без измеримого критерия: {', '.join(repr(w) for w in words)}",
                explanation="Субъективное или неизмеримое выражение: разработчик, тестировщик и бизнес поймут его "
                            "по-разному, объективно проверить выполнение нельзя.",
                recommendation="Заменить измеримым критерием (время, доля, порог) с указанием способа проверки.",
                evidence=[ev(doc, line.no)],
            ))
    return out


# --- 6. Полнота API и приёмки --------------------------------------------------------------------------

_READ_PROMISE = re.compile(r"\b(чита\w*|получа\w*\s+(настро|порог|данн|сумм)\w*|просматрива\w*|отобража\w*|"
                           r"видн[ыо]\b|показыва\w*)", re.I)
_URL = re.compile(r"(/[A-Za-z][\w/{}.-]*)")
_READ_URL = re.compile(r"(get|read|list|fetch|show|view|info|status|settings?|limits?)(?![a-z])", re.I)
_MUTATING_URL = re.compile(r"(save|update|create|delete|remove|set|add|edit|change|modify)", re.I)


@rule("R-API-READ-GAP")
def api_read_gap(doc: SourceDocument) -> list[Finding]:
    """Текст обещает чтение данных (UI показывает, интеграция «читает»), а в API описано только изменение."""
    lines = [ln for ln, _ in content_lines(doc, skip_code=False)]
    url_lines = [ln for ln in lines if re.search(r"request url|url|curl|endpoint|\b(GET|POST|PUT|PATCH|DELETE)\s+/", ln.text, re.I)
                 and _URL.search(ln.text)]
    urls = {m.group(1) for ln in url_lines for m in _URL.finditer(ln.text) if len(m.group(1)) > 2}
    if not urls:
        return []
    mutating = {u for u in urls if _MUTATING_URL.search(u)}
    readable = urls - mutating          # любой другой метод может оказаться методом чтения — тогда не шумим
    if readable or not mutating:
        return []
    api_parts = {top_part(ln) for ln in url_lines}
    promise = [ln for ln in lines if top_part(ln) not in api_parts and _READ_PROMISE.search(ln.text)
               and not _HEADING.match(ln.text)]
    if not promise:
        return []
    evidence = [ev(doc, promise[0].no)] + ([ev(doc, promise[1].no)] if len(promise) > 1 else []) + [ev(doc, url_lines[0].no)]
    return [Finding(
        scope="set", criterion="set_complete", severity=Severity.MAJOR,
        title="Документ обещает чтение данных, но в API описано только изменение",
        explanation=f"В тексте сказано, что данные читаются или показываются пользователю, однако в спецификации API "
                    f"есть только метод изменения ({', '.join(sorted(mutating))}). Неясно, откуда приложение возьмёт "
                    f"текущие настройки и значения для экрана — эту часть нельзя реализовать по документу.",
        recommendation="Добавить метод чтения (например, GET /…/settings) с описанием параметров, ответа и ошибок, "
                       "либо явно сослаться на существующий метод.",
        evidence=evidence[:3],
    )]


_NEGATIVE = re.compile(r"ошибк|отклон|неверн|некоррект|не\s+отправ|без\s+push|нет\s+push|не\s+меня|не\s+учит|"
                       r"запрещ|недоступ|отказ|401|403|404|400|исключ", re.I)


@rule("R-AC-COVERAGE")
def acceptance_coverage(doc: SourceDocument) -> list[Finding]:
    """Критериев приёмки меньше, чем функциональных требований, или нет ни одного негативного сценария."""
    ac = [s for s in doc.sections if re.search(r"критери\w*\s+при[её]мк|acceptance", s.title, re.I)]
    if not ac:
        return []
    sec = ac[0]
    rows = [t for t in doc.tables if sec.line_start <= t.header_line <= sec.line_end]
    n_cases = sum(len([r for r in t.rows if any(c.strip() for c in r)]) for t in rows)
    if not n_cases:
        return []
    ids = sorted({re.match(r"^([A-ZА-ЯЁ]{1,6}[-_.]?\d{1,3})", text).group(1)
                  for line, text in _id_paragraphs(doc)
                  if re.match(r"^([A-ZА-ЯЁ]{1,6}[-_.]?\d{1,3})", text)
                  and section_has(line, r"(?<!не)функциональн|functional")
                  and not section_has(line, r"нефункциональн|non-?functional")})
    body = " ".join(" ".join(r) for t in rows for r in t.rows)
    has_negative = bool(_NEGATIVE.search(body))
    problems = []
    if ids and n_cases < len(ids):
        problems.append(f"{n_cases} сценария(ев) на {len(ids)} функциональных требования(й) ({', '.join(ids)})")
    if not has_negative:
        problems.append("нет ни одного негативного сценария (ошибки ввода, отказ, граничные значения)")
    if not problems:
        return []
    return [Finding(
        scope="set", criterion="validatable", severity=Severity.MAJOR,
        title="Критерии приёмки не покрывают требования: " + "; ".join(problems),
        explanation="Без сценариев приёмки на каждое требование и на негативные случаи нельзя доказать, что "
                    "реализация соответствует документу: часть правил (границы порога, смена порога, возвраты, "
                    "ошибки API) останется без проверки.",
        recommendation="Добавить минимум по одному позитивному и одному негативному сценарию на каждое требование "
                       "с граничными значениями и связью с ID требования.",
        evidence=[ev(doc, sec.line_start)] + [ev(doc, t.header_line) for t in rows[:1]],
    )]
