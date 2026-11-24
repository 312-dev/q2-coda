#!/usr/bin/env python3
"""The seam test: the Subsonic app the firmware carries, run for real against a fake server, with the firmware's own
C on the other side of every file they share (test/fw/test_seam_subsonic.py, docs/building.md#seam-test).

    python3 test/subsonic_seam.py                       # the pinned app (BUNDLED_APPS' zip, its commit built here)
    python3 test/subsonic_seam.py --app ~/src/q2-subsonic   # an app checkout as it is, before its zip is cut
    python3 test/subsonic_seam.py --commit cef5763      # a commit of the app repository
    python3 test/subsonic_seam.py --mips QEMU           # the zip's own MIPS program, under qemu-mipsel
    python3 test/subsonic_seam.py -- -k radio           # pytest's options after --

It builds two programs for this machine, each once per commit (~/.cache/q2-seam/build/): the app under test, and
q2-fakesonic from the q2-subsonic commit named in FAKESONIC (the harness, which an older app commit lacks). Then it
runs the scenarios under pytest with this python, which must be a PIE build as test/fw needs. Each test runs the
app and the fake server in a user, mount and network namespace of their own (unshare), so it touches no /tmp or
port of this machine's. Without the pinned zip or the app repository it says so and exits 0, as
test/subsonic_contract.py does.
"""
import argparse
import hashlib
import io
import os
import shutil
import pathlib
import re
import subprocess
import sys
import tarfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from build import APPS_DIR, BUNDLED_APPS  # noqa: E402

CACHE = pathlib.Path.home()/'.cache/q2-seam'
# q2-subsonic's commit whose cmd/q2-fakesonic the tests drive (its branch test/fakesonic-rate, on the fork, after
# test/seam-harness). A change to the fake's control calls moves this pin with the test that uses them.
FAKESONIC = '44d1840'
REPO = pathlib.Path.home()/'repos/q2-subsonic'


def pinned_zip():
    """BUNDLED_APPS' subsonic zip, checked, and its commit; or (None, why)."""
    _, name, size, digest, _ = BUNDLED_APPS['subsonic']
    path = pathlib.Path(APPS_DIR)/name
    if not path.is_file():
        return None, f'{path} is not here'
    data = path.read_bytes()
    assert len(data) == size and hashlib.sha256(data).hexdigest() == digest, f'{path} is not the pinned zip'
    return path, zip_commit(path)


def zip_commit(path):
    """The commit a zip was cut from: its name's (q2-subsonic-vX.Y-SHA.zip), which its version file, when it has
    one (q2-vX.Y+SHA), must agree with."""
    m = re.search(r'-([0-9a-f]{7,40})\.zip$', path.name)
    assert m, f'{path.name} carries no commit'
    with zipfile.ZipFile(path) as z:
        if '.subsonic/version' in z.namelist():
            stamp = z.read('.subsonic/version').decode().strip()
            assert stamp.endswith('+' + m[1]), f'{path.name} says {stamp}'
    return m[1]


def go_build(src, package, out):
    """A static host build of src's package into out."""
    out.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, CGO_ENABLED='0')
    subprocess.run(['go', 'build', '-trimpath', '-o', str(out), package], cwd=src, env=env, check=True)
    return out


def at_commit(repo, commit, package, name):
    """package built from repo at commit, the tree exported once (git archive), the build kept by commit."""
    full = subprocess.run(['git', '-C', str(repo), 'rev-parse', '--short=7', commit + '^{commit}'], capture_output=True,
                          text=True, check=True).stdout.strip()
    out = CACHE/'build'/full/name
    if out.is_file():
        return out, full
    src = CACHE/'src'/full
    if not (src/'go.mod').is_file():
        src.mkdir(parents=True, exist_ok=True)
        tar = subprocess.run(['git', '-C', str(repo), 'archive', full], capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(tar)) as t:
            t.extractall(src, filter='data')
    return go_build(src, package, out), full


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    which = ap.add_mutually_exclusive_group()
    which.add_argument('--app', type=pathlib.Path, help='an app checkout, built as its tree is')
    which.add_argument('--commit', help='a commit of --repo to build')
    ap.add_argument('--zip', type=pathlib.Path, help='the zip whose card files (q2app, run) the app runs with: '
                    '--commit\'s own in ~/.cache/q2-apps when there is one, else the pinned one')
    ap.add_argument('--mips', metavar='QEMU', help='run the zip\'s own program under this qemu-mipsel, not a host build')
    ap.add_argument('--repo', type=pathlib.Path, default=REPO, help='the q2-subsonic repository (default %(default)s)')
    ap.add_argument('pytest', nargs='*', help='pytest\'s options, after --')
    args = ap.parse_args(argv)
    if args.commit and not args.zip:  # that commit's own zip, when there is one, for its card files
        args.zip = next(iter(sorted(pathlib.Path(APPS_DIR).glob(f'q2-subsonic-*-{args.commit[:7]}.zip'))), None)
    zip_path, commit = (args.zip, zip_commit(args.zip)) if args.zip else pinned_zip()
    if zip_path is None:
        print(f'skipped: {commit}')
        return 0
    if not (args.repo/'.git').exists():
        print(f'skipped: no q2-subsonic repository at {args.repo}')
        return 0
    fake, _ = at_commit(args.repo, FAKESONIC, './cmd/q2-fakesonic', 'q2-fakesonic')
    if args.mips:
        program = CACHE/'build'/f'zip-{commit}'/'q2-subsonic'
        program.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as z:
            program.write_bytes(z.read('.subsonic/q2-subsonic'))
        what = f'{zip_path.name}\'s MIPS program under {args.mips}'
    elif args.app:
        program = go_build(args.app.resolve(), './cmd/q2-subsonic', CACHE/'build'/'checkout'/'q2-subsonic')
        what = f'{args.app} as it is'
    else:
        program, commit = at_commit(args.repo, args.commit or commit, './cmd/q2-subsonic', 'q2-subsonic')
        what = f'q2-subsonic {commit}, a host build'
    print(f'seam: {what}; card files from {zip_path.name}; q2-fakesonic {FAKESONIC}', flush=True)
    env = dict(os.environ, Q2_SEAM_APP=str(program), Q2_SEAM_FAKE=str(fake), Q2_SEAM_ZIP=str(zip_path),
               Q2_SEAM_QEMU=args.mips or '')
    base = CACHE/'pytest'/str(os.getpid())  # test/fw's library build, on disk; gone after the run
    base.parent.mkdir(parents=True, exist_ok=True)
    try:
        return subprocess.run([sys.executable, '-m', 'pytest', str(ROOT/'test/fw/test_seam_subsonic.py'), '-q', '-p',
                               'no:cacheprovider', '--basetemp', str(base), '--durations=0', *args.pytest],
                              cwd=ROOT, env=env).returncode
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
