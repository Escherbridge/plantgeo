"""The two provenance classes an availability row may be bound by, and what each one has to prove.

`digested` is the ordinary class: the row names every part it publishes and each name carries a
SHA-256 computed from that object's bytes. `manifest_trusted` is the bootstrap-only class owner
decision D3 (`environmental_postgres_retirement_20260904`) introduced: the row names NO part and
rests on its completion marker, which is still fetched and digested. It exists because hashing every
part of every lane-day -- for `fire-detections`, every day since 2000-11-01 at four rungs -- would
have put the time slider's startup fix behind the whole cutover.

THREE PROPERTIES HERE ARE LOAD-BEARING:

  * The class is DERIVED FROM THE ROW'S SHAPE, never from a column. `AVAILABILITY_INDEX_SCHEMA` is
    frozen at version 1, so a provenance column would not survive the generation round trip
    `_write_generation` re-reads and compares. Published, holding rows, and naming no part is a
    claim only a trusted row can make.
  * A DECLARATION is checked against that shape, never believed. The bootstrap input and the terminal
    evidence may both say the class out loud; a document that says one and carries the other dies.
  * The class stops at the bootstrap, and it is stopped at the CHOKEPOINT both forward callers pass
    through -- `_publish_availability_owned` -- not merely at the document loader one of them uses.
    A forward publication holds every part digest it just wrote, so a trusted row there would grow
    the region D3 deliberately bounded to history.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.pipeline.parquet.availability_index import (
    DIGESTED_PROVENANCE,
    MANIFEST_TRUSTED_PROVENANCE,
    PROVENANCE_FIELD,
    AvailabilityConfig,
    AvailabilityConflictError,
    EvidenceReceipt,
    PublicationRequest,
    availability_provenance_summary,
    availability_row_provenance,
    load_bootstrap_request,
    load_publication_request,
)
from agri_data_service.pipeline.parquet.availability_index import (
    _bootstrap_availability_owned as bootstrap_availability,
)
from agri_data_service.pipeline.parquet.availability_index import (
    _publish_availability_owned as publish_availability_owned,
)
from agri_data_service.warehouse.schemas.availability_index import AVAILABILITY_REQUIRED_RUNGS
from tests.parquet.availability_documents import (
    CEILING,
    CREATED_AT,
    DIGESTED_DAY,
    FORWARD_TRUSTED_DAY,
    ROW_COUNT,
    TRUSTED_DAY,
    MemoryAvailabilityStorage,
)
from tests.parquet.availability_documents import bootstrap_document as _bootstrap_document
from tests.parquet.availability_documents import bootstrap_request as _bootstrap_request
from tests.parquet.availability_documents import part_key as _part_key
from tests.parquet.availability_documents import publication_document as _publication_document
from tests.parquet.availability_documents import trusted_day_rows as _trusted_day_rows
from tests.parquet.availability_documents import write_document as _write_document

if TYPE_CHECKING:
    from pathlib import Path


def test_the_class_is_derived_from_the_row_shape_alone() -> None:
    """Published, holding rows, naming no part: the one shape a trusted row has, and the only one."""
    part = EvidenceReceipt(key=_part_key(DIGESTED_DAY, 13, 0), sha256="c" * 64)

    assert (
        availability_row_provenance(terminal_state="published", row_count=ROW_COUNT, data_receipts=())
        == MANIFEST_TRUSTED_PROVENANCE
    )
    assert (
        availability_row_provenance(terminal_state="published", row_count=ROW_COUNT, data_receipts=(part,))
        == DIGESTED_PROVENANCE
    )
    # An emptied derived rung proves its emptiness outright and a governed absence proves it with an
    # absence marker; neither asserts anything about parts, so neither is TRUSTING anything.
    assert availability_row_provenance(terminal_state="published", row_count=0, data_receipts=()) == (
        DIGESTED_PROVENANCE
    )
    assert availability_row_provenance(terminal_state="governed_absence", row_count=0, data_receipts=()) == (
        DIGESTED_PROVENANCE
    )


def test_a_bootstrap_mixing_both_classes_publishes_and_records_the_split() -> None:
    """The whole point: a lane binds recent days by digest and old days by their markers, and says so."""
    store = MemoryAvailabilityStorage()
    request = _bootstrap_request(store)

    result = bootstrap_availability(store, request)

    assert result.advanced is True
    recorded = json.loads(store.objects[result.pointer.bootstrap_receipt.key].payload)[PROVENANCE_FIELD]
    assert recorded == availability_provenance_summary(request.rows)
    assert recorded[MANIFEST_TRUSTED_PROVENANCE] == {
        "earliest_day": TRUSTED_DAY.isoformat(),
        "latest_day": TRUSTED_DAY.isoformat(),
        "row_count": len(AVAILABILITY_REQUIRED_RUNGS),
    }
    assert recorded[DIGESTED_PROVENANCE]["row_count"] == len(AVAILABILITY_REQUIRED_RUNGS)
    # NOT ONE PART OF THE TRUSTED DAY WAS FETCHED, which is the cost this class exists to avoid.
    assert not [key for key in store.read_log if "part-" in key and f"year={TRUSTED_DAY.year:04d}" in key]
    assert _part_key(DIGESTED_DAY, 13, 0) in store.read_log


def test_a_trusted_row_must_agree_with_the_marker_it_rests_on() -> None:
    """Nothing else proves a trusted row's row_count, so a marker that disagrees ends the bootstrap."""
    store = MemoryAvailabilityStorage()
    request = _bootstrap_request(store, trusted_marker_row_count=ROW_COUNT + 1)

    with pytest.raises(AvailabilityConflictError, match="completion counts do not match"):
        bootstrap_availability(store, request)


