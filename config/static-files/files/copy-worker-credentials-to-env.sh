#!/bin/bash
#
# Helper script to copy all worker credentials into an env var
#
# Usage: copy-worker-credentials-to-env [max]
#   max - optional limit on the number of credentials to include
#
set -eu
set -o pipefail

max_credentials="${1:-0}"
if ! [[ "$max_credentials" =~ ^[0-9]+$ ]];
then
    echo "Invalid max value: ${max_credentials}" >&2
    exit 1
fi

worker_accounts=$(
    ldapsearch \
        -x -LL \
        -b 'ou=people,ou=servicegroups,dc=wikimedia,dc=org' \
        'uid=tools.cluebotng-worker-*' 'uid' | \
        awk '/^uid: /{sub(/^uid: /, ""); print}' | \
        sort -V
)

all_credentials=""
credential_count=0
for username in ${worker_accounts}
do
    if [[ "$max_credentials" -gt 0 ]] && [[ "$credential_count" -ge "$max_credentials" ]];
    then
        echo "Reached max of ${max_credentials} credentials, skipping ${username}"
        continue
    fi

    echo "Fetching credentials for ${username}"
    user_envvars=$(
        /usr/bin/sudo -u "${username}" -- \
            toolforge envvars list --json | \
            jq '[.envvars[] | select(.name | IN("TOOL_REPLICA_USER","TOOL_REPLICA_PASSWORD"))]
                | map({(.name): .value})
                | add'
    )

    database_username=$(jq -r '.TOOL_REPLICA_USER' <<< "$user_envvars")
    database_password=$(jq -r '.TOOL_REPLICA_PASSWORD' <<< "$user_envvars")
    if [[ -n "$database_username" ]] && [[ -n "$database_password" ]];
    then
        [[ -z "${all_credentials}" ]] || all_credentials="${all_credentials}, "
        all_credentials="${all_credentials}{\"user\": \"${database_username}\", \"pass\": \"${database_password}\"}"
        credential_count=$((credential_count + 1))
    fi
done

if [[ -n "$all_credentials" ]];
then
    echo "Updating CBNG_BOT_MYSQL_CREDENTIALS"
    jq -c '. | sort_by(.name)' <<< "[${all_credentials}]" | \
    toolforge envvars create CBNG_BOT_MYSQL_CREDENTIALS > /dev/null
fi
