#!/bin/sh
# Locate Python at runtime; exec preserves the hook's stdin/stdout and exit code.
set -u
plugin_root=${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-}}
if [ -z "$plugin_root" ]; then
    plugin_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd) || exit 2
fi
guard_path="$plugin_root/scripts/ssn_guard.py"
if [ ! -f "$guard_path" ]; then
    echo 'ssn-guard: guard script unavailable; blocked for safety' >&2
    exit 2
fi
for interpreter in python3 python; do
    if command -v "$interpreter" >/dev/null 2>&1 &&
       "$interpreter" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
        exec "$interpreter" "$guard_path"
    fi
done
echo 'ssn-guard: working Python 3.10+ unavailable; blocked for safety' >&2
exit 2
