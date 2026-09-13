#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${SCANOSS_REAL_DOCKER:-}" || -z "${SCANOSS_RUNTIME_IMAGE:-}" ]]; then
  printf '%s\n' 'SCANOSS_REAL_DOCKER and SCANOSS_RUNTIME_IMAGE are required' >&2
  exit 2
fi

args=("$@")
runtime_index=-1
scan_index=-1

for ((index = 0; index < ${#args[@]}; index++)); do
  if [[ "${args[index]}" == "$SCANOSS_RUNTIME_IMAGE" ]]; then
    runtime_index=$index
    break
  fi
done

if (( runtime_index >= 0 )); then
  for ((index = runtime_index + 1; index < ${#args[@]}; index++)); do
    if [[ "${args[index]}" == 'scan' ]]; then
      scan_index=$index
      break
    fi
  done
fi

if (( scan_index >= 0 )); then
  args+=(--all-hidden --all-extensions --all-folders)
fi

exec "$SCANOSS_REAL_DOCKER" "${args[@]}"
