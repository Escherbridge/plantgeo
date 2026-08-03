# Scheduled ingestion trigger: Railway runs this container on the
# cronSchedule in railway.json; it calls the cron route once and exits.
FROM curlimages/curl:8.10.1
# The route ACKs with 202 and runs ingestion detached; 409 = a run is already
# in flight, which is a healthy outcome for an overlapping cron tick.
ENTRYPOINT ["/bin/sh", "-c", "code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 60 -H \"x-cron-secret: $CRON_SECRET\" https://plantgeo.aevani.com/api/cron/ingest); echo \"ingest trigger: HTTP $code\"; case $code in 202|409) exit 0;; *) exit 1;; esac"]
