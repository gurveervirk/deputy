import json
import os
import sys
from pathlib import Path


def write_summary(lines):
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write("\n".join(lines) + "\n")
    print("\n".join(lines))


result = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
allowlist = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
allowed_matches = allowlist.get("matches", [])
known = []

for path, entry in result.items():
    if isinstance(entry, dict) and entry.get("known") is True:
        identity = entry.get("provenance") or entry.get("swhid")
        known.append((path, identity))

undeclared = [
    (path, identity)
    for path, identity in known
    if not any(
        item.get("path") == path
        and (not item.get("swhid") or item.get("swhid") == identity)
        for item in allowed_matches
    )
]

lines = [
    "## Software Heritage provenance prototype",
    f"Known archived files: {len(known)}",
    f"Undeclared archived files: {len(undeclared)}",
]

for path, identity in undeclared:
    lines.append(f"- `{path}` — `{identity}`")
    print(f"::warning file={path}::Known archived content requires provenance review")

if known:
    lines.append("")
    lines.append(
        "This prototype is report-only; it does not fail the workflow for a match."
    )
else:
    lines.append("")
    lines.append("No archived content match was reported.")

write_summary(lines)
