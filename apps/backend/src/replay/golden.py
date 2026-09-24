"""Golden-file normalization for replay reports.

A golden captures everything an implementation of the real-time pipeline is
observably responsible for under the scripted LLM:
  - dashboard broadcasts, in order;
  - request/response results (runtime hints, code ratings);
  - every LLM call's prompt, grouped by call site;
  - the final session state.

Wall-clock and random values are masked. LLM calls are grouped by call site
and kept in order within each group: calls from different background streams
(summary compression vs. analysis) may legitimately interleave differently in
another implementation, but each stream's own sequence must match.
"""

import json
from pathlib import Path
from typing import Any

from src.replay.runner import ReplayReport

GOLDEN_VERSION = 1

VOLATILE_KEYS = frozenset({"timestamp", "detected_at", "hint_id", "latency_ms"})
MASK = "<volatile>"


def _mask(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (MASK if k in VOLATILE_KEYS else _mask(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask(v) for v in value]
    return value


def normalize(report: ReplayReport) -> dict:
    llm_calls_by_kind: dict[str, list[dict]] = {}
    for call in report.llm_calls:
        entry = {k: v for k, v in call.items() if k not in ("seq", "kind")}
        llm_calls_by_kind.setdefault(call["kind"], []).append(entry)

    # Round-trip through JSON so enums/pydantic leftovers compare as plain data.
    return json.loads(
        json.dumps(
            {
                "golden_version": GOLDEN_VERSION,
                "scenario_id": report.scenario_id,
                "error": report.error,
                "dashboard_events": [_mask({"method": e.method, "payload": e.payload}) for e in report.events],
                "hint_responses": _mask(report.hint_responses),
                "code_ratings": _mask(report.code_ratings),
                "llm_calls_by_kind": dict(sorted(llm_calls_by_kind.items())),
                "final_state": _mask(report.final_state),
            },
            default=str,
        )
    )


def golden_path(golden_dir: Path, scenario_id: str) -> Path:
    return golden_dir / f"{scenario_id}.golden.json"


def write_golden(path: Path, normalized: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n")


def read_golden(path: Path) -> dict:
    return json.loads(path.read_text())
