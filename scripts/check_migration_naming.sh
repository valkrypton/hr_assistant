#!/usr/bin/env bash
# Pre-commit hook: enforce that every staged Alembic migration follows
# the NNNN_slug convention and that the in-file `revision = "..."` value
# matches the filename. Invoked from .pre-commit-config.yaml with the
# staged file paths passed as arguments.
#
# Why this exists:
#   Alembic's default hex revision ids (e.g. 50984e4fef6b_baseline.py) are
#   unordered and meaningless at a glance. Going forward, generate new
#   migrations with an explicit --rev-id so the filename is self-describing
#   and sorts chronologically:
#
#     uv run alembic revision --autogenerate --rev-id "0002_add_x" -m "add x"
#
# Pre-existing migrations are grandfathered: pre-commit only runs against
# *staged* files, so files committed before this hook existed aren't
# re-validated.

set -euo pipefail

FILENAME_PATTERN='^[0-9]{4}_[a-z0-9_]+\.py$'
REV_ID_PATTERN='^[0-9]{4}_[a-z0-9_]+$'

failed=0

for path in "$@"; do
  base=$(basename "$path")

  if ! echo "$base" | grep -qE "$FILENAME_PATTERN"; then
    echo "✗ $path"
    echo "    filename must match NNNN_slug.py (zero-padded prefix, lowercase, underscores)"
    failed=1
    continue
  fi

  expected_rev=${base%.py}

  actual_rev=$(grep -E '^revision[[:space:]:][^=]*=' "$path" \
    | sed -E 's/^revision[^=]*=[[:space:]]*"([^"]+)".*/\1/' \
    | head -n1 || true)

  if [ -z "$actual_rev" ]; then
    echo "✗ $path"
    echo "    no top-level 'revision = \"...\"' assignment found"
    failed=1
    continue
  fi

  if [ "$actual_rev" != "$expected_rev" ]; then
    echo "✗ $path"
    echo "    revision id ($actual_rev) must match filename ($expected_rev)"
    echo "    regenerate with: uv run alembic revision --autogenerate --rev-id \"$expected_rev\" -m \"...\""
    failed=1
    continue
  fi

  if ! echo "$actual_rev" | grep -qE "$REV_ID_PATTERN"; then
    echo "✗ $path"
    echo "    revision id must match NNNN_slug"
    failed=1
  fi
done

exit $failed
