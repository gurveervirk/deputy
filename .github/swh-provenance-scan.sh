#!/usr/bin/env bash
set -euo pipefail

base_sha=${1:?base commit is required}
head_sha=${2:?head commit is required}
delta_dir=${3:-"${RUNNER_TEMP:-/tmp}/swh-provenance-delta"}
result_path=${4:-"${RUNNER_TEMP:-/tmp}/swh-provenance-result.json"}
log_path=${5:-"${RUNNER_TEMP:-/tmp}/swh-provenance.log"}
config_path=${RUNNER_TEMP:-/tmp}/swh-provenance-config.yml
cache_home=${RUNNER_TEMP:-/tmp}/swh-provenance-cache

mkdir -p "$delta_dir" "$cache_home/swh"
printf '%s\n' 'SWH SCANNER SETUP 1.0' > "$cache_home/swh/scanner_setup_was_run"
printf '%s\n' \
  'keycloak:' \
  '  server_url: https://auth.softwareheritage.org/auth/' \
  '  realm_name: SoftwareHeritage' \
  '  client_id: swh-web' \
  'web-api:' \
  '  url: https://archive.softwareheritage.org/api/1/' \
  'scanner:' \
  '  disable_global_patterns: true' \
  '  disable_vcs_patterns: true' \
  '  exclude: []' \
  '  exclude_templates: []' > "$config_path"

is_excluded() {
  case "$1" in
    .git/*|.deputy/*|.venv/*|venv/*|.tox/*|.nox/*|__pycache__/*|*/__pycache__/*|.pytest_cache/*|.mypy_cache/*|.ruff_cache/*|.coverage|coverage.xml|htmlcov/*|build/*|*/build/*|dist/*|*/dist/*|site/*|*.egg-info/*|*/.egg-info/*|node_modules/*|*/node_modules/*|target/*|*/target/*|scanoss.json|uv.lock|*.pyc|*.pyo|*.so|*.dylib|*.class)
      return 0
      ;;
  esac
  return 1
}

git diff --name-only --diff-filter=ACMR -z "$base_sha" "$head_sha" |
  while IFS= read -r -d '' path; do
    if [[ -f "$path" ]] && ! is_excluded "$path"; then
      destination="$delta_dir/$path"
      mkdir -p "$(dirname "$destination")"
      cp -p -- "$path" "$destination"
    fi
  done

set +e
XDG_CACHE_HOME="$cache_home" \
  SWH_CONFIG_FILENAME="$config_path" \
  uv tool run --from swh.scanner==0.8.3 \
    swh scanner -C "$config_path" scan "$delta_dir" \
    --no-web-ui \
    --output-format json \
    --provenance \
    --disable-global-patterns \
    --disable-vcs-patterns > "$result_path" 2> "$log_path"
scan_status=$?
set -e

cat "$log_path" >&2

if (( scan_status != 0 )); then
  printf '%s\n' 'Software Heritage scanner failed before producing a result.' >&2
  exit 2
fi

if grep -Eiq 'error:|traceback|does not have permission|service unavailable|timed out' "$log_path"; then
  printf '%s\n' 'Software Heritage scanner reported a technical error; no clean provenance result is available.' >&2
  exit 2
fi

if ! python3 -m json.tool "$result_path" >/dev/null; then
  printf '%s\n' 'Software Heritage scanner did not produce valid JSON.' >&2
  exit 2
fi

python3 .github/swh-provenance-policy.py "$result_path" .github/swh-provenance-allowlist.json
