#!/usr/bin/env python3
"""
Nightly vector re-index script.

Rebuilds the Chroma collection:
  hr_erp — team/project descriptions for semantic ERP search (FR-4)

Usage:
    python scripts/reindex.py

Schedule example (cron, runs at 2 AM every night):
    0 2 * * * /path/to/.venv/bin/python /path/to/hr_assistant/scripts/reindex.py
"""

import sys
import time
from pathlib import Path

import structlog

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.logging import configure_logging  # noqa: E402

configure_logging()
logger = structlog.get_logger("reindex")


def main() -> None:
    from core.vector_index import build_index

    logger.info("reindex_started")
    t0 = time.monotonic()
    build_index()
    logger.info("reindex_finished", duration_s=round(time.monotonic() - t0, 1))


if __name__ == "__main__":
    main()
