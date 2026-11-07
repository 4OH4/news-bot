"""Unit tests for ``check_client_dir`` beyond the serve-level tests (#230)."""

from __future__ import annotations

from pathlib import Path

import pytest

from newsfeed.api.local_safety import UnsafeClientDirError, check_client_dir


def test_a_directory_above_the_working_directory_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    with pytest.raises(UnsafeClientDirError, match="working directory"):
        check_client_dir(Path(".."))


def test_a_clean_sibling_directory_is_accepted_and_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "work").mkdir()
    (tmp_path / "dist").mkdir()
    monkeypatch.chdir(tmp_path / "work")

    assert check_client_dir(Path("../dist")) == (tmp_path / "dist").resolve()


def test_the_error_names_the_directory_not_the_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / ".env").write_text("TOKEN=invented-secret-11aa\n")

    with pytest.raises(UnsafeClientDirError) as caught:
        check_client_dir(dist)

    assert str(dist) in str(caught.value)
    assert "invented-secret-11aa" not in str(caught.value)
