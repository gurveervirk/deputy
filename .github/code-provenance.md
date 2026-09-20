# Code provenance checks

The `Code Provenance` workflow runs for pull requests targeting `main`. For pull requests from the base repository it uses SCANOSS in delta mode, so the scan focuses on files changed by the pull request. File and snippet matching are enabled; dependency analysis remains disabled because dependency and license policy is handled separately.

Fork pull requests take a visible safe-skip path. The scanner does not check out or execute fork code because the pull-request token is restricted. The safe-skip job emits a notice; maintainers should review the change from a trusted branch before relying on a provenance result. For same-repository pull requests, the workflow executes the reviewed `.github/scanoss-docker-wrapper.sh` to invoke the pinned scanner; it does not execute application or test code.

A match means that SCANOSS found a likely match in its indexed open-source knowledge base. It is a review signal about possible reuse, not proof that code is plagiarized or that a legal conclusion has been reached.

## Reviewing a match

Review the component, license, source repository, matched file, and snippet lines reported by the workflow. Confirm whether the match is expected and whether the source and license obligations are understood.

Expected matches are declared in the version-controlled `scanoss.json` file under `bom.include`. Prefer a narrow entry containing the changed path, the component PURL, and a short review note:

```json
{
  "path": "path/to/source.py",
  "purl": "pkg:github/owner/repository@version",
  "comment": "Reviewed source reuse and license for this file"
}
```

Keep declarations scoped to the smallest appropriate path. A declaration should not be used to suppress an unrelated match in a new file. Existing accepted matches can be baselined by adding their exact path and component to `bom.include` after review.

## Current rollout

The `undeclared` policy is currently report-only. A policy finding is surfaced as a non-blocking policy result while the workflow is calibrated. Scanner, API, permission, rate-limit, and service failures remain technical failures because `halt_on_error` is enabled; they must be retried or diagnosed and must not be interpreted as provenance findings.

The policy will become merge-blocking only after representative pull requests establish useful exclusions and an acceptable false-positive rate. The enforcement change must keep the same version-controlled declarations and review process.

## Data handling and workflow safety

SCANOSS generates file and snippet fingerprints on the GitHub Actions runner and sends fingerprints and scan metadata to the configured SCANOSS service. The workflow does not intentionally upload repository source text to that service. The action also writes scan results to GitHub Actions artifacts and publishes summaries, checks, annotations, and pull-request comments through the GitHub token.

The runtime image is pinned to `ghcr.io/scanoss/scanoss-py@sha256:33a63229a4e36771dc90d1e7efc79f57e4e57d3a4b5c20cf52ea2c70f964481c`, which is the immutable multi-platform manifest resolved from `ghcr.io/scanoss/scanoss-py:v1.54.2`. The manifest has Linux amd64 and arm64 images.

The v1.54.2 runtime has built-in filters for hidden paths, non-code extensions, and `example`/`examples` folders. The pinned GitHub Action does not expose the corresponding runtime flags, so the workflow installs `.github/scanoss-docker-wrapper.sh` for the scan step only. The wrapper runs only the runtime's `delta copy` and `scan` operations as the GitHub runner UID/GID, filters the generated PR-delta directory using the scanning exclusions from `scanoss.json`, delegates conversion and inspection calls to the container's default user, and appends `--retry 0 --all-hidden --all-extensions --all-folders` only to the exact pinned runtime's `scan` call. This preserves original paths, keeps the checked-out source untouched, retains `scanoss.json` as the deliberate policy for generated and transient content, and lets SPDX-Lite conversion resolve the container user. Debug logging is enabled during calibration so hosted logs can show fingerprinting of changed authored files that would otherwise be filtered.

The v1.54.2 client includes the upstream 1.53.2 retry/error handling: HTTP 429 is reported as rate limiting, HTTP 503 as service unavailability, and both honor `Retry-After` with bounded backoff before failing technically. For pull-request scans, the wrapper intentionally passes `--retry 0`: the public service can advertise a multi-day retry interval, so repeating requests during one PR run is not useful. This is a fast technical failure, not a clean result. The v1.54.1 fingerprinting skip-pattern fix and v1.54.2 non-zero WFP failure behavior are also included. This refresh does not change the report-only policy.

## Runtime and alternatives evaluation

The 2026-09-18 v1.54.2 hosted validation reached fingerprinting for authored hidden Markdown/YAML paths in both repositories, then received HTTP 503 responses from `api.osskb.org` whose response body said `Rate limit exceeded`, with a long server retry interval. Upstream classifies HTTP 429 as rate limiting and HTTP 503 as service unavailable; this status/body mismatch does not prove whether the underlying cause is quota, service capacity, or another provider condition. The initial run spent roughly five minutes retrying before failing technically. PR scans now use `--retry 0`, so the same condition fails promptly while remaining visibly technical. When no result file is produced, the `undeclared` policy is cancelled/not evaluated; that is not a provenance-policy failure or a clean result. Public-service reliability remains unresolved and `undeclared` remains report-only.

The candidates below were compared against this workflow's source/file/snippet provenance contract, not ordinary dependency SCA:

