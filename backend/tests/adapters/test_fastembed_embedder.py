"""Tests for the fastembed adapter. Only the live test loads the real model."""

from __future__ import annotations

import math
import os
import time
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
