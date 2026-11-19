#!/usr/bin/env python3
"""Fail when tools/stock_sites.tsv is not current: runs docs/rebasing/sitenotes.py --check against the stock
firmware zip ($Q2_STOCK_ZIP, default ~/q2-stock/Q2 Firmware V1.36.zip). A site a branch adds to tools/build.py or
patch/ needs its row; fix a failure with `python3 -I docs/rebasing/sitenotes.py ZIP` and commit the table. Needs
no build and takes about 12 s (llvm-objdump)."""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
ZIP = pathlib.Path(os.environ.get('Q2_STOCK_ZIP', pathlib.Path.home()/'q2-stock/Q2 Firmware V1.36.zip'))

if not ZIP.exists():
    sys.exit(f'test/stock_sites.py: no stock zip at {ZIP}; set Q2_STOCK_ZIP')
sys.exit(subprocess.run([sys.executable, '-I', ROOT/'docs/rebasing/sitenotes.py', ZIP, '--check']).returncode)
