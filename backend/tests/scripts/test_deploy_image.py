"""The client is built into the backend image, and a deploy updates the service (#178).

These read the Dockerfile, `.dockerignore`, `deploy.yml` and the Makefile, because
the workflow itself can only run on `main`.
"""

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
DOCKERIGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8")
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
DEPLOY = yaml.safe_load((ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8"))


def stages(dockerfile: str) -> dict[str, list[str]]:
    """Map each named stage to its instruction lines, comments and blanks dropped."""
    result: dict[str, list[str]] = {}
    current: list[str] = []
    for raw in dockerfile.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"FROM\s+\S+\s+AS\s+(\S+)$", line, re.IGNORECASE)
        if match:
            current = result.setdefault(match.group(1), [])
        current.append(line)
    return result


def deploy_steps() -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = DEPLOY["jobs"]["deploy"]["steps"]
    return steps


def step_index(steps: list[dict[str, Any]], command: str) -> int:
    matches = [i for i, step in enumerate(steps) if command in step.get("run", "")]
    assert len(matches) == 1, f"expected one step running {command!r}"
    return matches[0]


def test_client_builder_uses_the_makefile_node_image() -> None:
    match = re.search(r"^CLIENT_NODE_IMAGE := (\S+)$", MAKEFILE, re.MULTILINE)
    assert match
    makefile_image = match.group(1)
    assert "@sha256:" in makefile_image
    from_line = stages(DOCKERFILE)["client-builder"][0]
    assert from_line.split()[1] == makefile_image


def test_client_builder_copies_only_client_paths() -> None:
    copies = [line for line in stages(DOCKERFILE)["client-builder"] if line.startswith("COPY")]
    assert copies
    for line in copies:
        assert "--from" not in line, line
        sources = line.split()[1:-1]
        assert sources, line
        for source in sources:
            assert source.startswith("client/"), line
            assert ".." not in source, line


def test_client_builder_install_ignores_scripts() -> None:
    builder = stages(DOCKERFILE)["client-builder"]
    installs = [line for line in builder if re.search(r"\bnpm (ci|install)\b", line)]
    assert installs
    for line in installs:
        assert "--ignore-scripts" in line, line


def test_runtime_has_the_built_client() -> None:
    runtime = stages(DOCKERFILE)["runtime"]
    assert any(re.match(r"COPY --from=client-builder \S+ /app/client$", line) for line in runtime)
    assert "ENV NEWSFEED_CLIENT_DIR=/app/client" in runtime
    # The runtime stage is the default target, so it must stay last.
    assert list(stages(DOCKERFILE))[-1] == "runtime"


def test_dockerignore_is_an_allowlist_that_keeps_out_build_output_and_credentials() -> None:
    stripped = (line.strip() for line in DOCKERIGNORE.splitlines())
    lines = [line for line in stripped if line and not line.startswith("#")]
    assert lines[0] == "*"
    allowed = [line for line in lines if line.startswith("!")]
    assert "!client/" in allowed
    excluded = {line.rstrip("/") for line in lines[lines.index("!client/") + 1 :]}
    assert {"client/node_modules", "client/dist", "**/gha-creds-*.json"} <= excluded
    # Nothing re-includes a credentials file or build output after the exclusions.
    for line in allowed:
        assert not any(word in line for word in ("gha-creds", "node_modules", "dist")), line


def test_service_update_follows_the_job_update_and_sets_only_the_image() -> None:
    steps = deploy_steps()
    job = step_index(steps, "gcloud run jobs update")
    service = step_index(steps, "gcloud run services update")
    assert job < service
    command = steps[service]["run"]
    flags = set(re.findall(r"--[\w-]+", command))
    assert flags == {"--image", "--region", "--project", "--quiet"}
    assert '--image="$IMAGE"' in command
    assert steps[service]["if"] == "vars.GCP_API_SERVICE != ''"


def test_no_step_deploys_from_source_or_uses_a_cache() -> None:
    for step in deploy_steps():
        assert "--source" not in step.get("run", "")
        assert "cache" not in str(step.get("with", {})), step
        assert not str(step.get("uses", "")).startswith("actions/cache")


def test_deploy_actions_are_pinned_to_commit_shas() -> None:
    for step in deploy_steps():
        if "uses" in step:
            assert re.search(r"@[0-9a-f]{40}$", step["uses"]), step["uses"]


def test_alert_title_covers_the_job_and_the_service() -> None:
    alert = DEPLOY["jobs"]["alert"]
    title = alert["env"]["TITLE"]
    assert "Job" in title and "service" in title
    # The exact-title lookup reads the same variable, so the two can't drift.
    assert 'select(.title == \\"${TITLE}\\")' in alert["steps"][0]["run"]
