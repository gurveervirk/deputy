import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
WRAPPER = ROOT / ".github" / "swh-provenance-scan.sh"


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def make_fixture(tmp_path):
    repo = tmp_path / "fixture"
    repo.mkdir()
    github = repo / ".github"
    github.mkdir()
    for name in ("swh-provenance-scan.sh", "swh-provenance-policy.py"):
        shutil.copy2(ROOT / ".github" / name, github / name)
    (github / "swh-provenance-allowlist.json").write_text(
        '{"matches": []}\n', encoding="utf-8"
    )
    (repo / "source.py").write_text("original\n", encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "SWH test")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "test: create provenance fixture")
    base_sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "source.py").write_text("changed\n", encoding="utf-8")
    git(repo, "add", "source.py")
    git(repo, "commit", "-qm", "test: change provenance fixture")
    head_sha = git(repo, "rev-parse", "HEAD").stdout.strip()

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "uv").write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

mode = os.environ.get("FAKE_SWH_MODE", "success")
if mode == "archive-failure" and "--provenance" not in sys.argv:
    print("archive service unavailable", file=sys.stderr)
    raise SystemExit(7)
if "--provenance" in sys.argv:
    if mode == "enrichment-success":
        print(json.dumps({"source.py": {"known": True, "swhid": "swh:1:cnt:" + "a" * 40, "provenance": "swh:1:snp:" + "b" * 40}}))
        raise SystemExit(0)
    if mode == "enrichment-unavailable":
        print("provenance lookup progress", file=sys.stderr)
        raise SystemExit(0)
    print("provenance lookup progress", file=sys.stderr)
    raise SystemExit(0)
print(json.dumps({"source.py": {"known": True, "swhid": "swh:1:cnt:" + "a" * 40}}))
""",
        encoding="utf-8",
    )
    (fake_bin / "uv").chmod(0o755)
    return repo, fake_bin, base_sha, head_sha


def run_scan(repo, fake_bin, base_sha, head_sha, tmp_path, mode, enrichment):
    output = tmp_path / mode
    output.mkdir()
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["FAKE_SWH_MODE"] = mode
    environment["SWH_PROVENANCE_ENRICHMENT"] = enrichment
    return subprocess.run(
        [
            "bash",
            ".github/swh-provenance-scan.sh",
            base_sha,
            head_sha,
            str(output / "delta"),
            str(output / "result.json"),
            str(output / "scan.log"),
            str(output / "evidence.json"),
        ],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
    ), output / "evidence.json"


def read_evidence(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_wrapper_distinguishes_archive_and_enrichment_failures(tmp_path):
    repo, fake_bin, base_sha, head_sha = make_fixture(tmp_path)

    archive_only, archive_evidence_path = run_scan(
        repo, fake_bin, base_sha, head_sha, tmp_path, "archive-only", "disabled"
    )
    assert archive_only.returncode == 0
    archive_evidence = read_evidence(archive_evidence_path)
    assert archive_evidence["archive_scan"] == "ok"
    assert archive_evidence["provenance_enrichment"] == {
        "status": "not_requested",
        "exit_status": None,
        "reason": None,
        "error": None,
    }

    enriched, enriched_evidence_path = run_scan(
        repo,
        fake_bin,
        base_sha,
        head_sha,
        tmp_path,
        "enrichment-success",
        "enabled",
    )
    assert enriched.returncode == 0
    enriched_evidence = read_evidence(enriched_evidence_path)
    assert enriched_evidence["provenance_enrichment"] == {
        "status": "available",
        "exit_status": 0,
        "reason": None,
        "error": None,
    }
    assert enriched_evidence["entries"][0]["provenance"] == "swh:1:snp:" + "b" * 40

    unavailable, unavailable_evidence_path = run_scan(
        repo,
        fake_bin,
        base_sha,
        head_sha,
        tmp_path,
        "enrichment-unavailable",
        "enabled",
    )
    assert unavailable.returncode == 3
    unavailable_evidence = read_evidence(unavailable_evidence_path)
    assert unavailable_evidence["archive_scan"] == "ok"
    assert unavailable_evidence["policy"]["known_content"] == 1
    assert unavailable_evidence["provenance_enrichment"] == {
        "status": "unavailable",
        "exit_status": 0,
        "reason": "enrichment produced no valid JSON",
        "error": "scanner output does not contain a JSON object",
    }
    assert "provenance enrichment unavailable" in unavailable.stderr.lower()

    archive_failure, archive_failure_evidence_path = run_scan(
        repo,
        fake_bin,
        base_sha,
        head_sha,
        tmp_path,
        "archive-failure",
        "disabled",
    )
    assert archive_failure.returncode == 2
    assert not archive_failure_evidence_path.exists()
    assert "scanner failed before producing a result" in archive_failure.stderr
