#!/bin/sh
# Format the payload's C and the Markdown docs: tools/build.py's NAVIGATION and tools/peq.py's
# UI_SOURCES but its UNFORMATTED (upstream's unformatted files and the vendored monocypher), the
# headers named below, then prettier over the docs. --check changes nothing and exits non-zero on
# drift. patch/link.ld and patch/contexts.inc have no usable formatter and are deliberately absent.
# CLANG_FORMAT and PRETTIER name other commands, such as
# CLANG_FORMAT='uvx --quiet --from clang-format==18.1.8 clang-format' (.clang-format names the version).
set -eu
cd "$(dirname "$0")/.."
clang_format=${CLANG_FORMAT:-clang-format}
prettier=${PRETTIER:-prettier}

# read from the lists, so a file added to either is formatted once it builds
c=$(python3 -c 'import sys; sys.path.insert(0, "tools"); import build, peq
print(" ".join("patch/" + f for f in build.NAVIGATION + [f for f in peq.UI_SOURCES if f not in peq.UNFORMATTED]))')
c="$c patch/navigation.h patch/theme.h patch/coverflow.h patch/playlist_art.h patch/library_scope.h patch/xiph.h patch/peq_types.h patch/q2eq.h"

if [ "${1:-}" = --check ]; then
    $clang_format --dry-run -Werror $c
    $prettier --check "*.md" "docs/*.md"
else
    $clang_format -i $c
    $prettier --write "*.md" "docs/*.md"
fi
