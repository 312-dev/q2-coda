#!/usr/bin/env python3
"""Vamp API 1's conformance (docs/platform/README.md#conformance): each protocol page's transcripts against its real
service, through each suite's test_conformance, and the Library's page against a scan (test/libd.py). Run it as
python3 test/api.py; the same environment variables as the suites build other copies of the services."""
import pathlib
import sys

import pytest

T = pathlib.Path(__file__).resolve().parent
sys.exit(pytest.main([str(T/'playd.py'), str(T/'sysd.py'), str(T/'netd.py'), str(T/'libd.py'), str(T/'launch.py'), '-q', '-p', 'no:cacheprovider',
                      '-k', 'conformance']))
