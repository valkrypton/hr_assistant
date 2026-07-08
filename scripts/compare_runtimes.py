#!/usr/bin/env python3
"""
Golden-query comparison: legacy vs LangGraph runtime.

Runs the 20 canonical queries from SPEC.md under BOTH agent runtimes, across a
set of role scopes, and prints a side-by-side report of answer / tables / SQL /
tokens / latency. This is the human-reviewed gate before flipping the default
runtime to langgraph (PR7).

Requires a live LLM provider (set AI_PROVIDER + the provider's key, or run
Ollama) and a populated ERP database (DATABASE_URL, INCLUDED_TABLES).

Usage:
    uv run python scripts/compare_runtimes.py
    uv run python scripts/compare_runtimes.py --scope cto_ceo --scope dept_head:3
    uv run python scripts/compare_runtimes.py --json report.json
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.rbac.context import RBACContext  # noqa: E402
from core.rbac.roles import Role  # noqa: E402
from core.runtimes.base import AgentRunRequest  # noqa: E402

# The 20 canonical queries (SPEC.md "Canonical Query Test Suite").
CANONICAL_QUERIES = [
    "Who hasn't filled their daily logs this week?",
    "Who's not adding full 8 hours in their daily logs?",
    "Who got warnings in the last quarter?",
    "Any devs who resigned recently?",
    "Who is not performing well on the backend team?",
    "Who's available for a Django project starting May?",
    "Who's been non-billable for the last 2 months?",
    "Show me the backend team right now",
    "Who has experience with Sabre APIs?",
    "Find React devs with e-commerce experience available in May",
    "Which team has the most attrition this year?",
    "Who's on leave next week?",
    "How many new joiners did we have in 2025?",
    "Of the 2025 joiners, how many were employees and how many subcontractors?",
    "How many people who joined in 2025 also left in 2025?",
    "Break down all resignations by department",
    "Show resignations by years of experience — use 1-year brackets",
    "How many terminations did we have in 2023?",
    "How many Software Engineers, QA Engineers, and Product Managers do we have?",
    "What is Bilal Qureshi's competency score?",
]


def _parse_scope(spec: str) -> tuple[str, RBACContext | None]:
    """ "none" | "cto_ceo" | "hr_manager" | "dept_head:<id>" | "team_lead:<id>"."""
    if spec == "none":
        return spec, None
    if ":" in spec:
        role_name, ident = spec.split(":", 1)
        ident = int(ident)
    else:
        role_name, ident = spec, None
    role = Role(role_name)
    if role is Role.DEPT_HEAD:
        return spec, RBACContext(role=role, department_id=ident)
    if role is Role.TEAM_LEAD:
        return spec, RBACContext(role=role, team_id=ident)
    return spec, RBACContext(role=role)


def _run_one(runtime_name: str, question: str, rbac_ctx) -> dict:
    # Import here so a provider/import error surfaces per-run, not at module load.
    from core.config import settings

    settings.AGENT_RUNTIME = runtime_name
    from core.runtimes import get_runtime

    runtime = get_runtime()
    t0 = time.monotonic()
    try:
        result = runtime.run(AgentRunRequest(question=question, rbac_ctx=rbac_ctx))
        if rbac_ctx is not None:
            result.answer = rbac_ctx.strip_forbidden(result.answer)
        return {
            "answer": result.answer,
            "tables": result.tables_accessed,
            "sql": list(result.sql_statements),
            "tokens": result.total_tokens,
            "ms": int((time.monotonic() - t0) * 1000),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 — report, don't abort the sweep
        return {
            "answer": "",
            "tables": "",
            "sql": [],
            "tokens": 0,
            "ms": int((time.monotonic() - t0) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--scope",
        action="append",
        default=None,
        help="Role scope to test (repeatable). Default: none, cto_ceo.",
    )
    ap.add_argument("--json", type=str, default=None, help="Also write the full report as JSON.")
    args = ap.parse_args()

    scopes = [_parse_scope(s) for s in (args.scope or ["none", "cto_ceo"])]
    report: list[dict] = []

    for scope_label, rbac_ctx in scopes:
        print(f"\n{'=' * 78}\nSCOPE: {scope_label}\n{'=' * 78}")
        for i, question in enumerate(CANONICAL_QUERIES, 1):
            legacy = _run_one("legacy", question, rbac_ctx)
            langgraph = _run_one("langgraph", question, rbac_ctx)
            match = legacy["answer"].strip() == langgraph["answer"].strip()
            report.append(
                {
                    "scope": scope_label,
                    "n": i,
                    "question": question,
                    "legacy": legacy,
                    "langgraph": langgraph,
                    "answers_match": match,
                }
            )
            print(f"\n[{i:>2}] {question}")
            print(
                f"  legacy    ({legacy['ms']}ms, {legacy['tokens']}tok): {legacy['answer'][:120]}"
            )
            print(
                f"  langgraph ({langgraph['ms']}ms, {langgraph['tokens']}tok): "
                f"{langgraph['answer'][:120]}"
            )
            print(f"  {'MATCH' if match else 'DIFF ***REVIEW***'}")
            if legacy["error"] or langgraph["error"]:
                print(f"  errors: legacy={legacy['error']} langgraph={langgraph['error']}")

    matches = sum(1 for r in report if r["answers_match"])
    print(
        f"\n{'=' * 78}\nSUMMARY: {matches}/{len(report)} answers match verbatim "
        f"(diffs need human review, not necessarily wrong).\n{'=' * 78}"
    )

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2))
        print(f"Wrote {args.json}")


if __name__ == "__main__":
    main()
