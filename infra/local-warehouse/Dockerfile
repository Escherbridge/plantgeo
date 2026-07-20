ARG BASE_IMAGE=timescale/timescaledb-ha@sha256:9965a1682187e30d36a24e303b77f1b265ff7458b383eb3304dd1d8d65edb3fc
FROM ${BASE_IMAGE}

USER root

# Keep packaged capabilities but require the operator's explicit extension gate.
# Fail closed if the verified upstream init-script contract changes.
RUN test -f /docker-entrypoint-initdb.d/000_install_timescaledb.sh \
    && test -f /docker-entrypoint-initdb.d/001_timescaledb_tune.sh \
    && test -f /docker-entrypoint-initdb.d/010_install_timescaledb_toolkit.sh \
    && rm \
      /docker-entrypoint-initdb.d/000_install_timescaledb.sh \
      /docker-entrypoint-initdb.d/001_timescaledb_tune.sh \
      /docker-entrypoint-initdb.d/010_install_timescaledb_toolkit.sh \
    && test ! -e /docker-entrypoint-initdb.d/000_install_timescaledb.sh \
    && test ! -e /docker-entrypoint-initdb.d/001_timescaledb_tune.sh \
    && test ! -e /docker-entrypoint-initdb.d/010_install_timescaledb_toolkit.sh

USER postgres
