"""Daily backups of the owner's feedback and preferences, and restoring them (#330, #334).

Feedback and reader preferences are the things in the store that can't be
rebuilt (CLAUDE.md, "Cost of failure"), and Firestore's point-in-time recovery
is off. A backup is one
JSON-lines file per collection under ``backups/<YYYY-MM-DD>/``, then
``manifest.json`` with the counts. The manifest is written last, so its
presence means every collection file before it was written in full.

Nothing here deletes, from the blob store or the repository. Backups sit
outside both bucket lifecycle rules (``articles/`` and ``feeds/``), so nothing
else deletes them either.

Logs carry counts, keys and durations only, never an event's contents or the
owner's settings: those are the owner's reading history and choices.

Cost: one ``list_feedback`` per backup, which on Firestore is one document read
per stored event, one ``get_preferences`` read, plus one write per collection
file and one for the manifest.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from pydantic import BaseModel, ValidationError

from newsfeed.logging import get_logger
from newsfeed.models import DEFAULT_USER_ID, Feedback, ReaderPreferences
from newsfeed.ports import BlobNotFoundError, BlobStore, Repository

__all__ = [
    "BACKUP_PREFIX",
    "COLLECTIONS",
    "MANIFEST_NAME",
    "BackupError",
    "BackupManifest",
    "PreferencesOutcome",
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
    # Whether a restore should write this backed-up item (given dry_run, force);
    # logs when it doesn't and may append a line to the report.
    should_restore: Callable[
        [Repository, BaseModel, bool, bool, list[PreferencesOutcome]], bool
    ] = lambda _r, _i, _d, _f, _o: True

    @property
    def file_name(self) -> str:
        return f"{self.name}.jsonl"


def _save_feedback(repository: Repository, item: BaseModel) -> None:
    assert isinstance(item, Feedback)
    repository.save_feedback(item)


def _dump_preferences(repository: Repository) -> list[ReaderPreferences]:
    # One user for now (ADR-0010); more users would list them here.
    prefs = repository.get_preferences(DEFAULT_USER_ID)
    return [prefs] if prefs is not None else []


def _save_preferences(repository: Repository, item: BaseModel) -> None:
    assert isinstance(item, ReaderPreferences)
    stored = repository.get_preferences(item.user_id)
    if stored is not None:
        # The version only ever goes up: a device holding an old number must
        # conflict after a restore, and the API's ranked-feed cache is keyed on
        # it (#396).
        item = item.model_copy(update={"version": max(stored.version, item.version) + 1})
    repository.save_preferences(item)


@dataclass(frozen=True)
class PreferencesOutcome:
    """What a restore did, or would do, with one user's preferences record.

    Carries the user id and timestamps only, never the settings themselves.
    """

    user_id: str
    action: str  # "kept", "replaced" or "restored" (nothing was stored)
    stored_updated_at: datetime | None
    backup_updated_at: datetime
    dry_run: bool

    def describe(self) -> str:
        """One output line, e.g. ``preferences for owner: kept stored (...)``."""
        backup = self.backup_updated_at.date().isoformat()
        stored = self.stored_updated_at.date().isoformat() if self.stored_updated_at else "none"
        verb = self.action
        if self.dry_run and verb != "kept":
            verb = f"would {'replace' if verb == 'replaced' else 'restore'}"
        return (
            f"preferences for {self.user_id}: {verb} (stored dated {stored}, backup dated {backup})"
        )


def _preferences_should_restore(
    repository: Repository,
    item: BaseModel,
    dry_run: bool,
    force: bool,
    outcomes: list[PreferencesOutcome],
) -> bool:
    """False when the stored record is as new as the backed-up copy or newer.

    With ``force`` the backup's copy always wins.

    Preferences are one record per user that the owner edits, so restoring an
    older backup over live data would lose newer choices (ADR-0016). Feedback
    needs no such rule: an event is never rewritten once stored.
    """
    assert isinstance(item, ReaderPreferences)
    stored = repository.get_preferences(item.user_id)
    stored_at = stored.updated_at if stored is not None else None

    def outcome(action: str) -> None:
        outcomes.append(
            PreferencesOutcome(item.user_id, action, stored_at, item.updated_at, dry_run)
        )

    if stored is None:
        outcome("restored")
        return True
    if force or stored.updated_at < item.updated_at:
        outcome("replaced")
        return True
    outcome("kept")
    # The user id and timestamps only, never the settings themselves.
    log.info(
        "restore kept the stored preferences, as new as the backup or newer",
        extra={
            "stage": "backup",
            "collection": "preferences",
            "user_id": item.user_id,
            "dry_run": dry_run,
            "stored_updated_at": stored.updated_at.isoformat(),
            "backup_updated_at": item.updated_at.isoformat(),
        },
    )
    return False


# The collections a backup holds.
_COLLECTIONS: tuple[_Collection, ...] = (
    _Collection("feedback", Feedback, lambda repo: repo.list_feedback(), _save_feedback),
    _Collection(
        "preferences",
        ReaderPreferences,
        _dump_preferences,
        _save_preferences,
        _preferences_should_restore,
    ),
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


def restore_backup(
    repository: Repository,
    blobs: BlobStore,
    *,
    day: date,
    dry_run: bool,
    force: bool = False,
    outcomes: list[PreferencesOutcome] | None = None,
) -> int:
    """Save every item of ``day``'s backup into ``repository``; return how many.

    Every file is read and checked against the manifest's counts before
    anything is written, so a damaged backup writes nothing. Feedback is saved
    with ``save_feedback``, which is keyed by id, so a second restore changes
    nothing. Feedback events are never rewritten once stored (ADR-0010), so
    saving a backed-up copy over an event already in the store writes the same
    fields back. Preferences are saved only when nothing is stored for that
    user or the stored record's ``updated_at`` is older than the backed-up
    copy's, or ``force`` is set; a kept record is logged and not counted
    (ADR-0016). Each preferences record's fate is appended to ``outcomes`` when
    given. Nothing is ever deleted. With ``dry_run`` nothing is written and the
    count is what would be saved.

    Raises ``BackupError`` if the backup is missing, incomplete or unreadable.
    """
    began = time.perf_counter()
    manifest = _read_manifest(blobs, day)
    loaded: list[tuple[_Collection, list[BaseModel]]] = []
    for unknown in sorted(set(manifest.counts) - set(COLLECTIONS)):
        # Written by a newer version of the code; nothing here can read it back.
        log.warning(
            "restore skips a collection this version doesn't know",
            extra={"stage": "backup", "day": day.isoformat(), "collection": unknown},
        )
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
    sink: list[PreferencesOutcome] = outcomes if outcomes is not None else []
    counts: dict[str, int] = {}
    for collection, items in loaded:
        counts[collection.name] = 0
        for item in items:
            if not collection.should_restore(repository, item, dry_run, force, sink):
                continue
            if not dry_run:
                collection.save(repository, item)
            counts[collection.name] += 1
    total = sum(counts.values())
    log.info(
        "backup restored" if not dry_run else "backup restore dry run",
        extra={
            "stage": "backup",
            "day": day.isoformat(),
            "dry_run": dry_run,
            "counts": counts,
            "duration_ms": round((time.perf_counter() - began) * 1000),
        },
    )
    return total
