"""Daily backups of the owner's feedback to the blob store, and restoring them (#330).

Feedback is the one thing in the store that can't be rebuilt (CLAUDE.md, "Cost
of failure"), and Firestore's point-in-time recovery is off. A backup is one
JSON-lines file per collection under ``backups/<YYYY-MM-DD>/``, then
``manifest.json`` with the counts. The manifest is written last, so its
presence means every collection file before it was written in full.

Nothing here deletes, from the blob store or the repository. Backups sit
outside both bucket lifecycle rules (``articles/`` and ``feeds/``), so nothing
else deletes them either.

Logs carry counts, keys and durations only, never an event's contents: those
are the owner's reading history.

Cost: one ``list_feedback`` per backup, which on Firestore is one document read
per stored event, plus one write per collection file and one for the manifest.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from pydantic import BaseModel, ValidationError

from newsfeed.logging import get_logger
from newsfeed.models import Feedback
from newsfeed.ports import BlobNotFoundError, BlobStore, Repository

__all__ = [
    "BACKUP_PREFIX",
    "COLLECTIONS",
    "MANIFEST_NAME",
    "BackupError",
    "BackupManifest",
    "backup_due",
    "backup_key",
    "latest_backup_day",
    "restore_backup",
    "write_backup",
]

log = get_logger(__name__)

BACKUP_PREFIX = "backups"
MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True)
class _Collection:
    """How one collection is read for a backup and written back on restore."""

    name: str
    model: type[BaseModel]
    dump: Callable[[Repository], Sequence[BaseModel]]
    save: Callable[[Repository, BaseModel], None]

    @property
    def file_name(self) -> str:
        return f"{self.name}.jsonl"


def _save_feedback(repository: Repository, item: BaseModel) -> None:
    assert isinstance(item, Feedback)
    repository.save_feedback(item)


# The collections a backup holds. Preferences (#334) join with one more entry.
_COLLECTIONS: tuple[_Collection, ...] = (
    _Collection("feedback", Feedback, lambda repo: repo.list_feedback(), _save_feedback),
)
COLLECTIONS: tuple[str, ...] = tuple(c.name for c in _COLLECTIONS)


class BackupError(Exception):
    """A backup can't be restored: missing, incomplete or not readable."""


class BackupManifest(BaseModel):
    """What ``manifest.json`` records about one day's backup."""

    day: date
    run_id: str | None
    written_at: datetime
    counts: dict[str, int]


def backup_key(day: date, name: str) -> str:
    """The blob key of file ``name`` in the backup for ``day``."""
    return f"{BACKUP_PREFIX}/{day.isoformat()}/{name}"


def backup_due(blobs: BlobStore, day: date) -> bool:
    """True when ``day`` has no complete backup yet, i.e. no manifest."""
    return not blobs.exists(blobs.ref_for(backup_key(day, MANIFEST_NAME)))


def latest_backup_day(blobs: BlobStore, today: date, *, lookback_days: int = 7) -> date | None:
    """The newest day in ``today - lookback_days .. today`` with a complete backup, else None.

    ``BlobStore`` can't list keys, so this probes each day's manifest. A day
    with collection files but no manifest is incomplete and doesn't count.
    """
    for n in range(lookback_days + 1):
        day = today - timedelta(days=n)
        if not backup_due(blobs, day):
            return day
    return None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def write_backup(
    repository: Repository,
    blobs: BlobStore,
    *,
    day: date,
    run_id: str | None,
    clock: Callable[[], datetime] = _utc_now,
) -> BackupManifest:
    """Write every collection to ``backups/<day>/``, then the manifest, and return it.

    Each item is one JSON line (``model_dump(mode="json")``). An existing backup
    for ``day`` is replaced; callers that must not replace one check
    ``backup_due`` first. If a collection write raises, the manifest isn't
    written, so the backup stays incomplete (and due) and the error propagates.
    """
    began = time.perf_counter()
    counts: dict[str, int] = {}
    for collection in _COLLECTIONS:
        items = collection.dump(repository)
        body = "".join(json.dumps(i.model_dump(mode="json")) + "\n" for i in items)
        key = backup_key(day, collection.file_name)
        blobs.put(key, body.encode(), "application/x-ndjson")
        counts[collection.name] = len(items)
        log.info(
            "backup collection written",
            extra={"stage": "backup", "key": key, "count": len(items)},
        )
    manifest = BackupManifest(day=day, run_id=run_id, written_at=clock(), counts=counts)
    key = backup_key(day, MANIFEST_NAME)
    blobs.put(key, manifest.model_dump_json(indent=2).encode(), "application/json")
    log.info(
        "backup written",
        extra={
            "stage": "backup",
            "key": key,
            "run_id": run_id,
            "counts": counts,
            "duration_ms": round((time.perf_counter() - began) * 1000),
        },
    )
    return manifest


