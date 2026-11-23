#!/usr/bin/env python3
"""The files-on-card contract between the firmware and the Subsonic app it carries (BUNDLED_APPS), checked against
the pinned build: `python3 test/subsonic_contract.py [--zip ZIP] [--repo Q2_SUBSONIC]`.

Firmware and app agree only through words and files (docs/internals.md#card-apps,
docs/server-library.md#download-ahead-and-the-streaming-state): the caps line demo writes to /tmp/q2-addon.caps
(patch/addon.c ADDON_CAPS, plus stream_state.c ahead_caps' " ahead grow"), and the /tmp/q2-<id>.* files one side
writes and the other reads (.catplay, .playing, .ahead, .stream). Checked here, with no build and no emulator:

- the pinned zip (~/.cache/q2-apps, or --zip) is BUNDLED_APPS' by size and SHA-256, and its name's commit is in
  q2-subsonic's repository (--repo, ~/repos/q2-subsonic by default);
- every caps word that commit's internal/fw/caps.go acts on is one this firmware writes (an app word the firmware
  lacks is a feature the app waits for in vain), and Download Ahead's two ("ahead", "grow") among them;
- each /tmp/q2-<id> file the firmware writes or reads is named in that commit's cmd/q2-subsonic sources and in the
  zip's program itself (its bytes hold the name), so a rename on either side fails here, not on a drive.

Without the zip or the repository it says so and exits 0 (a machine with neither has nothing to check against).
The app's behaviour under the contract (Download Ahead's plan under Shuffle and Repeat All, a list that starts on a
song not yet downloaded) is q2-subsonic's own tests; this checks that both sides still speak the same names.
"""
import argparse
import hashlib
import io
import pathlib
import re
import subprocess
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from build import APPS_DIR, BUNDLED_APPS  # noqa: E402

# The card files of one app, by what the firmware does with each (the source that writes or reads it).
FILES = {'catplay': 'patch/catalogue.c', 'playing': 'patch/addon.c', 'ahead': 'patch/stream_state.c',
         'stream': 'patch/stream_state.c'}


def firmware_caps(root=ROOT):
    """The words demo's caps line holds: ADDON_CAPS and the words ahead_caps adds while q2player plays."""
    addon = (root/'patch/addon.c').read_text()
    body = re.search(r'#define ADDON_CAPS\s*\\?\s*((?:"[^"]*"\s*\\?\s*)+)', addon)
    assert body, 'patch/addon.c: no ADDON_CAPS'
    words = set(''.join(re.findall(r'"([^"]*)"', body[1])).split())
    extra = re.search(r'return ss\.grow \? "([^"]*)" : "([^"]*)";', (root/'patch/stream_state.c').read_text())
    assert extra, 'patch/stream_state.c: ahead_caps\'s words not found'
    return words | set(extra[1].split()) | set(extra[2].split())


def app_caps(caps_go):
    """The caps words internal/fw/caps.go acts on (its switch's cases)."""
    return set(re.findall(r'case "([a-z]+)":', caps_go))


def pinned(app_id='subsonic', apps=APPS_DIR):
    """The pinned zip's bytes and the commit its name carries, or (None, why)."""
    _, name, size, digest, _ = BUNDLED_APPS[app_id]
    path = pathlib.Path(apps)/name
    if not path.is_file():
        return None, f'{path} is not here'
    data = path.read_bytes()
    assert len(data) == size and hashlib.sha256(data).hexdigest() == digest, f'{path} is not the pinned zip'
    commit = re.search(r'-([0-9a-f]{7,40})\.zip$', name)
    assert commit, f'{name} carries no commit'
    return data, commit[1]


def git_show(repo, commit, path):
    return subprocess.run(['git', '-C', str(repo), 'show', f'{commit}:{path}'], capture_output=True, text=True,
                          check=True).stdout


def check(data, commit, repo, app_id='subsonic', root=ROOT):
    """Every check above; returns a line saying what was compared."""
    fw = firmware_caps(root)
    have = app_caps(git_show(repo, commit, 'internal/fw/caps.go'))
    missing = sorted(have - fw)
    assert not missing, f'q2-subsonic {commit} acts on caps words this firmware never writes: {missing}'
    assert {'ahead', 'grow'} <= have & fw, 'Download Ahead\'s words are not on both sides'
    names = subprocess.run(['git', '-C', str(repo), 'ls-tree', '--name-only', commit, 'cmd/q2-subsonic/'],
                           capture_output=True, text=True, check=True).stdout.split()
    sources = ''.join(git_show(repo, commit, n) for n in names if n.endswith('.go') and not n.endswith('_test.go'))
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        program = z.read(f'.{app_id}/q2-{app_id}')
    for suffix, side in FILES.items():
        fw_text = (root/side).read_text()
        assert f'/tmp/q2-%s.{suffix}' in fw_text, f'{side} no longer names /tmp/q2-<id>.{suffix}'
        assert f'q2-{app_id}.{suffix}' in sources or f'"+appID+".{suffix}' in sources.replace(' ', ''), \
            f'q2-subsonic {commit} does not name /tmp/q2-{app_id}.{suffix}, which {side} uses'
        assert f'.{suffix}'.encode() in program, f'the pinned program holds no ".{suffix}"'
    assert b'q2-addon.caps' in program, 'the pinned program does not read /tmp/q2-addon.caps'
    return (f'q2-subsonic {commit}: {len(have)} caps words, all written by this firmware ({len(fw)}); '
            f'{", ".join("." + s for s in FILES)} named on both sides and in the program')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--zip', type=pathlib.Path, help='a q2-subsonic zip in place of the pinned one')
    ap.add_argument('--repo', type=pathlib.Path, default=pathlib.Path.home()/'repos/q2-subsonic')
    args = ap.parse_args(argv)
    if args.zip:
        data, commit = args.zip.read_bytes(), re.search(r'-([0-9a-f]{7,40})\.zip$', args.zip.name)[1]
    else:
        data, commit = pinned()
        if data is None:
            print(f'skipped: {commit}')
            return 0
    if not (args.repo/'.git').exists():
        print(f'skipped: no q2-subsonic repository at {args.repo}')
        return 0
    print(check(data, commit, args.repo))
    return 0


if __name__ == '__main__':
    sys.exit(main())
