"""The `soil-survey` layer's source protocol, its SSURGO binding, and an offline acquisition CLI.

DELIBERATELY EMPTY OF RE-EXPORTS, matching `pipeline/direct/soil/__init__.py` and
`pipeline/direct/climate/__init__.py`: callers import the submodule they mean, so a package
`__init__` can never close an import cycle through `pipeline/parquet/lane_registry.py`, and this
package's own `__main__.py` never becomes reachable through `import agri_data_service.pipeline.
direct.soil_survey` alone (`tests/direct/test_direct_writer_contract.py::
test_declared_non_writer_modules_really_have_no_parser` checks exactly this). `source.py` and
`capture.py` acquire SSURGO source-direct, into local content-addressed receipts; nothing here is
registered as a scheduled lane or reachable from the served map (`AGENTS.md`, "Operational entry
point"). See `AGENTS.md` in this directory for why the *live* per-region protocol below still has
no pull despite that CLI existing.
"""
