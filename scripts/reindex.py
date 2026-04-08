#!/usr/bin/env python3
"""
Nightly vector re-index script.

Rebuilds two Chroma collections:
  hr_erp    — team/project descriptions for semantic ERP search
  hr_schema — schema.md sections for query-time schema RAG

Usage:
    python scripts/reindex.py           # rebuild both
    python scripts/reindex.py --erp     # ERP content only
    python scripts/reindex.py --schema  # schema only

Schedule example (cron, runs at 2 AM every night):
    0 2 * * * /path/to/.venv/bin/python /path/to/hr_assistant/scripts/reindex.py
"""

import argparse
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


def rebuild_erp_index() -> None:
    from core.vector_index import build_index
    logger.info("Rebuilding ERP content index (hr_erp)...")
    t0 = time.monotonic()
    build_index()
    logger.info("ERP index done in %.1fs.", time.monotonic() - t0)


def rebuild_schema_index() -> None:
    from core.context.schema_index import build_schema_index
    logger.info("Rebuilding schema RAG index (hr_schema)...")
    t0 = time.monotonic()
    build_schema_index()
    logger.info("Schema index done in %.1fs.", time.monotonic() - t0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild HR vector indexes.")
    parser.add_argument("--erp", action="store_true", help="Rebuild ERP content index only.")
    parser.add_argument("--schema", action="store_true", help="Rebuild schema RAG index only.")
    args = parser.parse_args()

    both = not args.erp and not args.schema

    if both or args.schema:
        rebuild_schema_index()
    if both or args.erp:
        rebuild_erp_index()

    logger.info("All done.")


if __name__ == "__main__":
    main()
