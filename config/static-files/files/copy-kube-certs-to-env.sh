#!/bin/bash
#
# Helper script to copy k8s client certs into an env var
#
set -eu
set -o pipefail

toolforge envvars create K8S_CLIENT_CRT < ~/.toolskube/client.crt > /dev/null
toolforge envvars create K8S_CLIENT_KEY < ~/.toolskube/client.key > /dev/null
