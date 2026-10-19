"""Build the Google-backed adapters from plain values (application default credentials).

Kept apart from the adapters so ``wiring`` can import this lazily and still obey
CLAUDE.md rule 1: no ``google.cloud`` import outside ``adapters/``. On Cloud Run
the credentials are the job's service account; locally they come from
``gcloud auth application-default login``. No key files are read.
"""

from __future__ import annotations

from google.cloud import firestore, storage

from newsfeed.adapters.firestore_repository import FirestoreRepository
from newsfeed.adapters.gcs_blobstore import GcsBlobStore

__all__ = ["open_firestore_repository", "open_gcs_blobstore"]


def open_firestore_repository(
    *, project: str | None, database: str | None, prefix: str = ""
) -> FirestoreRepository:
    """A ``FirestoreRepository`` on ``database`` (default database when None)."""
    client = firestore.Client(project=project, database=database)
    return FirestoreRepository(client, prefix=prefix)


def open_gcs_blobstore(*, project: str | None, bucket: str, prefix: str = "") -> GcsBlobStore:
    """A ``GcsBlobStore`` on ``bucket``."""
    return GcsBlobStore(storage.Client(project=project), bucket, prefix=prefix)
