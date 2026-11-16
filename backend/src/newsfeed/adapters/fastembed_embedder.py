"""The local embedding model: ``fastembed`` running ``BAAI/bge-small-en-v1.5`` on the CPU.

ADR-0014: embeddings run on the owner's machine and in the job, so no third
party sees what the owner reads and there is no per-call bill. The model is
fetched once into a folder by ``download_model`` (``newsfeed embed download``);
``FastembedEmbedder`` only ever loads from that folder and never downloads.

``fastembed`` (and ``onnxruntime`` under it) is imported on first use, so
commands that never embed don't pay for the import.
"""

from __future__ import annotations

import math
import os
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from newsfeed.logging import get_logger
from newsfeed.ports import EmbeddingError

if TYPE_CHECKING:
    from fastembed import TextEmbedding

__all__ = ["DEFAULT_MODEL", "FastembedEmbedder", "download_model"]

log = get_logger(__name__)

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
"""MIT licence, 384 dimensions (ADR-0014). fastembed serves Qdrant's ONNX conversion."""


def _disable_telemetry() -> None:
    """Switch off library telemetry before ``fastembed`` or ``onnxruntime`` load.

    The design keeps what the owner reads from third parties (ADR-0014). The
    Hugging Face client reports usage by default, and onnxruntime's native
    library (``onnxruntime_pybind11_state``) uploads device details (CPU, OS,
    memory, a device id) to a Microsoft collector about 9 s after the model
    loads, then hourly (#359). ``ORT_DISABLE_TELEMETRY`` is the switch that
    stops it; it is read when onnxruntime is imported, so it is set first.
    Variables the operator set are left alone.
    """
    os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("DO_NOT_TRACK", "1")
    try:
        import onnxruntime  # type: ignore[import-untyped]  # ships no stubs
    except ImportError:  # fastembed's own import reports the real problem
        return
    disable = getattr(onnxruntime, "disable_telemetry_events", None)
    if disable is not None:
        disable()


class FastembedEmbedder:
    """An ``Embedder`` running a fastembed model from ``model_dir``, never downloading it.

    The model loads on the first ``embed`` call. If it isn't in ``model_dir``,
    that call raises ``EmbeddingError`` (and so does each later one, until it is).
    Calls are serialised with a lock: one ONNX session is shared, and the
    tokenizer it uses is not documented as thread-safe.
    """

    def __init__(
        self, model_dir: Path, model_name: str = DEFAULT_MODEL, batch_size: int = 32
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        self._model_dir = Path(model_dir)
        self._model_name = model_name
        self._batch_size = batch_size
        self._lock = threading.Lock()
        self._model: TextEmbedding | None = None
        self._dimensions: int | None = None

    @property
    def model(self) -> str:
        """The model name, stored as ``Article.embedding_model``."""
        return self._model_name

    @property
    def dimensions(self) -> int:
        """Length of every vector, from fastembed's model list (no model load)."""
        if self._dimensions is None:
            from fastembed import TextEmbedding

            try:
                self._dimensions = TextEmbedding.get_embedding_size(self._model_name)
            except ValueError as exc:
                raise EmbeddingError(f"fastembed doesn't know {self._model_name!r}") from exc
        return self._dimensions

    def _load(self) -> TextEmbedding:
        # Called with the lock held.
        if self._model is not None:
            return self._model
        hint = "run `newsfeed embed download` and set NEWSFEED_EMBED_MODEL_DIR to its folder"
        if not self._model_dir.is_dir():
            # Checked first: fastembed would create the folder and then fail less clearly.
            raise EmbeddingError(f"model folder {self._model_dir} doesn't exist; {hint}")
        _disable_telemetry()
        from fastembed import TextEmbedding

        try:
            self._model = TextEmbedding(
                self._model_name, cache_dir=str(self._model_dir), local_files_only=True
            )
        except Exception as exc:
            raise EmbeddingError(
                f"couldn't load {self._model_name} from {self._model_dir} "
                f"({type(exc).__name__}); {hint}"
            ) from exc
        log.info("embedding model loaded", extra={"model": self._model_name})
        return self._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one L2-normalised vector per text, in order. Raises ``EmbeddingError``."""
        if not texts:
            return []
        with self._lock:
            model = self._load()
            try:
                raw = list(model.embed(list(texts), batch_size=self._batch_size))
            except Exception as exc:
                raise EmbeddingError(f"embedding failed ({type(exc).__name__}: {exc})") from exc
        return [_normalise([float(v) for v in vector]) for vector in raw]


def _normalise(vector: list[float]) -> list[float]:
    # bge output is already unit length; this guards a model that isn't. A zero or
    # non-finite vector is returned as is, for the rank stage's check to reject.
    norm = math.sqrt(math.fsum(v * v for v in vector))
    if norm == 0 or not math.isfinite(norm):
        return vector
    return [v / norm for v in vector]


def download_model(model_dir: Path, model_name: str = DEFAULT_MODEL) -> str | None:
    """Fetch ``model_name`` into ``model_dir`` with fastembed's own downloader.

    This is the only code that downloads the model. It is a dependency fetch from
    the model hub, like ``uv sync``, not a fetch of a news site, so it doesn't go
    through the polite ``Fetcher`` (CLAUDE.md rule 6 covers publishers' pages).
    fastembed takes the hub's latest revision and offers no way to pin one, so
    the revision downloaded is returned (the snapshot folder name) for the record,
    or None if it can't be found.
    """
    _disable_telemetry()
    from fastembed import TextEmbedding

    model_dir.mkdir(parents=True, exist_ok=True)
    TextEmbedding(model_name, cache_dir=str(model_dir), lazy_load=True)
    snapshots = sorted(
        model_dir.glob("models--*/snapshots/*"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return snapshots[0].name if snapshots else None
