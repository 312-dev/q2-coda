#!/bin/sh
# btrelay.sh VERB [ARGS]: the laptop's half of the Bluetooth relay, run from WSL (docs/bt-relay.md#on-the-laptop).
# Copies win/ to a Windows folder, builds btrelay.exe there when the source changed, and runs it with the
# arguments given. Recordings named without a folder (--wav NAME) land in that folder, $BTRELAY_DIR.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
dir=${BTRELAY_DIR:-/mnt/c/Users/Public/vamp-btrelay}
command -v powershell.exe >/dev/null || { echo "btrelay: no powershell.exe: run this in WSL on the Windows laptop" >&2; exit 2; }
mkdir -p "$dir"
for f in BtRelay.cs build.ps1; do
    cmp -s "$here/win/$f" "$dir/$f" || { cp "$here/win/$f" "$dir/$f"; stale=1; }
done
cd "$dir"  # Windows cannot start a program from a folder it has no path for (a network mount in WSL)
if [ -n "${stale:-}" ] || [ ! -x btrelay.exe ]; then
    # a failed build empties the copy, so the next run builds again and never runs an exe older than its source
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w build.ps1)" >&2 || {
        : > BtRelay.cs
        exit 2
    }
fi
exec ./btrelay.exe "$@"
