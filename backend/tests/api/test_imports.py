"""Import boundaries for the API (#175, CLAUDE.md rule 1)."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).parents[2] / "src" / "newsfeed"
_HTTP_CLIENTS = (
    "httpx",
    "requests",
    "urllib.request",
    "urllib3",
    "http.client",
    "aiohttp",
    "socket",
)


def test_importing_the_api_app_loads_no_extractor_library() -> None:
    # A subprocess: other tests in this process may already have imported them.
    code = (
        "import sys, newsfeed.api.app; "
        "bad = sorted(m for m in sys.modules "
        "if m.split('.')[0] in {'trafilatura', 'newspaper', 'playwright'}); "
        "print(bad); sys.exit(1 if bad else 0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], check=False, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_no_google_import_outside_adapters() -> None:
    offenders = [
        f"{path.relative_to(SRC)}: {name}"
        for path in SRC.rglob("*.py")
        if "adapters" not in path.relative_to(SRC).parts
        for name in _imports(path)
        if name == "google" or name.startswith("google.")
    ]
    assert offenders == []


def _is_http_client(name: str, *, allow_requests: bool) -> bool:
    if allow_requests and (name == "requests" or name.startswith("requests.")):
        return False
    return any(name == client or name.startswith(f"{client}.") for client in _HTTP_CLIENTS)


def test_the_api_and_the_iap_adapter_use_no_http_client_of_their_own() -> None:
    assert (SRC / "api" / "app.py").is_file()
    # The adapter may import ``requests`` only to hand google-auth its transport session.
    files = [(path, False) for path in sorted((SRC / "api").rglob("*.py"))]
    files.append((SRC / "adapters" / "iap_auth.py", True))
    offenders = [
        f"{path.relative_to(SRC)}: {name}"
        for path, allow_requests in files
        for name in _imports(path)
        if _is_http_client(name, allow_requests=allow_requests)
    ]
    assert offenders == []
