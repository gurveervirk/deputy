# Code provenance checks

The `Code Provenance` workflow runs for pull requests targeting `main`. It uses SCANOSS in delta mode, so the scan focuses on files changed by the pull request. File and snippet matching are enabled; dependency analysis remains disabled because dependency and license policy is handled separately.

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

The workflow uses `pull_request`, does not execute repository-provided scripts, and does not use secrets. Fork pull requests receive a restricted token from GitHub, so comments or check updates may be unavailable and should be treated as an infrastructure limitation. Private repositories follow the same fingerprint and metadata data-flow; repository access is limited by the workflow permissions.

Generated outputs, caches, environments, package locks, and workflow configuration are excluded in `scanoss.json`. Authored source, tests, and documentation remain eligible for scanning unless a later reviewed exclusion is added.
