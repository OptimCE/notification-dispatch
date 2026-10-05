"""A change to anything an image is built from must build that image.

---------------------------------------------------------------------------
WHY THIS EXISTS.

`.github/workflows/build-worker.yml` runs only when a changed
file matches its `paths:` filter. Across the annexes those filters were
copied from a service template, and in billing, simulation-key and
document-generation they missed directories the Dockerfiles copy: a fix
confined to one of them built nothing on its pull request, published nothing
on merge, and production went on pulling the previous image with every check
green.

Here every input was listed when this test was added (2026-10-01). This test
keeps it that way when a Dockerfile gains a COPY.
---------------------------------------------------------------------------

The rule pinned here: every `COPY` source in each image's Dockerfile appears in
BOTH trigger lists of the workflow that builds it - as `<dir>/**` for a
directory, verbatim for a file.

PyYAML is not a dependency, so both files are read as text. The readers are
deliberately narrow and refuse what they do not recognise: a workflow they
cannot read must fail this test, not satisfy it with an empty list.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = SERVICE_ROOT / ".github" / "workflows"

# Every image CI publishes: (Dockerfile, the workflow that builds it).
IMAGES = [("Dockerfile.worker", "build-worker.yml")]

TRIGGERS = ["push", "pull_request"]

# `- 'x'`, `- "x"` or `- x`, with an optional trailing comment.
_LIST_ITEM = re.compile(r"""-\s+(?:'([^']+)'|"([^"]+)"|([^\s'"#][^#]*?))\s*(?:#.*)?""")

# `file: Dockerfile.worker` in a docker/build-push-action step.
_BUILD_FILE = re.compile(r"""^\s*file:\s*['"]?([^'"\s#]+)['"]?\s*(?:#.*)?$""", re.MULTILINE)


class _UnreadableError(Exception):
    """A file these readers do not understand. Never an empty result."""


def _copy_sources(dockerfile: str) -> list[str]:
    """Every COPY source taken from the build context, in file order."""
    sources: list[str] = []
    for line in re.sub(r"\\\r?\n", " ", dockerfile).splitlines():
        fields = line.split()
        if not fields or fields[0].upper() != "COPY":
            continue
        # From another stage, not from the build context. `--chown=` and the
        # other flags do not change where the files come from.
        if any(field.startswith("--from") for field in fields):
            continue
        arguments = [field for field in fields[1:] if not field.startswith("--")]
        if len(arguments) < 2 or arguments[0].startswith("["):
            raise _UnreadableError(f"cannot read this COPY: {line.strip()!r}")
        sources.extend(arguments[:-1])
    return sources


def _filter_entry(source: str) -> str:
    """The `paths:` entry that a change under `source` has to match."""
    path = source.removeprefix("./").rstrip("/")
    if source.endswith("/") or (SERVICE_ROOT / path).is_dir():
        return f"{path}/**"
    return path


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _children(lines: list[str], key: str) -> list[str]:
    """The lines nested under `key:`, which must be a direct child of `lines`."""
    lines = [line for line in lines if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        raise _UnreadableError(f"nothing to look for `{key}:` in")
    level = min(_indent(line) for line in lines)
    heads = [
        index
        for index, line in enumerate(lines)
        if _indent(line) == level and line.strip() == f"{key}:"
    ]
    if len(heads) != 1:
        raise _UnreadableError(f"expected exactly one `{key}:` block, found {len(heads)}")
    block: list[str] = []
    for line in lines[heads[0] + 1 :]:
        if _indent(line) <= level:
            break
        block.append(line)
    if not block:
        raise _UnreadableError(f"`{key}:` has nothing nested under it")
    return block


def _trigger_paths(workflow: str, event: str) -> list[str]:
    """The `on.<event>.paths` list, which must be a block list of scalars."""
    block = _children(_children(_children(workflow.splitlines(), "on"), event), "paths")
    level = _indent(block[0])
    entries: list[str] = []
    for line in block:
        match = _LIST_ITEM.fullmatch(line.strip())
        if _indent(line) != level or match is None:
            raise _UnreadableError(f"not a plain list item under {event}.paths: {line!r}")
        entries.append(next(group for group in match.groups() if group is not None))
    return entries


@pytest.mark.parametrize("event", TRIGGERS)
@pytest.mark.parametrize(("dockerfile", "workflow"), IMAGES)
def test_every_build_input_triggers_the_image_build(dockerfile, workflow, event):
    paths = _trigger_paths((WORKFLOWS / workflow).read_text(encoding="utf-8"), event)
    assert paths, f"{workflow} has an empty {event} paths: list"
    sources = _copy_sources((SERVICE_ROOT / dockerfile).read_text(encoding="utf-8"))
    assert sources, f"found no COPY from the build context in {dockerfile}, so this proved nothing"

    # They change the image without being COPY sources.
    also_build_inputs = [dockerfile, ".dockerignore", f".github/workflows/{workflow}"]
    required = [*(_filter_entry(source) for source in sources), *also_build_inputs]
    missing = [entry for entry in dict.fromkeys(required) if entry not in paths]
    assert not missing, (
        f"{workflow}'s {event} paths: filter is missing {missing}. "
        f"{dockerfile} builds the image from them, so a change confined to "
        "them builds nothing and production keeps the previous image."
    )


def test_the_images_checked_here_are_the_images_ci_builds():
    """A wrong or missing pair above would check one image against another's filter."""
    built = [
        (dockerfile, workflow.name)
        for workflow in sorted(WORKFLOWS.glob("*.y*ml"))
        for dockerfile in _BUILD_FILE.findall(workflow.read_text(encoding="utf-8"))
    ]
    assert sorted(built) == sorted(IMAGES)


def test_the_copy_reader():
    """Multi-stage, flags, continuation lines and a lowercase keyword."""
    dockerfile = (
        "FROM python:3.12-slim AS builder\n"
        "COPY requirements/base.txt requirements/base.txt\n"
        "FROM python:3.12-slim\n"
        "COPY --from=builder /install /usr/local\n"
        "# COPY ignored/ ignored/\n"
        "copy --chown=app:app core/ \\\n"
        "    domain/ /app/\n"
    )
    assert _copy_sources(dockerfile) == ["requirements/base.txt", "core/", "domain/"]


def test_the_workflow_reader_reads_block_lists():
    workflow = (
        "on:\n"
        "  push:\n"
        "    branches: [main]\n"
        "    # a comment\n"
        "    paths:\n"
        "      - 'core/**'\n"
        '      - "worker/**"\n'
        "      - Dockerfile.worker  # trailing\n"
        "  pull_request:\n"
        "    paths:\n"
        "      - 'shared/**'\n"
    )
    assert _trigger_paths(workflow, "push") == ["core/**", "worker/**", "Dockerfile.worker"]
    assert _trigger_paths(workflow, "pull_request") == ["shared/**"]


@pytest.mark.parametrize(
    "workflow",
    [
        "on:\n  push:\n    branches: [main]\n",
        "on:\n  push:\n    paths: ['core/**']\n",
        "on:\n  pull_request:\n    paths:\n      - 'core/**'\n",
        "on:\n  push:\n    paths:\n      core/**\n",
        "on:\n  push:\n    paths:\n      - 'core/**'\n        - 'worker/**'\n",
    ],
    ids=["no-paths", "flow-style", "no-such-event", "not-a-list-item", "mixed-indent"],
)
def test_a_workflow_it_cannot_read_is_an_error_not_an_empty_list(workflow):
    with pytest.raises(_UnreadableError):
        _trigger_paths(workflow, "push")
