#!/bin/sh
# kiroku を導入先から取り除く。処理は uninstall.py にある。
set -eu
exec python3 "$(dirname "$0")/uninstall.py" "$@"
