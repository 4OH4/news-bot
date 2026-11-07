"""``newsfeed serve`` must not expose a local ``.env`` (#230).

Two gaps combined: an empty ``NEWSFEED_CLIENT_DIR`` served the working directory
(which holds ``.env``), and in local mode (``NEWSFEED_API_AUTH=none``) any ``Host``
header was accepted, so a DNS-rebinding page could read what the server serves.
These tests drive the real ``serve`` command with ``uvicorn.run`` replaced, then
send requests to the app it would have served. Every secret here is invented.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from click.testing import Result
from typer.testing import CliRunner

from newsfeed import cli
from newsfeed.settings import Settings

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parents[3]

_SERVE_ENV = (
    "NEWSFEED_API_AUTH",
    "NEWSFEED_OWNER_EMAIL",
    "NEWSFEED_IAP_AUDIENCE",
    "NEWSFEED_CLIENT_DIR",
    "NEWSFEED_STORAGE",
    "NEWSFEED_GCS_BUCKET",
    "PORT",
)
_OWNER = "owner@example.com"
_AUDIENCE = "/projects/123456789/locations/europe-west2/services/reader-api"

FEED_MARKER = "exposure-feed-marker-3d71"
INDEX_MARKER = "exposure-index-marker-a92c"
ENV_SECRET = "ENV-SECRET-MARKER-6f0e"
CWD_INDEX_MARKER = "cwd-index-marker-b415"
_FEED = f'{{"schema_version": 1, "items": [], "note": "{FEED_MARKER}"}}'.encode()

_MARKERS = (FEED_MARKER, INDEX_MARKER, ENV_SECRET, CWD_INDEX_MARKER)


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A working directory like the repo root: a ``.env`` holding a secret, data and a client.

    The working directory also has an ``index.html``, so serving it by mistake
    answers the client route with something recognisable.
    """
    for name in _SERVE_ENV:
        monkeypatch.delenv(name, raising=False)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    # Settings read ``.env`` too, so it holds only a harmless unrelated variable.
    (work / ".env").write_text(f"NEWSFEED_UNRELATED_SECRET={ENV_SECRET}\n")
    (work / "index.html").write_text(f"<!doctype html>{CWD_INDEX_MARKER}")
    data = work / "data"
    (data / "blobs").mkdir(parents=True)
    (data / "blobs" / "feed.json").write_bytes(_FEED)
    monkeypatch.setenv("NEWSFEED_DATA_DIR", str(data))
    monkeypatch.setenv("NEWSFEED_CONFIG_DIR", str(work / "config"))
    monkeypatch.setenv("NEWSFEED_OWNER_EMAIL", _OWNER)
    return work


@pytest.fixture
def built_client(tmp_path: Path) -> Path:
    """A built client outside the working directory, with no ``.env`` in it."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(f"<!doctype html>{INDEX_MARKER}")
    return dist


@pytest.fixture
def uvicorn_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Replace ``uvicorn.run`` so nothing binds a socket; record its arguments."""
    import uvicorn

    real = uvicorn.run
    calls: list[dict[str, object]] = []

    def fake_run(*args: object, **kwargs: object) -> None:
        bound = inspect.signature(real).bind(*args, **kwargs)
        bound.apply_defaults()
        calls.append(dict(bound.arguments))

    monkeypatch.setattr(uvicorn, "run", fake_run)
    return calls


