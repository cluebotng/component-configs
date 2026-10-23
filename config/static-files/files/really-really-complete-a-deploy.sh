#!/bin/bash
set -uo pipefail

get_deploy_id() { toolforge components deployment show --json 2>/dev/null | jq -r ".deploy_id"; }
get_status() { toolforge components deployment show --json 2>/dev/null | jq -r ".status"; }

while true;
do
  previous_id="$(get_deploy_id)"
  toolforge components deployment create || { sleep 30; continue; }
  while true; do
    sleep 10
    [[ "$(get_deploy_id)" == "$previous_id" ]] && continue
    status="$(get_status)"
    echo "[$(date)] $status"
    case "$status" in
      successful) exit 0 ;;
      running|pending|"") ;;
      *) break ;;
    esac
  done
done