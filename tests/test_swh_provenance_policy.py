import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
POLICY = ROOT / ".github" / "swh-provenance-policy.py"


def swhid(kind, letter):
    return f"swh:1:{kind}:{letter * 40}"


def run_policy(
    tmp_path, result, allowlist, enrichment_status="not_requested", error=""
):
    result_path = tmp_path / "result.json"
    allowlist_path = tmp_path / "allowlist.json"
    evidence_path = tmp_path / "evidence.json"
    summary_path = tmp_path / "summary.md"
    result_path.write_text(json.dumps(result), encoding="utf-8")
    allowlist_path.write_text(json.dumps(allowlist), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(POLICY),
            str(result_path),
            str(allowlist_path),
            str(evidence_path),
            enrichment_status,
            error,
            "" if enrichment_status == "not_requested" else "0",
            "" if enrichment_status == "not_requested" else "test enrichment result",
        ],
        cwd=ROOT,
        env={"GITHUB_STEP_SUMMARY": str(summary_path)},
        capture_output=True,
        text=True,
        check=True,
    )
    return completed, json.loads(evidence_path.read_text(encoding="utf-8"))


def test_unknown_content_is_retained_without_a_finding(tmp_path):
    _, evidence = run_policy(
        tmp_path,
        {"new.py": {"known": False, "swhid": swhid("cnt", "a")}},
        {"matches": []},
    )

    assert evidence["entries"][0]["known"] is False
    assert evidence["policy"]["known_content"] == 0
    assert evidence["policy"]["undeclared_content"] == 0


def test_known_content_uses_core_swhid_without_provenance(tmp_path):
    core = swhid("cnt", "a")
    _, evidence = run_policy(
        tmp_path,
        {"vendor.py": {"known": True, "swhid": core, "provenance": None}},
        {"matches": []},
    )

    assert evidence["entries"][0]["swhid"] == core
    assert evidence["policy"]["known_content"] == 1
    assert evidence["policy"]["undeclared_content"] == 1


def test_qualified_provenance_is_separate_from_approval_identity(tmp_path):
    core = swhid("cnt", "b")
    qualified = "swh:1:snp:" + "c" * 40 + ";origin=https://example.invalid"
    _, evidence = run_policy(
        tmp_path,
        {"vendor.py": {"known": True, "swhid": core, "provenance": qualified}},
        {"matches": [{"path": "vendor.py", "swhid": core, "reason": "Reviewed reuse"}]},
    )

    entry = evidence["entries"][0]
    assert entry["swhid"] == core
    assert entry["provenance"] == qualified
    assert evidence["policy"]["undeclared_content"] == 0


def test_path_only_approval_is_ignored(tmp_path):
    core = swhid("cnt", "d")
    _, evidence = run_policy(
        tmp_path,
        {"vendor.py": {"known": True, "swhid": core}},
        {"matches": [{"path": "vendor.py", "reason": "Reviewed reuse"}]},
    )

    assert evidence["policy"]["invalid_approvals_ignored"] == 1
    assert evidence["policy"]["undeclared_content"] == 1


def test_wildcard_approval_is_ignored(tmp_path):
    core = swhid("cnt", "e")
    _, evidence = run_policy(
        tmp_path,
        {"vendor.py": {"known": True, "swhid": core}},
        {
            "matches": [
                {
                    "path": "*.py",
                    "swhid": core,
                    "reason": "Reviewed reuse",
                }
            ]
        },
    )

    assert evidence["policy"]["invalid_approvals_ignored"] == 1
    assert evidence["policy"]["undeclared_content"] == 1


def test_different_core_swhid_at_approved_path_remains_visible(tmp_path):
    approved = swhid("cnt", "f")
    unrelated = swhid("cnt", "0")
    _, evidence = run_policy(
        tmp_path,
        {"vendor.py": {"known": True, "swhid": unrelated}},
        {
            "matches": [
                {
                    "path": "vendor.py",
                    "swhid": approved,
                    "reason": "Reviewed reuse",
                }
            ]
        },
    )

    assert evidence["policy"]["undeclared_content"] == 1


def test_known_directory_is_diagnostic_only(tmp_path):
    _, evidence = run_policy(
        tmp_path,
        {"pkg": {"known": True, "swhid": swhid("dir", "1")}},
        {"matches": []},
    )

    assert evidence["policy"]["known_content"] == 0
    assert evidence["policy"]["known_directories_ignored"] == 1
    assert evidence["policy"]["undeclared_content"] == 0


def test_enrichment_failure_preserves_archive_identity_result(tmp_path):
    core = swhid("cnt", "2")
    _, evidence = run_policy(
        tmp_path,
        {"vendor.py": {"known": True, "swhid": core}},
        {"matches": []},
        enrichment_status="unavailable",
        error="permission denied",
    )

    assert evidence["archive_scan"] == "ok"
    assert evidence["provenance_enrichment"] == {
        "status": "unavailable",
        "exit_status": 0,
        "reason": "test enrichment result",
        "error": "permission denied",
    }
    assert evidence["policy"]["known_content"] == 1
    assert evidence["policy"]["undeclared_content"] == 1
