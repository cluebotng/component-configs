#!/bin/bash
#
# Wrapper script to access the Trove hosted schema
#
set -eu
set -o pipefail

# Similar to `replica.my.cnf`, but drive from secrets.
# Note: Not passing on the command line to avoid exposing e.g. the password
umask o-r
cat > ~/.trove.my.cnf <<EOF
[client]
host = $(toolforge envvars show --raw TOOL_DB_HOST)
user = $(toolforge envvars show --raw TOOL_DB_USER)
password = $(toolforge envvars show --raw TOOL_DB_PASSWORD)
disable-ssl=true
EOF

exec mysql --defaults-file=~/.trove.my.cnf -A "$(toolforge envvars show --raw TOOL_DB_SCHEMA)"
