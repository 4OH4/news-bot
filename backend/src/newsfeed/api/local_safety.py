"""Guards that keep ``newsfeed serve`` from exposing local secrets (#230).

Two checks. A client directory that could hold secrets (the working directory,
the checkout the code runs from, or anything holding a ``.env``) is refused at
startup. And in local mode (``NEWSFEED_API_AUTH=none``) only loopback ``Host``
headers are accepted, so a page using DNS rebinding (its own domain pointed at
127.0.0.1) can't read what the server serves.
"""

from __future__ import annotations

from pathlib import Path

import newsfeed

__all__ = ["LOOPBACK_HOSTS", "UnsafeClientDirError", "check_client_dir"]

# Host names only; the port is ignored. 0.0.0.0 is not loopback: browsers send it
# as a Host header, and a server bound to 127.0.0.1 has no business being named so.
LOOPBACK_HOSTS: tuple[str, ...] = ("localhost", "127.0.0.1", "[::1]")

_PACKAGE_DIR = Path(newsfeed.__file__).resolve().parent


class UnsafeClientDirError(ValueError):
    """The client directory could expose files that aren't the built client."""


def check_client_dir(client_dir: Path) -> Path:
    """Return ``client_dir`` resolved, or raise ``UnsafeClientDirError``.

    Refused: the working directory or any directory above it, a directory that
    holds this code (which covers the repo root of a checkout), and any directory
    with a ``.env`` in it. The message names the directory, never a file's contents.
    """
    root = client_dir.resolve()
    cwd = Path.cwd().resolve()
    if cwd == root or cwd.is_relative_to(root):
        raise UnsafeClientDirError(f"{root} is the working directory or contains it")
    if _PACKAGE_DIR.is_relative_to(root):
        raise UnsafeClientDirError(f"{root} contains the newsfeed code (a repo checkout?)")
    if (root / ".env").exists():
        raise UnsafeClientDirError(f"{root} contains a .env file")
    return root
