#!/bin/sh
set -eu

mkdir -p /tmp/aiapp/logs
nvidia-smi --query-gpu=name,driver_version --format=csv,noheader > /tmp/aiapp/logs/gpu-smoke-result.txt
