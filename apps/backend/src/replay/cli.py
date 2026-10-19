"""CLI: python -m src.replay <scenario.json>"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from src.replay.runner import LLMMode, ReplayRunner, graph_pipeline_factory
from src.replay.scenario import load_scenario


def _ensure_mock_env() -> None:
    """Bypass startup validation when no API keys are set."""
    os.environ.setdefault("INTERVIEW_PILOT_ANTHROPIC_API_KEY", "test-key")
    os.environ.setdefault("INTERVIEW_PILOT_OPENAI_API_KEY", "test-key")


async def _run(scenario, llm_mode: "LLMMode", real_time: bool):
    """The session pipeline checkpoints to Postgres, so replays need the database."""
    from src.config import settings
    from src.graphs.checkpointer import Checkpointer
    from src.graphs.code_graph import code_thread_id
    from src.workers.langgraph_session import thread_id_for

    checkpointer = Checkpointer()
    await checkpointer.open(settings.database_url_sync)
    threads = (thread_id_for(scenario.meta.id), code_thread_id(scenario.meta.id))
    try:
        await checkpointer.delete_threads(*threads)
        runner = ReplayRunner(
            scenario, llm_mode=llm_mode, real_time=real_time, pipeline_factory=graph_pipeline_factory(checkpointer)
        )
        return await runner.run()
    finally:
        await checkpointer.delete_threads(*threads)
        await checkpointer.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.replay",
        description="Replay an interview scenario through the analytical engine.",
    )
    parser.add_argument("scenario", type=Path, help="Path to a .scenario.json file")
    parser.add_argument(
        "--llm",
        choices=["mock", "scripted", "real"],
        default="mock",
        help="LLM mode: mock (canned), scripted (input-sensitive, deterministic), real (default: mock)",
    )
    parser.add_argument(
        "--real-time",
        action="store_true",
        help="Honor delay_ms between events instead of running compressed",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write JSON report to this path instead of stdout",
    )
    args = parser.parse_args()

    if args.llm in ("mock", "scripted"):
        _ensure_mock_env()

    scenario = load_scenario(args.scenario)
    report = asyncio.run(_run(scenario, args.llm, args.real_time))

    payload = json.dumps(report.to_dict(), indent=2, default=str)
    if args.output:
        args.output.write_text(payload)
    else:
        print(payload)

    return 2 if report.error else 0


if __name__ == "__main__":
    sys.exit(main())
