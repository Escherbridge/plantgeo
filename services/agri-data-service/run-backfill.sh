#!/usr/bin/env bash
# Launch an agri-cli historical backfill against the DSN in .env.
#
# The loader DSN is pinned explicitly so an unrelated value in the operator's shell cannot
# redirect the load. See services/agri-data-service/README.md §3.1.
#
# Usage: ./run-backfill.sh <agri-cli-verb> [args...]

set -euo pipefail
cd "$(dirname "$0")"

set -a
# shellcheck disable=SC1091
. ./.env
set +a

if [ -z "${DATABASE_URL:-}" ]; then
  echo "DATABASE_URL missing from .env" >&2
  exit 1
fi

export LOCAL_SOURCE_LOADER_DATABASE_URL="$DATABASE_URL"

echo "loader host: $(printf '%s' "$DATABASE_URL" | sed -E 's#.*@([^/]+)/.*#\1#')"
echo "verb: $*"
exec uv run agri-cli "$@"
