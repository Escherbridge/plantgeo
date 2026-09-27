---
type: runbook
date: 2026-09-27
---

# Probing the private data services

`plantgeo-parquet-api` and `plantgeo-strategy-knowledge` have **no public domain**. The owner decided this on
2026-09-27: they are private-only, and internal connections within project Aevani are allowed. Production reaches
them over Railway private networking. For example, plantgeo-main uses
`AGRI_PARQUET_SERVICE_URL=http://plantgeo-parquet-api.railway.internal:8080`.

To check them yourself for release checkpoints, QA or debugging, run the request from **inside** a project service
with `railway ssh`. plantgeo-main has `node` 18+ and therefore `fetch`, so no extra tooling is needed.

```bash
P=6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990   # project Aevani
probe() { railway ssh -p "$P" -e production -s plantgeo-main -- node -e "$1"; }

# Readiness: parquet API + strategy-knowledge (reports the served corpus_version)
probe "Promise.all(['http://plantgeo-parquet-api.railway.internal:8080/ready','http://plantgeo-strategy-knowledge.railway.internal:8000/ready'].map(u=>fetch(u).then(async r=>u+' '+r.status+' '+(await r.text()).slice(0,160)))).then(x=>console.log(x.join('\n')))"

# Agent-tools bridge: catalogue, then one literature call
probe "fetch('http://plantgeo-parquet-api.railway.internal:8080/api/v1/agent-tools').then(async r=>console.log(r.status,(await r.text()).slice(0,200)))"
probe "fetch('http://plantgeo-parquet-api.railway.internal:8080/api/v1/agent-tools/call',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name:'search_environmental_strategies',arguments:{query:'erosion control after wildfire on steep slopes',limit:2}})}).then(async r=>console.log(r.status,(await r.text()).slice(0,300)))"
```

- All three commands were verified on 2026-09-27: `/ready` returned 200 on both services, and the literature call
  returned 200 with `evidenceOrigin: literature`.
- Wrap the JS in double quotes and use single quotes inside it. That works in Git Bash and in PowerShell.
- `railway ssh` needs a registered SSH key; see `railway ssh keys`.
- Each call opens a short session. Nothing keeps running afterwards.
- Evidence that used to cite `https://plantgeo-parquet-api-production.up.railway.app/...` should now cite these
  commands. That hostname stopped resolving on 2026-09-27.
- Reversal is an owner decision: `railway domain` or the MCP `generate-domain` creates a new public domain. The
  generated hostname may differ from the old one.
