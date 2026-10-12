"""Local-filesystem implementation of the ``BlobStore`` port, for development and CI.

Blobs are plain files under a base directory: key ``articles/<id>.html`` is
stored at ``<base>/articles/<id>.html``. The ref returned by ``put`` is the path
relative to the base directory (here the same string as the key), never an
absolute path, so ``Article.content_ref`` values stay valid if the data
directory moves and map one-to-one onto object names when migrating to GCS.

Writes go to a temporary file in the destination directory, which is then
renamed over the target with ``os.replace``. The rename is atomic on POSIX, so a
concurrent reader sees either the old blob or the new one in full, never a
partial write.

The ``content_type`` argument is accepted for compatibility with the port but
not stored: the local filesystem has nowhere to keep it, and nothing reads it
back through the port.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from newsfeed.ports import BlobNotFoundError, validate_blob_key

if TYPE_CHECKING:
    from newsfeed.ports import BlobStore

__all__ = ["LocalBlobStore"]

_TEMP_PREFIX = ".tmp-"
_FILE_MODE = 0o644


class LocalBlobStore:
    """A ``BlobStore`` that keeps each blob as a file under ``base_dir``.

    The base directory is created on first write if it doesn't exist. Keys are
    checked with ``validate_blob_key``, which rejects absolute paths and ``..``
    segments, so a blob can never be written outside ``base_dir``. ``get`` and
    ``exists`` apply the same check to refs and treat an invalid ref as missing.

    Args:
        base_dir: directory holding the blobs, e.g. ``<data dir>/blobs``.
    """

    def __init__(self, base_dir: Path | str) -> None:
        self.base_dir = Path(base_dir)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Atomically write ``data`` to ``<base_dir>/<key>`` and return the ref ``key``."""
        validate_blob_key(key)
        target = self.base_dir / key
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(dir=target.parent, prefix=_TEMP_PREFIX)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            # mkstemp creates files readable only by the owner; blobs such as
            # feed.json are served as static files, so make them world-readable.
            temp_path.chmod(_FILE_MODE)
            temp_path.replace(target)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                temp_path.unlink()
            raise
        return key

    def get(self, ref: str) -> bytes:
        """Return the blob for ``ref``. Raises ``BlobNotFoundError`` if missing."""
        path = self._path(ref)
        if path is None:
            raise BlobNotFoundError(ref)
        try:
            return path.read_bytes()
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            raise BlobNotFoundError(ref) from None

    def exists(self, ref: str) -> bool:
        """Return True if a blob exists for ``ref``."""
        path = self._path(ref)
        return path is not None and path.is_file()

    def _path(self, ref: str) -> Path | None:
        """Return the file for ``ref``, or None if ``ref`` isn't a valid key."""
        try:
            validate_blob_key(ref)
        except ValueError:
            return None
        return self.base_dir / ref


# --- Static conformance check ------------------------------------------------

if TYPE_CHECKING:
    _blob_store: BlobStore = LocalBlobStore("blobs")
