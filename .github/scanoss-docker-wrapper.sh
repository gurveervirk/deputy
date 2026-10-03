#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${SCANOSS_REAL_DOCKER:-}" || -z "${SCANOSS_RUNTIME_IMAGE:-}" ]]; then
  printf '%s\n' 'SCANOSS_REAL_DOCKER and SCANOSS_RUNTIME_IMAGE are required' >&2
  exit 2
fi

filter_scan_path() {
  local settings_path=$1
  local scan_path=$2

  if [[ ! -f "$settings_path" || ! -d "$scan_path" ]]; then
    return 0
  fi

  local filter_root
  filter_root="$(mktemp -d "${RUNNER_TEMP:-/tmp}/scanoss-filter.XXXXXX")"
  git -C "$filter_root" init --quiet
  python3 - "$settings_path" "$filter_root/.gitignore" <<'PY'
import json
import pathlib
import sys

settings = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
patterns = settings.get('settings', {}).get('skip', {}).get('patterns', {}).get('scanning', [])
pathlib.Path(sys.argv[2]).write_text('\n'.join(patterns) + ('\n' if patterns else ''), encoding='utf-8')
PY

  while IFS= read -r -d '' ignored; do
    relative=${ignored#./}
    if [[ -n "$relative" && "$relative" != "$ignored" ]]; then
      rm -f -- "$scan_path/$relative"
    fi
  done < <(
    (
      cd "$scan_path"
      find . -type f ! -name .gitignore -print0
    ) | (
      cd "$filter_root"
      git check-ignore --no-index -z --stdin || true
    )
  )

  rm -rf "$filter_root"
}

filter_delta() {
  local host_root=$1
  local delta_dir=$2
  filter_scan_path "$host_root/scanoss.json" "$host_root/$delta_dir"
}

args=("$@")
runtime_index=-1
scan_index=-1
delta_copy_index=-1
runner_user_operation=0

for ((index = 0; index < ${#args[@]}; index++)); do
  if [[ "${args[index]}" == "$SCANOSS_RUNTIME_IMAGE" ]]; then
    runtime_index=$index
    break
  fi
done

if (( runtime_index >= 0 )); then
  operation_index=$((runtime_index + 1))
  if [[ "${args[operation_index]:-}" == 'scan' || ( "${args[operation_index]:-}" == 'delta' && "${args[operation_index + 1]:-}" == 'copy' ) ]]; then
    runner_user_operation=1
  fi
fi

if (( runner_user_operation == 1 )) && [[ "${args[0]:-}" == 'run' ]]; then
  args=("${args[0]}" --user "$(id -u):$(id -g)" "${args[@]:1}")
  runtime_index=$((runtime_index + 2))
fi

if (( runtime_index >= 0 )); then
  for ((index = runtime_index + 1; index < ${#args[@]}; index++)); do
    if [[ "${args[index]}" == 'scan' ]]; then
      scan_index=$index
      break
    fi
    if [[ "${args[index]}" == 'delta' && "${args[index + 1]:-}" == 'copy' ]]; then
      delta_copy_index=$index
      break
    fi
  done
fi

if (( delta_copy_index >= 0 )); then
  set +e
  output="$("$SCANOSS_REAL_DOCKER" "${args[@]}")"
  status=$?
  set -e
  if (( status != 0 )); then
    printf '%s' "$output"
    exit "$status"
  fi

  host_root=''
  for ((index = 0; index + 1 < ${#args[@]}; index++)); do
    if [[ "${args[index]}" == '-v' && "${args[index + 1]}" == *:/scanoss ]]; then
      host_root=${args[index + 1]%:/scanoss}
      break
    fi
  done
  if [[ -n "$host_root" && "$output" != */* && "$output" != *..* ]]; then
    filter_delta "$host_root" "$output"
  fi
  printf '%s\n' "$output"
  exit 0
fi

scan_tree=''
cleanup_scan_tree() {
  if [[ -n "$scan_tree" && -d "$scan_tree" ]]; then
    rm -rf -- "$scan_tree"
  fi
}

if (( scan_index >= 0 )); then
  host_root=''
  for ((index = 0; index + 1 < ${#args[@]}; index++)); do
    if [[ "${args[index]}" == '-v' && "${args[index + 1]}" == *:/scanoss ]]; then
      host_root=${args[index + 1]%:/scanoss}
      break
    fi
  done

  scan_path_index=$((scan_index + 1))
  if [[ -n "$host_root" && -f "$host_root/scanoss.json" && "${args[scan_path_index]:-}" == '.' ]]; then
    scan_relative=".scanoss-source-$$"
    scan_tree="$host_root/$scan_relative"
    rm -rf -- "$scan_tree"
    mkdir -p "$scan_tree"
    if ! git -C "$host_root" archive --format=tar HEAD | tar -x -C "$scan_tree"; then
      cleanup_scan_tree
      exit 2
    fi
    filter_scan_path "$host_root/scanoss.json" "$scan_tree"
    args[scan_path_index]="$scan_relative"
    trap cleanup_scan_tree EXIT
  fi
  args+=(--retry 0 --all-hidden --all-extensions --all-folders)

  if [[ -n "$scan_tree" ]]; then
    set +e
    "$SCANOSS_REAL_DOCKER" "${args[@]}"
    status=$?
    set -e
    exit "$status"
  fi
fi

exec "$SCANOSS_REAL_DOCKER" "${args[@]}"
