#!/bin/sh
set -eu

mkdir -p /tmp/aiapp/logs
printf 'cpu-smoke-ok\n' > /tmp/aiapp/logs/cpu-smoke-result.txt
