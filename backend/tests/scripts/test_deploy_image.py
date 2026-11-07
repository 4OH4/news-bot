"""The client is built into the backend image, and a deploy updates the service (#178).

These read the Dockerfile, `.dockerignore`, `deploy.yml` and the Makefile, because
the workflow itself can only run on `main`.
"""

import re
import subprocess
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


def expanded_makefile_variable(name: str) -> str:
    """Expand a Makefile variable offline, by dumping make's database without running a rule."""
    result = subprocess.run(
        ["make", "-s", "-p", "-n", "-f", "Makefile"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    match = re.search(rf"^{name} :?= (\S+)$", result.stdout, re.MULTILINE)
    assert match, f"{name} not found in make's database"
    return match.group(1)


def test_client_builder_uses_the_makefile_node_image() -> None:
    # The Makefile reads the image from the Dockerfile rather than repeating it, so a
    # Dependabot bump of the FROM line moves both (#256).
    assert re.search(r"^CLIENT_NODE_IMAGE :?= \$\(shell .*Dockerfile", MAKEFILE, re.MULTILINE)
    from_line = stages(DOCKERFILE)["client-builder"][0]
    image = from_line.split()[1]
    assert re.search(r"^node:\S+@sha256:[0-9a-f]{64}$", image), image
    assert expanded_makefile_variable("CLIENT_NODE_IMAGE") == image


def test_client_builder_has_no_add_and_no_mount() -> None:
    # Either would hand the npm build code (third-party) more than the files it copies.
    for line in stages(DOCKERFILE)["client-builder"]:
        assert not re.match(r"ADD\b", line, re.IGNORECASE), line
        assert "--mount" not in line, line


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


# --- Base images pinned by digest, client env files kept out of the context (#241) ---

DIGEST = re.compile(r"@sha256:[0-9a-f]{64}(?=\s|$)")


def image_references(dockerfile: str) -> list[str]:
    """Every external image a build pulls, and any image named outside a FROM line."""
    stage_names = {name.lower() for name in stages(dockerfile)}
    references: list[str] = []
    for raw in dockerfile.splitlines():
        line = raw.strip()
        from_match = re.match(r"FROM\s+(?:--\S+\s+)*(\S+)", line, re.IGNORECASE)
        if from_match and from_match.group(1).lower() not in stage_names:
            references.append(from_match.group(1))
        copy_match = re.match(r"(?:COPY|RUN)\s+.*?--(?:from|mount=\S*from)=(\S+?)[\s,]", line + " ")
        if copy_match and copy_match.group(1).lower() not in stage_names:
            references.append(copy_match.group(1))
        arg_match = re.match(r"ARG\s+\w*IMAGE=(\S+)", line)
        if arg_match:
            references.append(arg_match.group(1))
    return references


def external_from_images(dockerfile: str) -> list[str]:
    """The image on each FROM line that is not an earlier stage."""
    stage_names = {name.lower() for name in stages(dockerfile)}
    pattern = re.compile(r"^FROM\s+(?:--\S+\s+)*(\S+)", re.MULTILINE | re.IGNORECASE)
    images = [m.group(1) for m in pattern.finditer(dockerfile)]
    return [image for image in images if image.lower() not in stage_names]


def test_every_external_from_has_a_tag_and_a_digest() -> None:
    images = external_from_images(DOCKERFILE)
    assert any(i.startswith("python:") for i in images)
    assert any("astral-sh/uv" in i for i in images)
    assert any(i.startswith("node:") for i in images)
    for image in images:
        assert re.fullmatch(r"\S+?:[\w.-]+@sha256:[0-9a-f]{64}", image), image


def test_no_image_is_referenced_outside_a_from_line() -> None:
    # Dependabot reads only FROM lines; a pin elsewhere would never be refreshed.
    assert image_references(DOCKERFILE) == external_from_images(DOCKERFILE)


def test_python_and_uv_images_are_named_stages() -> None:
    all_stages = stages(DOCKERFILE)
    assert all_stages["python-base"][0].startswith("FROM python:")
    assert all_stages["uv"][0].startswith("FROM ghcr.io/astral-sh/uv:")
    # Both Python stages share one interpreter image: the venv links to it.
    assert all_stages["builder"][0] == "FROM python-base AS builder"
    assert all_stages["base"][0] == "FROM python-base AS base"
    assert "COPY --from=uv /uv /bin/uv" in all_stages["builder"]


def test_dockerfile_syntax_directive_is_pinned_by_digest() -> None:
    first = DOCKERFILE.splitlines()[0]
    assert re.fullmatch(r"# syntax=docker/dockerfile:1@sha256:[0-9a-f]{64}", first), first
    # The tag it was resolved from stays readable.
    assert "docker/dockerfile:1" in first


def dockerignore_excludes(path: str) -> bool:
    """Whether the build context drops `path`: the last matching pattern wins."""
    from fnmatch import fnmatchcase

    excluded = True  # the leading `*` excludes everything not re-included
    for raw in DOCKERIGNORE.splitlines():
        pattern = raw.strip()
        if not pattern or pattern.startswith("#"):
            continue
        negate = pattern.startswith("!")
        pattern = pattern.lstrip("!").rstrip("/")
        directory_hit = path.startswith(pattern + "/")
        if fnmatchcase(path, pattern) or directory_hit:
            excluded = not negate
    return excluded


def test_client_env_files_are_not_sent_to_the_build_context() -> None:
    for name in ("client/.env", "client/.env.local", "client/.env.production.local"):
        assert dockerignore_excludes(name), name
    # The helper must not simply exclude everything: client sources still go in.
    assert not dockerignore_excludes("client/package.json")
    assert not dockerignore_excludes("client/src/main.tsx")


def test_dockerignore_lists_client_env_pattern_after_the_client_allow() -> None:
    lines = [line.strip() for line in DOCKERIGNORE.splitlines()]
    assert "client/.env*" in lines
    assert lines.index("client/.env*") > lines.index("!client/")
    assert not any(line.startswith("!") and ".env" in line for line in lines)
