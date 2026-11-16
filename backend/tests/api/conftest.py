"""Fixtures for the API tests; the helpers live in ``tests.api.support``."""

import pytest

from tests.api.support import blobs, client_dir

__all__ = ["blobs", "client_dir", "no_views_config"]


@pytest.fixture(autouse=True)
def no_views_config(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point the app at a config directory with no ``views.yaml``.

    ``create_app`` reads the views from ``NEWSFEED_CONFIG_DIR``, which ``make test`` sets
    to the repo's ``config/``. Without this, the paging tests would depend on how the
    repo's views are ranked (#326). Tests of the ranking write their own file.
    """
    monkeypatch.setenv("NEWSFEED_CONFIG_DIR", str(tmp_path_factory.mktemp("no-views")))
