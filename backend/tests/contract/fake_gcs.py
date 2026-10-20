"""A minimal in-process stand-in for ``google.cloud.storage.Client``.

Implements only the calls ``GcsBlobStore`` makes (``bucket``, ``blob``,
``cache_control``, ``upload_from_string``, ``download_as_bytes``, ``exists``) over
shared dicts, so the adapter's key, prefix and error handling run in the default
test suite without Docker or network. Real-service behaviour is covered by the emulator run.
"""

from __future__ import annotations

import threading

from google.api_core.exceptions import NotFound


class FakeGcsState:
    """Objects of one fake project: ``(bucket, name) -> (data, content_type)``."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, str]] = {}
        # Cache-Control of each object whose uploader assigned one. A key is
        # absent when it was never assigned (the real client then sends no
        # cacheControl field); an explicit None means `cacheControl: null` was sent.
        self.cache_control: dict[tuple[str, str], str | None] = {}
        self.lock = threading.Lock()


class FakeBlob:
    """Stands in for ``storage.Blob``."""

    def __init__(self, state: FakeGcsState, bucket: str, name: str) -> None:
        self._state = state
        self._key = (bucket, name)
        self._cache_control: str | None = None
        self._cache_control_set = False

    @property
    def cache_control(self) -> str | None:
        return self._cache_control

    @cache_control.setter
    def cache_control(self, value: str | None) -> None:
        # Like the real Blob, any assignment (even None) becomes upload metadata.
        self._cache_control = value
        self._cache_control_set = True

    def upload_from_string(self, data: bytes, content_type: str = "text/plain") -> None:
        with self._state.lock:
            self._state.objects[self._key] = (bytes(data), content_type)
            if self._cache_control_set:
                self._state.cache_control[self._key] = self._cache_control

    def download_as_bytes(self) -> bytes:
        with self._state.lock:
            try:
                return self._state.objects[self._key][0]
            except KeyError:
                raise NotFound(f"No such object: {self._key[1]}") from None

    def exists(self) -> bool:
        with self._state.lock:
            return self._key in self._state.objects


class FakeBucket:
    """Stands in for ``storage.Bucket``."""

    def __init__(self, state: FakeGcsState, name: str) -> None:
        self._state = state
        self.name = name

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self._state, self.name, name)


class FakeGcsClient:
    """Stands in for ``storage.Client``."""

    def __init__(self) -> None:
        self.state = FakeGcsState()

    def bucket(self, name: str) -> FakeBucket:
        return FakeBucket(self.state, name)
