# Fire detections — direct FIRMS writer

This package keeps the source-direct lane in the same lifecycle order as the UI layer it supplies:
the selected *Fire Detections* layer has a bounded source request, a conformed cell-day population,
and a publication ladder. It does not create a backend `fire/` group merely because the front end
groups four fire-related layers; each source-backed layer remains independently deployable and
registered.

## Module boundaries

- `products.py` owns the lane identity, tier ladder, and bounds; `forward.py` owns the
  `WRITER_CONTRACT` beside its parser, like every active direct writer.
- `models.py` carries the bounded-turn request and complete source-day evidence.
- `source.py` fetches every FIRMS product that covers one exact settled UTC day. A missing
  constellation member refuses that day; it must not look like a quiet fire day.
- `rows.py` deduplicates/conforms that complete response to the registered 0.005-degree cell-day
  schema. An identity failure still refuses the whole day, the declared lane-specific policy.
- `adapter.py` writes the base rung or governs a complete zero-row day absent, and retracts a prior
  absence before a later non-empty response is written.
- `support.py` holds retry/census mechanics. `fire_detections_forward_retry` remains the source/R2
  retry event; the publication loop emits `fire_detections_forward_r2_retry` separately.
- `forward.py` owns CLI parsing and the bounded lock → refetch → publish → verify walk.

`PipelineOperationError` in `pipeline/errors.py` is the shared operation error. Its stable
`PipelineErrorContext` carries code, lane, stage, and retryability without changing the message a
CLI operator or existing caller sees.
The `DirectFireDetectionsError` export is only a compatibility alias, so existing callers retain the
same message and exit behavior without adding another lane-specific exception class.

## Entry point and compatibility

`python -m agri_data_service.pipeline.direct.fire_detections` calls `__main__.py`; package-level
exports preserve `WRITER_CONTRACT`, `_parse_args`, source/row helpers, configuration, adapter, and
the historical `DirectFireDetectionsError` import name. Do not reintroduce a flat sibling module:
the direct-lane import contract treats every flat `*.py` there as a lane.
