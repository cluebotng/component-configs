#!/bin/bash
#
# Helper script to copy all worker credentials into an env var
#
set -eu
set -o pipefail

worker_accounts=$(
    ldapsearch \
        -x -LL \
        -b 'ou=people,ou=servicegroups,dc=wikimedia,dc=org' \
        'uid=tools.cluebotng-worker-*' 'uid' | \
        awk '/^uid: /{sub(/^uid: /, ""); print}'
)

all_credentials=""
for username in ${worker_accounts}
do
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
    fi
done

if [[ -n "$all_credentials" ]];
then
    echo "Updating CBNG_BOT_MYSQL_CREDENTIALS"
    jq -c '. | sort_by(.name)' <<< "[${all_credentials}]" | \
    toolforge envvars create CBNG_BOT_MYSQL_CREDENTIALS > /dev/null
fi
