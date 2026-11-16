import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
WRAPPER = ROOT / ".github" / "scanoss-docker-wrapper.sh"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
IMAGE = "scanoss-runtime-test-image"

FAKE_DOCKER = """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
host = next(a for p, a in zip(args, args[1:]) if p == "-v").rsplit(":/scanoss", 1)[0]
scan_path = args[args.index("scan") + 1]
base = os.path.join(host, scan_path)
files = sorted(
    os.path.relpath(os.path.join(directory, name), base).replace(os.sep, "/")
    for directory, _, names in os.walk(base)
    for name in names
)
with open(os.environ["FAKE_DOCKER_RECORD"], "w", encoding="utf-8") as record:
    json.dump({"args": args, "files": files}, record)
raise SystemExit(int(os.environ.get("FAKE_DOCKER_STATUS", "0")))
"""


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def fixture(tmp_path):
    repo = tmp_path / "fixture"
    write(repo / "acme_billing" / "src" / "invoice.py", "total = 1\n")
    write(repo / "acme_billing" / "src" / "tax.py", "rate = 1\n")
    write(repo / "acme_billing" / "build" / "generated.py", "generated = 1\n")
    write(repo / "acme_shipping" / "route.py", "stops = 1\n")
    write(
        repo / "scanoss.json",
        json.dumps({"settings": {"skip": {"patterns": {"scanning": ["build/"]}}}}),
    )
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "SCANOSS test")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "test: create provenance fixture")
    git(repo, "tag", "acme_billing-v1.0.0")
    write(repo / "acme_billing" / "src" / "invoice.py", "total = 2\n")
    write(repo / "acme_billing" / "src" / "discount.py", "percent = 1\n")
    write(repo / "acme_billing" / "build" / "generated.py", "generated = 2\n")
    write(repo / "acme_shipping" / "route.py", "stops = 2\n")
    git(repo, "rm", "-q", "acme_billing/src/tax.py")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "test: change provenance fixture")

    fake_docker = tmp_path / "docker"
    fake_docker.write_text(FAKE_DOCKER, encoding="utf-8")
    fake_docker.chmod(0o755)
    return repo, fake_docker, tmp_path / "record.json"


def changed_files(repo, tmp_path, since, path):
    listing = git(
        repo, "diff", "-z", "--name-only", "--diff-filter=d", since, "HEAD", "--", path
    )
    list_path = tmp_path / "changed-files"
    list_path.write_text(listing.stdout, encoding="utf-8")
    return list_path


def run_wrapper(fixture, scan_path=".", changed_from=None, status=0):
    repo, fake_docker, record = fixture
    environment = os.environ.copy()
    environment["SCANOSS_REAL_DOCKER"] = str(fake_docker)
    environment["SCANOSS_RUNTIME_IMAGE"] = IMAGE
    environment["FAKE_DOCKER_RECORD"] = str(record)
    environment["FAKE_DOCKER_STATUS"] = str(status)
    environment.pop("SCANOSS_CHANGED_FILES_FROM", None)
    if changed_from is not None:
        environment["SCANOSS_CHANGED_FILES_FROM"] = str(changed_from)
    record.unlink(missing_ok=True)
    result = subprocess.run(
        [
            "bash",
            str(WRAPPER),
            "run",
            "-v",
            f"{repo}:/scanoss",
            IMAGE,
            "scan",
            scan_path,
            "--output",
            "./results.json",
        ],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
    )
    scanned = (
        json.loads(record.read_text(encoding="utf-8")) if record.exists() else None
    )
    return result, scanned


def test_full_scan_covers_every_authored_file(fixture):
    result, scanned = run_wrapper(fixture)

    assert result.returncode == 0, result.stderr
    assert scanned["files"] == [
        "acme_billing/src/discount.py",
        "acme_billing/src/invoice.py",
        "acme_shipping/route.py",
        "scanoss.json",
    ]


def test_changed_file_list_narrows_the_scan_to_the_package_since_its_tag(
    fixture, tmp_path
):
    repo = fixture[0]
    changed = changed_files(repo, tmp_path, "acme_billing-v1.0.0", "acme_billing")

    result, scanned = run_wrapper(fixture, changed_from=changed)

    assert result.returncode == 0, result.stderr
    assert scanned["files"] == [
        "acme_billing/src/discount.py",
        "acme_billing/src/invoice.py",
    ]
    assert "--retry" in scanned["args"]
    assert not list(repo.glob(".scanoss-source-*"))


def test_empty_tree_base_scans_the_whole_package_with_exclusions(fixture, tmp_path):
    changed = changed_files(fixture[0], tmp_path, EMPTY_TREE, "acme_billing")

    result, scanned = run_wrapper(fixture, changed_from=changed)

    assert result.returncode == 0, result.stderr
    assert scanned["files"] == [
        "acme_billing/src/discount.py",
        "acme_billing/src/invoice.py",
    ]


def test_missing_changed_file_list_fails_without_scanning(fixture, tmp_path):
    result, scanned = run_wrapper(fixture, changed_from=tmp_path / "missing")

    assert result.returncode == 2
    assert "does not name a file" in result.stderr
    assert scanned is None


def test_changed_file_list_requires_a_repository_root_scan(fixture, tmp_path):
    changed = changed_files(fixture[0], tmp_path, "acme_billing-v1.0.0", "acme_billing")

    result, scanned = run_wrapper(
        fixture, scan_path="acme_billing", changed_from=changed
    )

    assert result.returncode == 2
    assert "requires a full scan" in result.stderr
    assert scanned is None


def test_scanner_failure_is_returned_unchanged(fixture, tmp_path):
    changed = changed_files(fixture[0], tmp_path, "acme_billing-v1.0.0", "acme_billing")

    result, scanned = run_wrapper(fixture, changed_from=changed, status=1)

    assert result.returncode == 1
    assert scanned is not None
