#!/usr/bin/env bash
# Launch an agri-cli historical backfill under the local DSN contract.
#
# The loader URL must differ from DATABASE_URL or Settings rejects it, so DATABASE_URL
# is pinned to an unroutable dummy and the real prod DSN is passed as the loader.
# CDS credentials are exported because _require_cds_credentials reads os.environ directly
# and pydantic-settings' env_file never populates it.
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

LOADER_DSN="$DATABASE_URL"
export LOCAL_SOURCE_LOADER_DATABASE_URL="$LOADER_DSN"
export DATABASE_URL="postgresql+asyncpg://unused:unused@127.0.0.1:1/unused"

echo "loader host: $(printf '%s' "$LOADER_DSN" | sed -E 's#.*@([^/]+)/.*#\1#')"
echo "verb: $*"
exec uv run agri-cli "$@"
