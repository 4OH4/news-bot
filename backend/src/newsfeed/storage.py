"""Open the storage adapters chosen by settings.

Kept apart from ``wiring`` so the API can open storage without importing the
extractors and their libraries (#175).
"""

from __future__ import annotations

from newsfeed.adapters.localfs_blobstore import LocalBlobStore
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.ports import BlobStore, Repository
from newsfeed.settings import Settings

__all__ = ["open_storage"]


def open_storage(settings: Settings) -> tuple[Repository, BlobStore]:
    """The repository and blob store for ``settings.storage``.

    The Google adapters are imported only for ``gcp``, so local runs and CI need
    neither the Google libraries' import cost nor any credentials.
    """
    if settings.storage == "gcp":
        from newsfeed.adapters.gcp import open_firestore_repository, open_gcs_blobstore

        assert settings.gcs_bucket  # Settings rejects gcp without a bucket
        return (
            open_firestore_repository(
                project=settings.gcp_project, database=settings.firestore_database
            ),
            open_gcs_blobstore(
                project=settings.gcp_project,
                bucket=settings.gcs_bucket,
                prefix=settings.gcs_prefix,
            ),
        )
    return SqliteRepository(settings.database_path), LocalBlobStore(settings.blob_dir)
