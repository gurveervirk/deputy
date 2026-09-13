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

The runtime image is pinned to `ghcr.io/scanoss/scanoss-py@sha256:38b006ea5ebbc7972d46f1affd066a950b2349246abd1c5ee30fca3e1d8eff94`, which is the immutable multi-platform manifest resolved from `ghcr.io/scanoss/scanoss-py:v1.52.1`. The manifest has Linux amd64 and arm64 images.

The v1.52.1 runtime has built-in filters for hidden paths, non-code extensions, and `example`/`examples` folders. The pinned GitHub Action does not expose the corresponding runtime flags, so the workflow installs `.github/scanoss-docker-wrapper.sh` for the scan step only. The wrapper delegates delta-copy calls unchanged and appends `--all-hidden --all-extensions --all-folders` only to the exact pinned runtime's `scan` call. This preserves the original paths and keeps the `scanoss.json` exclusions as the deliberate policy for generated and transient content. Debug logging is enabled during calibration so hosted logs can show fingerprinting of changed authored files that would otherwise be filtered.

The workflow uses `pull_request`, does not execute repository-provided scripts, and does not use secrets. Same-repository scans use only `checks: write`, `contents: read`, and `pull-requests: write`; the fork safe-skip path requests no permissions. The workflow does not use `pull_request_target`. Private repositories follow the same fingerprint and metadata data-flow; repository access is limited by the workflow permissions.

Generated outputs, caches, environments, package locks, and build artifacts are excluded in `scanoss.json`. Authored source, tests, documentation, scripts, and workflow/action files remain eligible for scanning unless a later reviewed exclusion is added.
