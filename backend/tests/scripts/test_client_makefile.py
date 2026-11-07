"""The client's Docker images are pinned by tag and digest in the Makefile."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
PINNED = re.compile(r"^[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}$")


def variable(name: str) -> str:
    match = re.search(rf"^{name} := (\S+)$", MAKEFILE, re.MULTILINE)
    assert match, f"{name} is not set in the Makefile"
    return match.group(1)


def test_client_images_are_pinned_by_tag_and_digest() -> None:
    for name in ("CLIENT_NODE_IMAGE", "CLIENT_PLAYWRIGHT_IMAGE"):
        assert PINNED.match(variable(name)), name


def test_playwright_image_matches_the_npm_package_version() -> None:
    package = (ROOT / "client" / "package.json").read_text(encoding="utf-8")
    match = re.search(r'"@playwright/test": "(\d+\.\d+\.\d+)"', package)
    assert match, "@playwright/test must be pinned to an exact version"
    assert f":v{match.group(1)}-" in variable("CLIENT_PLAYWRIGHT_IMAGE")
