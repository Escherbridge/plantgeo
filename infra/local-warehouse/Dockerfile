# postgis/postgis:16-3.4 -- same PostgreSQL 16 major as the retired timescale/timescaledb-ha:pg16 base.
ARG BASE_IMAGE=postgis/postgis@sha256:44126d872ac91993766c341e369c539e8196614321765d36a6f1bab0419a5fa5
FROM ${BASE_IMAGE}

USER root

# Keep packaged capabilities but require the operator's explicit extension gate.
# Fail closed if the verified upstream init-script contract changes.
RUN test -f /docker-entrypoint-initdb.d/10_postgis.sh \
    && rm /docker-entrypoint-initdb.d/10_postgis.sh \
    && test ! -e /docker-entrypoint-initdb.d/10_postgis.sh

USER postgres
