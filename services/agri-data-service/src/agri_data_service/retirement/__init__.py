"""Drop-packet tooling for `environmental_postgres_retirement_20260904`. See `AGENTS.md` in this directory.

Nothing in this package opens a database, a bucket or a socket. Every public entry point is a pure
function of the checkout plus values an operator recorded, which is what lets a packet be assembled
and judged while production is unreachable -- and what makes it impossible for this tool to fire a
production action by accident.
"""

from agri_data_service.retirement.archive import ArchiveForm, ArchiveRecord, build_archive_record
from agri_data_service.retirement.ledger import (
    DropCandidate,
    DropForm,
    DropFormRefusedError,
    InventoryClass,
    Ledger,
    LedgerError,
    ObjectKind,
    load_ledger,
    parse_inventory,
)
from agri_data_service.retirement.packet import (
    Blocker,
    BlockerCode,
    DropPacket,
    DropPacketRequest,
    PacketError,
    build_packet,
)
from agri_data_service.retirement.parity import (
    PARITY_BINDINGS,
    PARQUET_REWRITE_EPOCHS,
    ParityAvailability,
    ParityReceiptError,
    RecordedLaneWriteProbe,
    ShortfallClass,
    assess_shortfall,
    normalize_parity_receipt,
)
from agri_data_service.retirement.readers import ReaderDisposition, ReaderScan, scan_for_readers

__all__ = [
    "PARITY_BINDINGS",
    "PARQUET_REWRITE_EPOCHS",
    "ArchiveForm",
    "ArchiveRecord",
    "Blocker",
    "BlockerCode",
    "DropCandidate",
    "DropForm",
    "DropFormRefusedError",
    "DropPacket",
    "DropPacketRequest",
    "InventoryClass",
    "Ledger",
    "LedgerError",
    "ObjectKind",
    "PacketError",
    "ParityAvailability",
    "ParityReceiptError",
    "ReaderDisposition",
    "ReaderScan",
    "RecordedLaneWriteProbe",
    "ShortfallClass",
    "assess_shortfall",
    "build_archive_record",
    "build_packet",
    "load_ledger",
    "normalize_parity_receipt",
    "parse_inventory",
    "scan_for_readers",
]