def _read_manifest(blobs: BlobStore, day: date) -> BackupManifest:
    key = backup_key(day, MANIFEST_NAME)
    ref = blobs.ref_for(key)
    if not blobs.exists(ref):
        raise BackupError(f"no complete backup for {day.isoformat()}: {key} is missing")
    try:
        return BackupManifest.model_validate_json(blobs.get(ref))
    except (ValidationError, BlobNotFoundError) as exc:
        raise BackupError(f"{key} is not a valid manifest") from exc


def _read_collection(blobs: BlobStore, day: date, collection: _Collection) -> list[BaseModel]:
    key = backup_key(day, collection.file_name)
    items: list[BaseModel] = []
    try:
        lines = blobs.get(blobs.ref_for(key)).decode().splitlines()
    except BlobNotFoundError:
        raise BackupError(f"{key} is missing, though the manifest lists it") from None
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            items.append(collection.model.model_validate_json(line))
        except ValidationError:
            # Only the line number, and no chained cause: the error text quotes the event.
            raise BackupError(
                f"{key} line {number} is not a valid {collection.name} item"
            ) from None
    return items


def restore_backup(repository: Repository, blobs: BlobStore, *, day: date, dry_run: bool) -> int:
    """Save every item of ``day``'s backup into ``repository``; return how many.

    Every file is read and checked against the manifest's counts before
    anything is written, so a damaged backup writes nothing. Items are saved
    with ``save_feedback``, which is keyed by id, so a second restore changes
    nothing. Feedback events are never rewritten once stored (ADR-0010), so
    saving a backed-up copy over an event already in the store writes the same
    fields back. Nothing is ever deleted. With ``dry_run`` nothing is written
    and the count is what would be saved.

    Raises ``BackupError`` if the backup is missing, incomplete or unreadable.
    """
    began = time.perf_counter()
    manifest = _read_manifest(blobs, day)
    loaded: list[tuple[_Collection, list[BaseModel]]] = []
    for collection in _COLLECTIONS:
        if collection.name not in manifest.counts:
            log.info(
                "restore skips a collection the backup lacks",
                extra={"stage": "backup", "day": day.isoformat(), "collection": collection.name},
            )
            continue  # a collection added after this backup was made
        items = _read_collection(blobs, day, collection)
        if len(items) != manifest.counts[collection.name]:
            raise BackupError(
                f"{backup_key(day, collection.file_name)} holds {len(items)} item(s) "
                f"but the manifest says {manifest.counts[collection.name]}"
            )
        loaded.append((collection, items))
    total = sum(len(items) for _, items in loaded)
    if not dry_run:
        for collection, items in loaded:
            for item in items:
                collection.save(repository, item)
    log.info(
        "backup restored" if not dry_run else "backup restore dry run",
        extra={
            "stage": "backup",
            "day": day.isoformat(),
            "dry_run": dry_run,
            "counts": {c.name: len(items) for c, items in loaded},
            "duration_ms": round((time.perf_counter() - began) * 1000),
        },
    )
    return total
