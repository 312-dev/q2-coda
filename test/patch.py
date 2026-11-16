#!/usr/bin/env python3
"""Execute the actual patched MIPS callback with stock filter and mocked UI services.
Requires unicorn==2.1.4. Does not emulate the entire device or flash hardware.

    python3 test/patch.py BUILD

The suite is test/patch_suite/'s files, one per area, run here in name order in this one namespace: a
later file uses the Machines, helpers and state of the earlier ones, so none runs alone
(test/suite_source.py; tools/ci/patch_shards.py runs ranges of it in parallel).
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from suite_source import parts  # noqa: E402

for _part in parts():
    exec(compile(_part.read_text(), str(_part), 'exec'))
