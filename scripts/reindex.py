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

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("reindex")


def main() -> None:
    from core.vector_index import build_index
    logger.info("Rebuilding ERP content index (hr_erp)...")
    t0 = time.monotonic()
    build_index()
    logger.info("ERP index done in %.1fs.", time.monotonic() - t0)
    logger.info("All done.")


if __name__ == "__main__":
    main()
