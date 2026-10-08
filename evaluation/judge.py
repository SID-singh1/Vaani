"""LLM-as-judge for the parts of a note that string metrics can't grade.

Per clip, one call answers:
  * recall: which expected action items are covered by the generated ones;
  * precision: which generated action items are actually supported by the transcript
    (anything else is a hallucinated task);
  * faithfulness: whether the summary makes claims the transcript doesn't support.

The judge sees the human reference transcript, not the system's own transcript, so ASR
errors that change meaning are penalized. Use a judge model from a different vendor than
the model under test to limit self-preference bias. Verdicts are cached on disk so
re-running a report doesn't spend API quota again.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from vaani.analysis import load_json_object
from vaani.engines.base import LLMClient

CACHE_PATH = Path(__file__).resolve().parent / ".cache" / "judge.json"

JUDGE_SYSTEM = """\
You are a strict, literal grader of an AI system that turns voice notes into summaries and action items.
You are given the true transcript of a voice note (Hindi/English code-mixed), the action items a human
expected, and the system's output. Grade only against the transcript. Return JSON."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "expected": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"index": {"type": "integer"}, "covered": {"type": "boolean"}},
                "required": ["index", "covered"],
            },
        },
        "generated": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"index": {"type": "integer"}, "supported": {"type": "boolean"}},
                "required": ["index", "supported"],
            },
        },
        "summary_unsupported_claims": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["expected", "generated", "summary_unsupported_claims"],
}


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{i}. {item}" for i, item in enumerate(items, 1)) or "(none)"


def build_prompt(reference: str, expected: list[str], summary: str, generated: list[str]) -> str:
    return f"""TRUE TRANSCRIPT:
<transcript>
{reference}
</transcript>

EXPECTED ACTION ITEMS (written by a human):
{_numbered(expected)}

SYSTEM SUMMARY:
{summary}

SYSTEM ACTION ITEMS:
{_numbered(generated)}

Instructions:
- "expected": for every expected item (by number), covered=true if some system action item asks for the same
  action. Wording, owner and deadline may differ; the task itself must match.
- "generated": for every system action item (by number), supported=true if the transcript states or clearly
  implies that task, decision or follow-up. Invented or distorted tasks are unsupported.
- "summary_unsupported_claims": list any factual claims in the summary that the transcript does not support
  (empty list if none)."""


class Judge:
    def __init__(self, llm: LLMClient):
        self.llm = llm
        self.cache: dict = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}

    def _save(self) -> None:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1), encoding="utf-8")

    async def grade(self, reference: str, expected: list[str], summary: str, generated: list[str]) -> dict:
        prompt = build_prompt(reference, expected, summary, generated)
        key = hashlib.sha256(f"{self.llm.name}\n{JUDGE_SYSTEM}\n{prompt}".encode()).hexdigest()
        if key not in self.cache:
            text = await self.llm.generate(
                system=JUDGE_SYSTEM, user=prompt, json_schema=JUDGE_SCHEMA, max_output_tokens=2048, temperature=0.0
            )
            self.cache[key] = load_json_object(text)
            self._save()
        verdict = self.cache[key]

        covered = {e.get("index") for e in verdict.get("expected", []) if e.get("covered")}
        supported = {g.get("index") for g in verdict.get("generated", []) if g.get("supported")}
        n_expected, n_generated = len(expected), len(generated)
        return {
            "judge": self.llm.name,
            "covered": sorted(i for i in covered if isinstance(i, int) and 1 <= i <= n_expected),
            "supported": sorted(i for i in supported if isinstance(i, int) and 1 <= i <= n_generated),
            "recall": (len(covered & set(range(1, n_expected + 1))) / n_expected) if n_expected else None,
            "precision": (len(supported & set(range(1, n_generated + 1))) / n_generated) if n_generated else None,
            "summary_unsupported_claims": verdict.get("summary_unsupported_claims") or [],
        }
