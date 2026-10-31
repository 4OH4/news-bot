"""Cloud Storage implementation of the ``BlobStore`` port, for production.

Each blob is one object in a single bucket. An optional ``prefix`` is put in
front of every object name, so one bucket can hold several environments, e.g.
``prefix="prod"`` stores key ``articles/<id>.html`` as ``prod/articles/<id>.html``.

The prefix is an implementation detail of the store: the ref returned by
``put`` is the *key*, without the prefix, exactly as the local filesystem
adapter does. ``Article.content_ref`` values therefore stay valid when the
prefix or the backend changes, and ``get``/``exists`` re-apply the prefix. Keys
used by the pipeline are ``articles/<id>.html`` (which the bucket's lifecycle
rule targets, ADR-0005), ``feed.json`` and ``feeds/<run_id>.json`` (ADR-0006);
all sit under the prefix.

Object writes in Cloud Storage are atomic: a reader sees the old object or the
new one in full, never a partial upload, which is what the port requires. The
client is safe to share between threads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from google.api_core.exceptions import NotFound

from newsfeed.ports import BlobNotFoundError, validate_blob_key

if TYPE_CHECKING:
    import google.cloud.storage as storage

    from newsfeed.ports import BlobStore

__all__ = ["GcsBlobStore"]

# Types set from the key's extension, so objects served straight from the bucket
# (feed.json) get the right header whatever the caller passed.
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".json": "application/json",
}

# feed.json is overwritten at a fixed key every run, and Cloud Storage would let
# caches keep a public object for up to an hour; a bucket has no default
# Cache-Control, so it is set per object here. JSON keys only: the feed copies
# are small and rarely read, and article HTML is written once.
_CACHE_CONTROL = {
    ".json": "no-cache",
}


class GcsBlobStore:
    """A ``BlobStore`` backed by one Cloud Storage bucket.

    Keys are checked with ``validate_blob_key``. ``get`` and ``exists`` apply
    the same check to refs and treat an invalid ref as missing.

    Args:
        client: a ``google.cloud.storage.Client``; the caller owns credentials
            and, for the emulator, ``STORAGE_EMULATOR_HOST``.
        bucket: bucket name. It must already exist.
        prefix: optional object-name prefix (``/``-separated, same character
            rules as a key). Empty means the bucket root.
    """

    def __init__(self, client: storage.Client, bucket: str, *, prefix: str = "") -> None:
        self._bucket = client.bucket(bucket)
        prefix = prefix.strip("/")
        self._prefix = f"{validate_blob_key(prefix)}/" if prefix else ""

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Upload ``data`` as the object for ``key`` and return the ref ``key``."""
        validate_blob_key(key)
        blob = self._bucket.blob(self._name(key))
        cache_control = _cache_control(key)
        # Only set it when there is a value, so other uploads don't send
        # `cacheControl: null` in their metadata.
        if cache_control is not None:
            blob.cache_control = cache_control
        blob.upload_from_string(data, content_type=_content_type(key, content_type))
        return key

    def get(self, ref: str) -> bytes:
        """Return the blob for ``ref``. Raises ``BlobNotFoundError`` if missing."""
        if not _valid(ref):
            raise BlobNotFoundError(ref)
        try:
            data: bytes = self._bucket.blob(self._name(ref)).download_as_bytes()
        except NotFound:
            raise BlobNotFoundError(ref) from None
        return data

    def exists(self, ref: str) -> bool:
        """Return True if a blob exists for ``ref``."""
        return _valid(ref) and bool(self._bucket.blob(self._name(ref)).exists())

    def _name(self, key: str) -> str:
        """Return the object name for ``key``: the prefix, then the key."""
        return f"{self._prefix}{key}"


def _valid(ref: str) -> bool:
    try:
        validate_blob_key(ref)
    except ValueError:
        return False
    return True


def _content_type(key: str, given: str) -> str:
    """Prefer the type implied by the key's extension, else the caller's."""
    for suffix, content_type in _CONTENT_TYPES.items():
        if key.endswith(suffix):
            return content_type
    return given


def _cache_control(key: str) -> str | None:
    """The Cache-Control header for ``key``, or None for Cloud Storage's default."""
    for suffix, cache_control in _CACHE_CONTROL.items():
        if key.endswith(suffix):
            return cache_control
    return None


# --- Static conformance check ------------------------------------------------

if TYPE_CHECKING:

    def _check(client: storage.Client) -> BlobStore:
        return GcsBlobStore(client, "bucket")
