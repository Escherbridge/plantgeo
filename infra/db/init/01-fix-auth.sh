#!/bin/bash
set -e

# Fix pg_hba.conf to use md5 instead of scram-sha-256 for external connections
# This runs during container initialization
PG_HBA="$PGDATA/pg_hba.conf"

if [ -f "$PG_HBA" ]; then
  sed -i 's/host all all all scram-sha-256/host all all all md5/' "$PG_HBA"
  echo "Fixed pg_hba.conf: switched external auth to md5"
fi

# Ensure the geo user password is set correctly
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  ALTER USER geo WITH PASSWORD '${POSTGRES_PASSWORD:-geopass}';
EOSQL

# Reload configuration
pg_ctl reload
