#!/bin/bash
#
# Helper script to execute management commands inside a Django based container
#
set -eu
set -o pipefail

: "${QUIET:=}"
COMPONENT_NAME="{{ COMPONENT_NAME }}"

candidate_pods=$(kubectl get pods -l "app.kubernetes.io/name=${COMPONENT_NAME}" -o json)

# If we have no pods, then bail early
if [ "$(jq '.items | length' <<< "${candidate_pods}")" -eq 0 ];
then
  echo "No pods found for ${COMPONENT_NAME}" >&2
  exit 1
fi

# Find the first running & healthy pod
target_pod=$(jq -r '
  [.items[]
   | select(.metadata.deletionTimestamp == null)
   | select(any(.status.conditions[]?; .type == "Ready" and .status == "True"))
   | .metadata.name]
  | sort | first // empty
' <<< "${candidate_pods}")

if [ -z "${target_pod}" ];
then
  echo "No ready pods found for ${COMPONENT_NAME}" >&2
  kubectl get pods -l "app.kubernetes.io/name=${COMPONENT_NAME}" >&2
  exit 2
fi

[ -n "${QUIET}" ] || echo "Using ${target_pod}" >&2

# We allocate a terminal for `shell` as it is interactive, everything else is assumed
# to be non-interactive, but can have a tty allocated by passing `-t`
exec_flags=(-i)
if [ "${1:-}" == "-t" ];
then
  exec_flags+=(-t)
  shift # Do not pass this flag into exec
elif [ "${1:-}" == "shell" ]; then
  exec_flags+=(-t)
fi

exec kubectl exec "${exec_flags[@]}" "${target_pod}" -- launcher ./manage.py "$@"
