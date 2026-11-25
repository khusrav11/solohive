"""🎭 Пчела-сценарист: «проигрывает» ТЗ на конкретных сценариях и ищет, где документ не даёт однозначного результата."""

from __future__ import annotations

from pydantic import Field

from ..document import SourceDocument
from ..llm.base import BaseLLM, FatalLLMError
from ..models import Finding, Scenario
from .common import Loose, RawDefect, defect_to_finding, fill, ground_evidence, load_prompt, system_prompt


class RawScenario(Loose):
    title: str = ""
    given_when: str = ""
    spec_says: str = ""
    verdict: str = "undefined"
    quotes: list[str] = Field(default_factory=list)
    defect: RawDefect | None = None


class ScenarioResponse(Loose):
    scenarios: list[RawScenario] = Field(default_factory=list)


_VERDICTS = {"defined": "defined", "ok": "defined", "определено": "defined",
             "undefined": "undefined", "unknown": "undefined", "не определено": "undefined",
             "contradictory": "contradictory", "conflict": "contradictory", "противоречиво": "contradictory"}


async def play_scenarios(doc: SourceDocument, llm: BaseLLM, hints: str
                         ) -> tuple[list[Scenario], list[Finding], list[str]]:
    user = fill(load_prompt("scenarios"), TITLE=doc.title, DOC=doc.numbered(), RULE_HINTS=hints)
    try:
        resp = await llm.generate_json(system_prompt(), user, ScenarioResponse, stage="scenarios")
    except FatalLLMError:
        raise
    except Exception as exc:  # noqa: BLE001
        return [], [], [f"Сценарный анализ не выполнен: {exc}"]
    scenarios: list[Scenario] = []
    findings: list[Finding] = []
    for i, raw in enumerate([r for r in resp.scenarios if r.title.strip()], start=1):
        verdict = _VERDICTS.get(raw.verdict.strip().lower(), "undefined")
        evidence = [e for e in ground_evidence(doc, raw.quotes) if e.verified]
        sc = Scenario(sid=f"S-{i:02d}", title=raw.title.strip(), given_when=raw.given_when.strip(),
                      spec_says=raw.spec_says.strip(), verdict=verdict, evidence=evidence)
        if verdict != "defined" and raw.defect and raw.defect.title.strip():
            d = raw.defect
            if not d.all_quotes():
                d.quotes = list(raw.quotes)
            f = defect_to_finding(doc, d, scope="set", source="llm:scenario")
            if not f.explanation:
                f.explanation = f"Сценарий: {sc.given_when}. ТЗ: {sc.spec_says}"
            else:
                f.explanation = f"Сценарий «{sc.title}»: {sc.given_when}. {f.explanation}"
            f.scenario = sc.sid
            findings.append(f)
        scenarios.append(sc)
    return scenarios, findings, []
