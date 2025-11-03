#!/bin/bash
#
# Helper script to copy all worker credentials into an env var
#
set -eu
set -o pipefail

credentials=""
for i in {1..10}
do
    username="tools.cluebotng-worker-$i"
    echo "Fetching credentials for ${username}"
    user=$(/usr/bin/sudo -u "${username}" -- toolforge envvars show --raw TOOL_REPLICA_USER)
    password=$(/usr/bin/sudo -u "${username}" -- toolforge envvars show --raw TOOL_REPLICA_PASSWORD)

    [[ -z "${credentials}" ]] || credentials="${credentials}, "
    credentials="${credentials}{\"user\": \"${user}\", \"pass\": \"${password}\"}"
done

echo "Updating CBNG_BOT_MYSQL_CREDENTIALS"
toolforge envvars create CBNG_BOT_MYSQL_CREDENTIALS <<< "[${credentials}]" > /dev/null