def test_a_digested_row_must_agree_with_the_parts_its_marker_recorded() -> None:
    """A marker that recorded its own digests is evidence, so a row citing different ones is refused."""
    store = MemoryAvailabilityStorage()
    request = _bootstrap_request(store, recorded_digest="f" * 64)

    with pytest.raises(AvailabilityConflictError, match="completion marker parts and terminal data receipts"):
        bootstrap_availability(store, request)


def test_a_row_declaring_a_class_it_does_not_have_is_refused(tmp_path: Path) -> None:
    """The declaration is checked against the shape; a document that lies about it never loads."""
    store = MemoryAvailabilityStorage()
    request = _bootstrap_request(store)
    rows = [{**row.to_wire(), PROVENANCE_FIELD: MANIFEST_TRUSTED_PROVENANCE} for row in request.rows]
    document = _bootstrap_document(request, rows=rows)
    path = _write_document(tmp_path, document)

    with pytest.raises(ValueError, match="has the shape of digested"):
        load_bootstrap_request(
            path,
            expected_sha256=sha256_digest(path.read_bytes()),
            expected_row_count=len(request.rows),
        )


def test_a_forward_publication_may_not_carry_a_trusted_row(tmp_path: Path) -> None:
    """The trusted region is bounded to history: a writer that just wrote a day holds its digests."""
    store = MemoryAvailabilityStorage()
    request = _bootstrap_request(store)
    document = _publication_document(request)
    path = _write_document(tmp_path, document)

    with pytest.raises(ValueError, match="manifest-trusted row"):
        load_publication_request(
            path,
            expected_sha256=sha256_digest(path.read_bytes()),
            expected_row_count=len(AVAILABILITY_REQUIRED_RUNGS),
        )


def test_the_publisher_itself_refuses_a_trusted_row_with_no_document_in_sight() -> None:
    """DO NOT DELETE. The document loader is not the forward path, and guarding only it guards nobody.

    `load_publication_request` serves exactly ONE caller, the `interface/cli/data.py` command. The
    primary forward writer is `availability_extension._publish_rows`, which assembles its
    `PublicationRequest` in memory and calls `publish_availability` straight through -- no document,
    no loader, no guard. Owner decision D4's eight direct-to-Parquet writers all publish this way,
    and the template they will be copied from does pass `provenance=`, so a trusted row reaching the
    publisher is one careless copy away rather than hypothetical. This test publishes exactly as they
    will: nothing is written to disk and no bytes are hashed but the request's own.
    """
    store = MemoryAvailabilityStorage()
    request = _bootstrap_request(store)
    bootstrapped = bootstrap_availability(store, request)
    rows = _trusted_day_rows(store, request.identity, FORWARD_TRUSTED_DAY, marker_row_count=ROW_COUNT)
    reads_before = len(store.read_log)

    with pytest.raises(ValueError, match="manifest-trusted row"):
        publish_availability_owned(
            store,
            PublicationRequest(
                config=AvailabilityConfig(
                    identity=request.identity,
                    source_ceiling=CEILING,
                    bootstrap_receipt=bootstrapped.pointer.bootstrap_receipt,
                ),
                created_at=CREATED_AT,
                rows=tuple(sorted(rows, key=lambda row: (row.day, row.rung))),
                input_sha256=sha256_digest(b"forward publication input"),
            ),
        )

    # REFUSED BEFORE THE HEAD IS EVEN READ: the guard sits beside `_validate_generation_rows`, above
    # every fetch, so a bad publication costs one validation pass rather than a round of evidence.
    assert len(store.read_log) == reads_before
