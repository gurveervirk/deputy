import json
import os
import re
import sys
from pathlib import Path

CORE_SWHID = re.compile(r"^swh:1:cnt:[0-9a-f]{40}$")
DIRECTORY_SWHID = re.compile(r"^swh:1:dir:[0-9a-f]{40}$")


def write_summary(lines):
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write("\n".join(lines) + "\n")
    print("\n".join(lines))


def classify_swhid(swhid):
    if isinstance(swhid, str) and CORE_SWHID.fullmatch(swhid):
        return "content"
    if isinstance(swhid, str) and DIRECTORY_SWHID.fullmatch(swhid):
        return "directory"
    return "unknown"


def is_relative_path(path):
    return (
        isinstance(path, str)
        and bool(path)
        and not path.startswith("/")
        and path not in {"..", "."}
        and not path.startswith("../")
    ) or path == "."


def valid_approval(item):
    if not isinstance(item, dict):
        return None
    path = item.get("path")
    swhid = item.get("swhid")
    reason = item.get("reason") or item.get("context")
    if not is_relative_path(path) or any(token in path for token in "*?["):
        return None
    if not isinstance(swhid, str) or not CORE_SWHID.fullmatch(swhid):
        return None
    if not isinstance(reason, str) or not reason.strip():
        return None
    return {"path": path, "swhid": swhid, "reason": reason.strip()}


def main():
    result = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    allowlist = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    evidence_path = Path(sys.argv[3])
    enrichment_status = sys.argv[4] if len(sys.argv) > 4 else "not_requested"
    enrichment_error = sys.argv[5].strip() if len(sys.argv) > 5 else ""
    enrichment_exit_status = None
    if len(sys.argv) > 6 and sys.argv[6].strip():
        enrichment_exit_status = int(sys.argv[6])
    enrichment_reason = sys.argv[7].strip() if len(sys.argv) > 7 else ""

    if not isinstance(result, dict):
        raise ValueError("scanner result must be a JSON object")

    entries = []
    known_content = []
    known_directories = []
    for path, raw_entry in result.items():
        if not isinstance(raw_entry, dict) or not is_relative_path(path):
            continue
        swhid = raw_entry.get("swhid")
        object_type = classify_swhid(swhid)
        entry = {
            "path": path,
            "known": raw_entry.get("known") is True,
            "swhid": swhid,
            "object_type": object_type,
            "provenance": raw_entry.get("provenance"),
        }
        entries.append(entry)
        if entry["known"] and object_type == "content":
            known_content.append(entry)
        elif entry["known"] and object_type == "directory":
            known_directories.append(entry)

    raw_approvals = allowlist.get("matches", [])
    if not isinstance(raw_approvals, list):
        raw_approvals = []
    approvals = []
    invalid_approvals = 0
    for item in raw_approvals:
        approval = valid_approval(item)
        if approval is None:
            invalid_approvals += 1
        else:
            approvals.append(approval)

    undeclared = [
        entry
        for entry in known_content
        if not any(
            approval["path"] == entry["path"] and approval["swhid"] == entry["swhid"]
            for approval in approvals
        )
    ]

    evidence = {
        "schema_version": 1,
        "scanner": "swh.scanner",
        "scanner_version": "0.8.3",
        "archive_scan": "ok",
        "provenance_enrichment": {
            "status": enrichment_status,
            "exit_status": enrichment_exit_status,
            "reason": enrichment_reason or None,
            "error": enrichment_error or None,
        },
        "entries": entries,
        "policy": {
            "known_content": len(known_content),
            "known_directories_ignored": len(known_directories),
            "undeclared_content": len(undeclared),
            "invalid_approvals_ignored": invalid_approvals,
        },
    }
    evidence_path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    lines = [
        "## Software Heritage provenance prototype",
        f"Known archived content files: {len(known_content)}",
        f"Known archived directory nodes (diagnostic only): {len(known_directories)}",
        f"Undeclared archived content files: {len(undeclared)}",
        f"Provenance enrichment: {enrichment_status}",
    ]
    if invalid_approvals:
        lines.append(f"Ignored malformed approvals: {invalid_approvals}")
        print(
            "::warning title=Software Heritage allowlist::"
            f"Ignored {invalid_approvals} malformed approval(s); exact path, core "
            "content SWHID, and review reason are required."
        )

    for entry in undeclared:
        lines.append(f"- `{entry['path']}` — `{entry['swhid']}`")
        print(
            f"::warning file={entry['path']}::"
            "Known archived content requires provenance review"
        )

    if enrichment_status == "unavailable":
        lines.append(
            "Origin/provenance enrichment was unavailable; archive identities "
            "remain reviewable, but the full provenance contract was not evaluated."
        )
    elif enrichment_status == "not_requested":
        lines.append("Origin/provenance enrichment was not requested for this scan.")
    if known_content or known_directories:
        lines.append("")
        lines.append(
            "This prototype is report-only; it does not fail the workflow for a match."
        )
    else:
        lines.append("")
        lines.append("No archived content or directory match was reported.")

    write_summary(lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