def _served(calls: list[dict[str, object]]) -> TestClient:
    """A client for the app ``serve`` handed to uvicorn, addressed as ``localhost``."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    assert len(calls) == 1, calls
    app = calls[0]["app"]
    assert isinstance(app, FastAPI), f"serve should pass the app object, got {app!r}"
    return TestClient(app, base_url="http://localhost:8080", raise_server_exceptions=False)


def _assert_no_content(body: bytes) -> None:
    text = body.decode("utf-8", errors="replace")
    for marker in _MARKERS:
        assert marker not in text, f"response leaked {marker}"


def _serve_local(monkeypatch: pytest.MonkeyPatch, client_dir: str | None) -> Result:
    monkeypatch.setenv("NEWSFEED_API_AUTH", "none")
    if client_dir is not None:
        monkeypatch.setenv("NEWSFEED_CLIENT_DIR", client_dir)
    return runner.invoke(cli.app, ["serve"])


# --- An empty NEWSFEED_CLIENT_DIR means unset ---------------------------------


@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_client_dir_setting_means_the_default(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    default = Settings(_env_file=None).client_dir  # type: ignore[call-arg]
    monkeypatch.setenv("NEWSFEED_CLIENT_DIR", value)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.client_dir == default
    assert settings.client_dir != Path(".")


def test_serve_with_an_empty_client_dir_does_not_serve_the_working_directory(
    workdir: Path, uvicorn_calls: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _serve_local(monkeypatch, "")

    assert result.exit_code == 0, result.output
    client = _served(uvicorn_calls)
    for path in ("/.env", "/", "/index.html", "/reconnect"):
        response = client.get(path)
        assert ENV_SECRET not in response.text, path
        assert CWD_INDEX_MARKER not in response.text, path


# --- A client dir that could hold secrets is a startup error ------------------


def _assert_refused(result: Result, calls: list[dict[str, object]]) -> None:
    output = result.output
    assert result.exit_code != 0, output
    assert calls == [], "serve started although the client dir was refused"
    assert "NEWSFEED_CLIENT_DIR" in output, output
    assert ENV_SECRET not in output  # never echo what .env holds


def test_serve_refuses_dot_as_client_dir_from_a_root_with_a_dot_env(
    workdir: Path, uvicorn_calls: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _serve_local(monkeypatch, ".")

    _assert_refused(result, uvicorn_calls)


def test_serve_refuses_the_working_directory_even_without_a_dot_env(
    workdir: Path, uvicorn_calls: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    (workdir / ".env").unlink()

    result = _serve_local(monkeypatch, str(workdir))

    _assert_refused(result, uvicorn_calls)


def test_serve_refuses_a_client_dir_that_resolves_to_the_working_directory(
    workdir: Path,
    uvicorn_calls: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (workdir / ".env").unlink()
    link = tmp_path / "linked-client"
    link.symlink_to(workdir, target_is_directory=True)

    result = _serve_local(monkeypatch, str(link))

    _assert_refused(result, uvicorn_calls)


def test_serve_refuses_a_client_dir_containing_a_dot_env(
    workdir: Path,
    built_client: Path,
    uvicorn_calls: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (built_client / ".env").write_text(f"SOMETHING={ENV_SECRET}\n")

    result = _serve_local(monkeypatch, str(built_client))

    _assert_refused(result, uvicorn_calls)


def test_serve_refuses_the_repo_root_as_client_dir(
    workdir: Path, uvicorn_calls: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The checkout this code runs from, whether or not it has a .env.
    result = _serve_local(monkeypatch, str(REPO_ROOT))

    _assert_refused(result, uvicorn_calls)


def test_serve_refuses_an_unsafe_client_dir_in_iap_mode_too(
    workdir: Path, uvicorn_calls: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEWSFEED_API_AUTH", "iap")
    monkeypatch.setenv("NEWSFEED_IAP_AUDIENCE", _AUDIENCE)
    monkeypatch.setenv("NEWSFEED_CLIENT_DIR", ".")

    result = runner.invoke(cli.app, ["serve"])

    _assert_refused(result, uvicorn_calls)


def test_serve_still_accepts_a_built_client_dir(
    workdir: Path,
    built_client: Path,
    uvicorn_calls: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _serve_local(monkeypatch, str(built_client))

    assert result.exit_code == 0, result.output
    assert INDEX_MARKER in _served(uvicorn_calls).get("/reconnect").text


# --- Local mode accepts only loopback Host headers ----------------------------


@pytest.mark.parametrize(
    "host",
    [
        "evil.example:8000",
        "evil.example",
        "localhost.evil.example:8000",
        "127.0.0.1.evil.example",
        "attacker.localhost.example:8080",
        "192.168.1.20:8080",
        "0.0.0.0:8080",
    ],
)
@pytest.mark.parametrize("path", ["/api/feed", "/", "/reconnect", "/.env"])
def test_local_mode_refuses_a_foreign_host_header_with_400(
    workdir: Path,
    built_client: Path,
    uvicorn_calls: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
    host: str,
    path: str,
) -> None:
    result = _serve_local(monkeypatch, str(built_client))
    assert result.exit_code == 0, result.output

    response = _served(uvicorn_calls).get(path, headers={"Host": host})

    assert response.status_code == 400
    _assert_no_content(response.content)


@pytest.mark.parametrize(
    "host", ["localhost:8000", "127.0.0.1:8000", "[::1]:8000", "localhost", "127.0.0.1", "[::1]"]
)
def test_local_mode_serves_loopback_host_headers_as_before(
    workdir: Path,
    built_client: Path,
    uvicorn_calls: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
    host: str,
) -> None:
    result = _serve_local(monkeypatch, str(built_client))
    assert result.exit_code == 0, result.output
    client = _served(uvicorn_calls)

    feed = client.get("/api/feed", headers={"Host": host})
    page = client.get("/reconnect", headers={"Host": host})

    assert feed.status_code == 200
    assert feed.content == _FEED
    assert page.status_code == 200
    assert INDEX_MARKER in page.text


def test_iap_mode_does_not_check_the_host_header(
    workdir: Path,
    built_client: Path,
    uvicorn_calls: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # IAP fronts the service under its own host name; the owner check still applies.
    monkeypatch.setenv("NEWSFEED_API_AUTH", "iap")
    monkeypatch.setenv("NEWSFEED_IAP_AUDIENCE", _AUDIENCE)
    monkeypatch.setenv("NEWSFEED_CLIENT_DIR", str(built_client))

    result = runner.invoke(cli.app, ["serve"])

    assert result.exit_code == 0, result.output
    response = _served(uvicorn_calls).get(
        "/api/feed", headers={"Host": "reader-api.example.run.app"}
    )
    assert response.status_code == 401  # the JWT check, not a host refusal
    _assert_no_content(response.content)
