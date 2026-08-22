"""The `weather-observations` domain: NASA POWER and Open-Meteo ERA5-Land historical producers.

Domain package under the shared execute path (RUNBOOK §0.25.1 decision 2). It may import
`execution` root primitives; it may NOT import another `execution.<domain>` package -- enforced by
`tests/test_layer_import_contract.py`. See `AGENTS.md` in this directory.
"""