| Candidate | Matching, evidence, and integration | Data flow, operations, and current decision |
| --- | --- | --- |
| SCANOSS | File and snippet matching against public OSSKB; PURL/component, license, source, and line evidence; official delta-capable GitHub Action; raw, CycloneDX, SPDX-Lite, and CSV outputs; path-scoped `bom.include` declarations | Generates fingerprints locally and sends scan metadata/fingerprints to the service. Action and runtime are full-SHA/manifest pinned, and technical failures are distinct from policy findings. Retain as the report-only baseline while service reliability is evaluated. |
| ScanCode.io / MatchCode | MatchCode toolkit computes file/codebase fingerprints; ScanCode.io provides JSON/XLSX/SPDX/CycloneDX outputs and a GitHub Action. Public documentation says there is no public MatchCode.io instance and current matching is limited to archives/directories/files from Maven and npm packages | A broad public-OSS corpus and service would need to be hosted and maintained. Changed-file PR scanning, declarations, fork handling, failure semantics, and corpus breadth require an experiment; no replacement is active. |
| Codequiry | Web/source matching with line-level evidence and source links; REST API returns machine-readable results and supports custom CI packaging | The documented API uploads source ZIPs rather than only fingerprints. Public web matching is paid/limited, while public terms describe retention and shared-corpus differences by plan. No official immutable GitHub Action or proven path-scoped baseline/fork/outage contract was found; treat as an experiment only. |
| Software Heritage `swh-scanner` | Local content identity discovery against the Software Heritage archive, machine-readable JSON, core content SWHIDs, and an optional provenance mode that can return qualified source/origin references | The scanner reports anonymous fingerprints rather than raw source. The provenance API requires special permission, current matching is exact file/content identity rather than snippet matching, and license metadata is not part of the result. The aligned workflow is an opt-in report-only prototype, not a replacement yet. |

Peer-only similarity tools, dependency-only SCA, license-only scanners, and general code-quality tools are not equivalent source-provenance replacements. Any future alternative must be tested with clean, known-match, approved-match, unrelated-match, service-failure, fork, private-repository, outbound-data, and artifact-retention cases before adoption.

The workflow uses `pull_request` and does not use secrets. Fork pull requests are not checked out or executed. Same-repository scans execute the reviewed `.github/scanoss-docker-wrapper.sh` but do not execute application or test code; they use only `checks: write`, `contents: read`, and `pull-requests: write`. The fork safe-skip path requests no permissions. The workflow does not use `pull_request_target`. Private repositories follow the same fingerprint and metadata data-flow; repository access is limited by the workflow permissions.

Generated outputs, caches, environments, package locks, and build artifacts are excluded in `scanoss.json`. Authored source, tests, documentation, scripts, and workflow/action files remain eligible for scanning unless a later reviewed exclusion is added.

## Software Heritage prototype

The repository contains an opt-in `Code Provenance (Software Heritage prototype)` workflow for Phase 2 evaluation. It scans only added or modified authored paths, uses `swh-scanner==0.8.3`, and keeps the result report-only. Pull-request execution is enabled only when the repository variable `SWH_PROVENANCE_PROTOTYPE` is set to `enabled`; manual runs require explicit base and head commits. Fork pull requests have a no-permission safe-skip path. Ordinary archive scanning is always attempted without privileged provenance enrichment. Manual runs may explicitly enable the separate enrichment attempt; an unavailable requested enrichment preserves and uploads the archive result but exits with a technical failure. Archive-only scans remain green when archive lookup succeeds, and enrichment unavailability is never converted into a provenance-policy finding.

The scanner is installed with `astral-sh/setup-uv` pinned to `bec219d24cd3e171d82865faccec33120bb574f4` (v10.1.0), `actions/checkout` pinned to `3d3c42e5aac5ba805825da76410c181273ba90b1` (v7.0.1), and `actions/upload-artifact` pinned to `ea165f8d65b6e75b540449e92b4886f43607fa02` (v4.6.2). The direct scanner artifacts are recorded for the 0.8.3 release: the PyPI source distribution has SHA-256 `d0b09abaa243207203d0766fb4f2b7b6cb47ed7d4c316f2b95876bfbb5c36e44` and the wheel has SHA-256 `d13abac078d995e6dd15c3ff848180066b111bc678b932f67e1c997a1f4ca65a`. These hashes are documentation only; `uv tool run --from swh.scanner==0.8.3` does not enforce them. The transitive runtime closure is resolved by uv for this prototype; a production replacement would require a committed lock or immutable container before enforcement.

Controlled evaluation on 2026-09-19 used a temporary copy of a public Software Heritage scanner source file. The unmodified file was returned as `known: true` with a core content SWHID. Adding one local line made the modified file `known: false`, demonstrating exact content/file matching rather than snippet matching. The ordinary archive result is retained before any optional enrichment. When manual enrichment was enabled, it emitted `Your account does not have permission to query the Provenance API` while returning exit code 0 and no JSON; the wrapper preserves the archive identity result, records `status: unavailable`, `exit_status: 0`, and `reason: enrichment produced no valid JSON`, uploads the evidence, then exits nonzero as a technical failure. The scanner's own output states that only anonymous fingerprints are sent, and no raw source upload was observed in the controlled run.

The prototype allowlist is `.github/swh-provenance-allowlist.json`. Each approval must contain an exact repository-relative path, the exact core content SWHID (`swh:1:cnt:...`), and a non-empty `reason` or `context`; path-only and wildcard approvals are ignored. Qualified `provenance` values remain evidence and are never used as the approval identity. Directory SWHIDs (`swh:1:dir:...`) are retained as diagnostic evidence but do not count as authored-file findings.

Successful archive scans write a machine-readable `.json` evidence artifact containing relative paths, `known`, core SWHIDs, optional qualified provenance, object type, policy counts, and structured enrichment status/reason/exit status. The artifact is retained for 14 days with the immutable upload-artifact action pin and contains no repository source. If archive scanning itself fails or produces no valid result, no clean result is fabricated. If explicitly requested enrichment fails, the artifact is still uploaded before the wrapper returns a technical nonzero; the policy remains report-only. These changes do not alter the existing SCANOSS workflow.

Software Heritage currently remains a supplemental candidate: the controlled exact-content experiment does not demonstrate the snippet-level matching required by T4, and the enrichment API is permission-gated. SCANOSS remains the active report-only baseline while snippet coverage and hosted reliability are evaluated further.
