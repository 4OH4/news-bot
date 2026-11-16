"""Tests for the fastembed adapter. Only the live test loads the real model."""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
import math
import os
import sys
import time
import types
from pathlib import Path

import pytest

from newsfeed.adapters.fastembed_embedder import DEFAULT_MODEL, FastembedEmbedder
from newsfeed.ports import Embedder, EmbeddingError
from newsfeed.settings import Settings
from newsfeed.wiring import build_embedder


def test_it_is_an_embedder_named_after_its_model(tmp_path: Path) -> None:
    embedder = FastembedEmbedder(tmp_path)

    assert isinstance(embedder, Embedder)
    assert embedder.model == DEFAULT_MODEL
    assert embedder.dimensions == 384


def test_a_missing_folder_is_a_clear_error_and_is_not_created(tmp_path: Path) -> None:
    folder = tmp_path / "models"

    with pytest.raises(EmbeddingError, match="embed download"):
        FastembedEmbedder(folder).embed(["text"])

    assert not folder.exists()


def test_an_empty_folder_fails_without_downloading(tmp_path: Path) -> None:
    # Sockets are disabled in tests, so a download attempt would fail differently;
    # this checks that the model is only looked for locally.
    with pytest.raises(EmbeddingError, match="couldn't load"):
        FastembedEmbedder(tmp_path).embed(["text"])

    assert list(tmp_path.glob("**/*.onnx")) == []


def test_no_texts_need_no_model(tmp_path: Path) -> None:
    assert FastembedEmbedder(tmp_path / "absent").embed([]) == []


class _ImportSpy(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Serves stand-ins for fastembed and onnxruntime, noting the telemetry switch at import."""

    NAMES = ("fastembed", "onnxruntime")

    def __init__(self) -> None:
        self.switch_at_import: dict[str, str | None] = {}

    def find_spec(
        self, fullname: str, path: object = None, target: object = None
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname not in self.NAMES:
            return None
        self.switch_at_import.setdefault(fullname, os.environ.get("ORT_DISABLE_TELEMETRY"))
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> None:
        return None

    def exec_module(self, module: types.ModuleType) -> None:
        class TextEmbedding:
            @staticmethod
            def get_embedding_size(model_name: str) -> int:
                return 384

        module.TextEmbedding = TextEmbedding  # type: ignore[attr-defined]
        module.disable_telemetry_events = lambda: None  # type: ignore[attr-defined]


def test_dimensions_called_first_turns_telemetry_off_before_onnxruntime_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ORT_DISABLE_TELEMETRY", raising=False)
    for name in list(sys.modules):
        if name.split(".")[0] in _ImportSpy.NAMES:
            monkeypatch.delitem(sys.modules, name)
    spy = _ImportSpy()
    monkeypatch.setattr(sys, "meta_path", [spy, *sys.meta_path])

    assert FastembedEmbedder(tmp_path).dimensions == 384

    assert spy.switch_at_import  # the embedder did import one of them
    assert set(spy.switch_at_import.values()) == {"1"}


def test_wiring_builds_one_only_when_the_folder_is_set(tmp_path: Path) -> None:
    assert build_embedder(Settings(_env_file=None)) is None  # type: ignore[call-arg]

    built = build_embedder(Settings(_env_file=None, embed_model_dir=tmp_path))  # type: ignore[call-arg]

    assert isinstance(built, FastembedEmbedder)
    assert built.model == DEFAULT_MODEL


@pytest.mark.live
def test_the_real_model_gives_unit_vectors_that_group_by_meaning() -> None:
    folder = os.environ.get("NEWSFEED_EMBED_MODEL_DIR")
    if not folder:
        pytest.skip("NEWSFEED_EMBED_MODEL_DIR is not set")
    embedder = FastembedEmbedder(Path(folder))

    a, b, c = embedder.embed(
        [
            "The council approved a new bridge over the river.",
            "A new river bridge was approved by the town council.",
            "The striker scored twice in the cup final.",
        ]
    )

    for vector in (a, b, c):
        assert len(vector) == embedder.dimensions
        assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, abs_tol=1e-4)

    def cosine(x: list[float], y: list[float]) -> float:
        return sum(p * q for p, q in zip(x, y, strict=True))

    assert cosine(a, b) > cosine(a, c)


@pytest.mark.live
def test_the_real_model_cpu_time_for_50_articles(capsys: pytest.CaptureFixture[str]) -> None:
    """Prints the CPU seconds to embed 50 synthetic ~2,000-character articles."""
    folder = os.environ.get("NEWSFEED_EMBED_MODEL_DIR")
    if not folder:
        pytest.skip("NEWSFEED_EMBED_MODEL_DIR is not set")
    embedder = FastembedEmbedder(Path(folder))
    embedder.embed(["warm up"])  # loading the model is once per run, timed apart
    words = [
        "harbour",
        "council",
        "budget",
        "river",
        "transit",
        "school",
        "market",
        "energy",
        "storm",
        "league",
    ]
    texts = [" ".join(words[(i + j) % len(words)] for j in range(330))[:2000] for i in range(50)]

    cpu, wall = time.process_time(), time.perf_counter()
    vectors = embedder.embed(texts)
    cpu, wall = time.process_time() - cpu, time.perf_counter() - wall

    assert len(vectors) == 50
    with capsys.disabled():
        print(f"\n50 articles: {cpu:.2f} s CPU, {wall:.2f} s wall")
