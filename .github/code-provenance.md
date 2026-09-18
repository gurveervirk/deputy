# Code provenance checks

The `Code Provenance` workflow runs for pull requests targeting `main`. For pull requests from the base repository it uses SCANOSS in delta mode, so the scan focuses on files changed by the pull request. File and snippet matching are enabled; dependency analysis remains disabled because dependency and license policy is handled separately.

Fork pull requests take a visible safe-skip path. The scanner does not check out or execute fork code because the pull-request token is restricted. The safe-skip job emits a notice; maintainers should review the change from a trusted branch before relying on a provenance result.

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

The v1.54.2 runtime has built-in filters for hidden paths, non-code extensions, and `example`/`examples` folders. The pinned GitHub Action does not expose the corresponding runtime flags, so the workflow installs `.github/scanoss-docker-wrapper.sh` for the scan step only. The wrapper runs only the runtime's `delta copy` and `scan` operations as the GitHub runner UID/GID, filters the generated PR-delta directory using the scanning exclusions from `scanoss.json`, delegates conversion and inspection calls to the container's default user, and appends `--all-hidden --all-extensions --all-folders` only to the exact pinned runtime's `scan` call. This preserves original paths, keeps the checked-out source untouched, retains `scanoss.json` as the deliberate policy for generated and transient content, and lets SPDX-Lite conversion resolve the container user. Debug logging is enabled during calibration so hosted logs can show fingerprinting of changed authored files that would otherwise be filtered.

The v1.54.2 client includes the upstream 1.53.2 retry/error handling: HTTP 429 is reported as rate limiting, HTTP 503 as service unavailability, and both honor `Retry-After` with bounded backoff before failing technically. The v1.54.1 fingerprinting skip-pattern fix and v1.54.2 non-zero WFP failure behavior are also included. This refresh does not change the report-only policy or treat an exhausted retry sequence as a clean scan.

## Runtime and alternatives evaluation

The 2026-09-18 v1.54.2 hosted validation reached fingerprinting for authored hidden Markdown/YAML paths in both repositories, then received HTTP 503 responses from `api.osskb.org` with a long server retry interval. The client retried with its bounded backoff and failed technically after the retry limit; it did not produce a clean or policy result. This confirms the runtime error handling while leaving public-service reliability unresolved. `undeclared` remains report-only.

The candidates below were compared against this workflow's source/file/snippet provenance contract, not ordinary dependency SCA:

| Candidate | Matching, evidence, and integration | Data flow, operations, and current decision |
| --- | --- | --- |
| SCANOSS | File and snippet matching against public OSSKB; PURL/component, license, source, and line evidence; official delta-capable GitHub Action; raw, CycloneDX, SPDX-Lite, and CSV outputs; path-scoped `bom.include` declarations | Generates fingerprints locally and sends scan metadata/fingerprints to the service. Action and runtime are full-SHA/manifest pinned, and technical failures are distinct from policy findings. Retain as the report-only baseline while service reliability is evaluated. |
| ScanCode.io / MatchCode | MatchCode toolkit computes file/codebase fingerprints; ScanCode.io provides JSON/XLSX/SPDX/CycloneDX outputs and a GitHub Action. Public documentation says there is no public MatchCode.io instance and current matching is limited to archives/directories/files from Maven and npm packages | A broad public-OSS corpus and service would need to be hosted and maintained. Changed-file PR scanning, declarations, fork handling, failure semantics, and corpus breadth require an experiment; no replacement is active. |
| Codequiry | Web/source matching with line-level evidence and source links; REST API returns machine-readable results and supports custom CI packaging | The documented API uploads source ZIPs rather than only fingerprints. Public web matching is paid/limited, while public terms describe retention and shared-corpus differences by plan. No official immutable GitHub Action or proven path-scoped baseline/fork/outage contract was found; treat as an experiment only. |

Peer-only similarity tools, dependency-only SCA, license-only scanners, and general code-quality tools are not equivalent source-provenance replacements. Any future alternative must be tested with clean, known-match, approved-match, unrelated-match, service-failure, fork, private-repository, outbound-data, and artifact-retention cases before adoption.

The workflow uses `pull_request`, does not execute repository-provided scripts, and does not use secrets. Same-repository scans use only `checks: write`, `contents: read`, and `pull-requests: write`; the fork safe-skip path requests no permissions. The workflow does not use `pull_request_target`. Private repositories follow the same fingerprint and metadata data-flow; repository access is limited by the workflow permissions.

Generated outputs, caches, environments, package locks, and build artifacts are excluded in `scanoss.json`. Authored source, tests, documentation, scripts, and workflow/action files remain eligible for scanning unless a later reviewed exclusion is added.
