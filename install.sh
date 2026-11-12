#!/bin/sh
# POSIX shell と PowerShell で共通のインストーラーを使う。
set -eu
exec python3 "$(dirname "$0")/install.py" "$@"
