#!/usr/bin/env python3
"""test/patch.py's suite: the files of test/patch_suite/, one per area, run in name order in one namespace.

    python3 test/suite_source.py LINE    # a line of the joined suite (tools/ci/patch_shards.py's numbers) as FILE:LINE

The files are one script cut in pieces: a later file uses the Machines, helpers and state of the earlier
ones, so none runs alone. test/patch.py runs them; tools/ci/patch_shards.py and tools/ci/affected.py read
the joined text (`source`), whose line numbers `locate` maps back to a file's.
"""
import pathlib
import sys

DIR = pathlib.Path(__file__).with_name('patch_suite')


def parts():
    """The suite's files in run order."""
    return sorted(DIR.glob('[0-9][0-9]_*.py'))


def source():
    """The joined suite, every file whole and in order."""
    return ''.join(p.read_text() for p in parts())


def starts():
    """{file: the joined suite's line its first line is}."""
    out, line = {}, 1
    for p in parts():
        out[p] = line
        line += p.read_text().count('\n')
    return out


def locate(line):
    """The file and its own line number for line `line` of the joined suite."""
    for p, first in reversed(list(starts().items())):
        if line >= first:
            return p, line - first + 1
    raise ValueError(line)


if __name__ == '__main__':
    p, n = locate(int(sys.argv[1]))
    print(f'{p.relative_to(DIR.parent.parent)}:{n}')
