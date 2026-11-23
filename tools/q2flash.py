#!/usr/bin/env python3
"""Install an update.tar on a Q2 over adb, with no one at its screen (docs/adb.md#installing-an-update-over-adb).

    python3 tools/q2flash.py /tmp/q2-dev --dry-run    # everything but the install: push, verify, the Q2's own check
    python3 tools/q2flash.py /tmp/q2-dev              # ... then Software Update installs it and the Q2 restarts
    python3 tools/q2flash.py update.tar -s Q2CODA     # a file (a release's, its update.tar.sig beside it); one Q2 of several
    q2 flash /tmp/q2-dev                              # the same, through the q2 command

It refuses, before anything is copied, a file that is not one of ours (an update.tar as tools/build.py writes it, whose
build directory's manifest.json names its SHA-256, or a release's whose update.tar.sig the release key signed), a Q2
whose battery is under 30% and not charging, a Q2 that already runs that build, and a card without room. It pushes the
file to the card as update.tar.part, compares its SHA-256 on the Q2 with the local one, renames it over
/mnt/mmc/update.tar and compares that file's SHA-256 again (adb drops the rename's exit status). The install is the
Q2's own: q2update (a --dev build's, patch/q2update.sh) asks demo to run Software Update's SD card path, first as a
check, whose answer must name this build's tag, then as the install, which checks the file again, shows Restarting to
Install and runs stock's updater. On a build without q2update it stops after the push and says to press Software
Update. It never runs the flasher itself.

Exit status: 0 installed (or, with --dry-run, would install), 1 refused or failed, 3 pushed but the install needs a
press on the Q2 (no q2update there).
"""
import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import tarfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import signing  # noqa: E402

CARD = '/mnt/mmc'
TARGET = CARD + '/update.tar'
PART = TARGET + '.part'
BATTERY = '/sys/devices/i2c-0/0-0062/cw2015_capacity'  # the gauge's percent, as q2guard reads it
STATUS = '/sys/class/power_supply/battery/status'
MIN_BATTERY = 30
CHARGING = {'Charging', 'Full'}
ROOM = 100 << 20  # stock's updater unpacks beside the tar and refuses under 0.1 GB free (check_updatefw_exist)
RELEASE_TAG = re.compile(r'V([0-9])([0-9])([0-9])[0-9I]')  # tools/build.py TAG: 1.8.2 is V1820, its beta 2 V1822
DEV_TAG = re.compile(r'V[0-9A-Z]{3}i')  # tools/build.py dev_tag
DOWN_S, UP_S = 120, 900  # the Q2 leaving adb once stock's updater runs; back after the recovery flashes


class Refused(Exception):
    pass


def ours(path):
    """path's update.tar if it is one of ours: {'tag', 'sha256', 'bytes', 'source'}, source 'build' (a manifest.json
    beside it names its SHA-256 and tag) or 'release' (its update.tar.sig checks against the release key). Else
    Refused, saying why."""
    path = pathlib.Path(path)
    if path.is_dir():
        path = path/'update.tar'
    if not path.is_file():
        raise Refused(f'no update.tar at {path}')
    try:
        with tarfile.open(path) as t:
            members = t.getmembers()
            if not members or members[0].name != 'firmware_v20.info':
                raise Refused('not a Q2 update: firmware_v20.info is not its first member')
            head = t.extractfile(members[0])
            if head is None:
                raise Refused('not a Q2 update: firmware_v20.info is not a file')
            info = head.read().decode(errors='replace').splitlines()
            if len(info) < 4 or info[0].strip() != 'Shanling Q2':
                raise Refused('not a Q2 update: its firmware_v20.info is not Shanling Q2\'s')
            tag = info[1].strip()
            if not (RELEASE_TAG.fullmatch(tag) or DEV_TAG.fullmatch(tag)):
                raise Refused(f'not a build of ours: version {tag!r} (stock\'s, or another firmware\'s)')
            for line, name in ((info[2], 'recovery-update/xImage'), (info[3], 'recovery-update/rootfs.squashfs')):
                member = next((m for m in members if m.name == name), None)
                if member is None:
                    raise Refused(f'damaged: no {name}')
                md5 = hashlib.md5()
                f = t.extractfile(member)
                if f is None:
                    raise Refused(f'damaged: {name} is not a file')
                for chunk in iter(lambda: f.read(1 << 20), b''):
                    md5.update(chunk)
                if line[:32] != md5.hexdigest():
                    raise Refused(f'damaged: {name} does not match its MD5')
    except (tarfile.TarError, OSError, EOFError) as e:
        raise Refused(f'not a readable tar: {e}') from e
    data = path.read_bytes()
    found = {'tag': tag, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
    manifest = path.parent/'manifest.json'
    if manifest.is_file():
        try:
            m = json.loads(manifest.read_text())
        except ValueError:
            m = {}
        if m.get('update_sha256') == found['sha256'] and m.get('version') == tag:
            return {**found, 'source': 'build'}
    sig, release = path.parent/(path.name + '.sig'), RELEASE_TAG.fullmatch(tag)
    if sig.is_file() and release:
        if signing.verify(signing.update_message('.'.join(release.groups()), path), sig.read_bytes()):
            return {**found, 'source': 'release'}
        raise Refused(f'{sig.name} does not check against the release key')
    raise Refused('not a build of ours: no manifest.json beside it naming this file, and no release signature')


def battery_ok(level, status, minimum=MIN_BATTERY):
    return level >= minimum or status in CHARGING


class Adb:
    """adb with the serial, if given. Commands on the Q2 go through exec-out, whose exit status adb drops: what they
    print is the answer."""
    def __init__(self, serial=None, binary='adb'):
        self.base = [binary, *(['-s', serial] if serial else [])]

    def run(self, *args, timeout=60):
        r = subprocess.run([*self.base, *args], capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout

    def sh(self, command, timeout=60):
        return self.run('exec-out', command, timeout=timeout)[1]

    def state(self):
        try:
            return self.run('get-state', timeout=10)[1].strip()
        except subprocess.TimeoutExpired:
            return ''


def q2update(adb, op, timeout=300):
    """q2update's answer as its words (NONCE WORD INSTALLED UPDATE), or None when the Q2 has no q2update."""
    # if, not && ... ||: a refusal is q2update's exit status 1, which must not read as no q2update
    out = adb.sh(f'if command -v q2update >/dev/null; then q2update {op} 2>&1; else echo q2update-missing; fi',
                 timeout=timeout)
    if 'q2update-missing' in out:
        return None
    return (out.strip().splitlines() or [''])[-1].split()


def say(text):
    print(text, flush=True)


def wait_for(adb, gone, seconds):
    """Until the Q2 has left adb (gone) or is back on it, at most seconds."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if (adb.state() != 'device') == gone:
            return True
        time.sleep(2)
    return False


def flash(update, adb, dry_run=False, minimum=MIN_BATTERY, wait=True):
    found = ours(update)
    path = pathlib.Path(update)
    path = path/'update.tar' if path.is_dir() else path
    say(f'{path}: {found["tag"]}, {found["bytes"] / 1e6:.1f} MB, a {found["source"]} of ours')
    if adb.state() != 'device':
        raise Refused('no Q2 on adb (adb devices: USB Debugging on the Q2, the computer allowed)')
    words = q2update(adb, 'version', timeout=30)
    if words is not None and len(words) < 3:
        raise Refused(f'q2update version gave no answer ({" ".join(words) or "nothing"}): is demo running?')
    installed = words[2] if words else None
    if installed == found['tag']:
        raise Refused(f'the Q2 already runs {installed}')
    level_text = adb.sh(f'cat {BATTERY} 2>/dev/null').strip()
    status = adb.sh(f'cat {STATUS} 2>/dev/null').strip()
    if not level_text.isdigit():
        raise Refused(f'cannot read the battery ({BATTERY})')
    level = int(level_text)
    say(f'battery {level}% {status or "(no status)"}; installed {installed or "(a build without q2update)"}')
    if not battery_ok(level, status, minimum):
        raise Refused(f'battery {level}% and not charging: needs {minimum}% or a charger')
    if f' {CARD} ' not in adb.sh('cat /proc/mounts'):
        raise Refused('the card is not mounted on the Q2 (lent to a computer? press Return on USB Connected)')
    df = (adb.sh(f'df -k {CARD} 2>/dev/null').strip().splitlines() or [''])[-1].split()
    old = adb.sh(f'[ -f {TARGET} ] && wc -c < {TARGET}').strip()
    try:
        free = int(df[-3]) << 10  # the last line's Available Use% Mounted-on (busybox puts a long device on its own line)
    except (ValueError, IndexError):
        raise Refused(f'cannot read the card\'s free space: {" ".join(df)!r}') from None
    if free + (int(old) if old.isdigit() else 0) - found['bytes'] < ROOM:
        raise Refused(f'not enough room on the card: {free >> 20} MB free, needs {(found["bytes"] + ROOM) >> 20} MB')
    say(f'pushing to {PART}')
    code, out = adb.run('push', str(path), PART, timeout=900)
    if code:
        adb.sh(f'rm -f {PART}')
        raise Refused(f'adb push failed: {out.strip()}')
    remote = (adb.sh(f'sha256sum {PART}', timeout=300).split() or [''])[0]
    if remote != found['sha256']:
        adb.sh(f'rm -f {PART}')
        raise Refused(f'the copy on the Q2 differs (sha256 {remote or "unreadable"}, expected {found["sha256"]}): removed')
    adb.sh(f'mv -f {PART} {TARGET} && sync')
    # the rename prints nothing either way: read back the file the install will take, not the .part just checked
    final = (adb.sh(f'sha256sum {TARGET}', timeout=300).split() or [''])[0]
    if final != found['sha256']:
        adb.sh(f'rm -f {PART}')
        raise Refused(f'the rename to {TARGET} failed: it holds sha256 {final or "(no file)"}, not this build\'s; '
                      'nothing installed')
    say(f'{TARGET} on the Q2, sha256 {final} checked')
    if words is None:
        say('this Q2\'s build has no q2update: press Settings > General > Software Update on it to install')
        return 3
    say('q2update check: the Q2 checks the file on screen')
    answer = q2update(adb, 'check')
    if not answer or len(answer) < 4 or answer[1] != 'ok':
        raise Refused(f'the Q2\'s own check refused it: {" ".join(answer or [])}')
    if answer[3] != found['tag']:
        raise Refused(f'the Q2 reads {TARGET} as {answer[3]}, not {found["tag"]}: nothing installed')
    if dry_run:
        say(f'dry run passed: {found["tag"]} would install over {installed}; nothing installed')
        return 0
    say('q2update install: the Q2 checks it once more and installs')
    answer = q2update(adb, 'install')
    word = answer[1] if answer and len(answer) > 1 else 'none'
    if word != 'installing':
        raise Refused(f'the Q2 did not start the install: {" ".join(answer or [])}')
    if len(answer) < 4 or answer[3] != found['tag']:
        raise Refused(f'the Q2 is installing {answer[3] if len(answer) > 3 else "(an unnamed file)"}, '
                      f'not {found["tag"]}: look at its screen')
    say('installing: Restarting to Install on the Q2; it restarts and the recovery flashes (about 2 min)')
    if not wait:
        return 0
    if not wait_for(adb, True, DOWN_S):
        ack = adb.sh('cat /tmp/q2update.ack 2>/dev/null').strip()
        raise Refused(f'the Q2 did not restart within {DOWN_S} s (last answer: {ack or "none"})')
    say('restarting; waiting for it to come back on adb (USB Debugging must be picked again if the Q2 asks)')
    if not wait_for(adb, False, UP_S):
        raise Refused(f'the Q2 was not back on adb within {UP_S} s: look at its screen')
    for _ in range(30):  # demo starts some seconds after adbd
        now = q2update(adb, 'version', timeout=30)
        if now and len(now) > 2:
            break
        time.sleep(2)
    else:
        now = None
    if not now or now[2] != found['tag']:
        raise Refused(f'back, but running {now[2] if now else "(no answer)"}, not {found["tag"]}')
    say(f'installed: the Q2 runs {found["tag"]}')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('update', help='update.tar, or a build directory holding one')
    ap.add_argument('--dry-run', action='store_true', help='push and check on the Q2, but do not install')
    ap.add_argument('-s', '--serial', help='the Q2\'s adb serial, with more than one device')
    ap.add_argument('--min-battery', type=int, default=MIN_BATTERY, help='least charge without a charger (%(default)s)')
    ap.add_argument('--no-wait', action='store_true', help='return once the install has started')
    ap.add_argument('--adb', default='adb', help='the adb program (%(default)s)')
    args = ap.parse_args(argv)
    try:
        return flash(args.update, Adb(args.serial, args.adb), args.dry_run, args.min_battery, not args.no_wait)
    except Refused as e:
        print(f'q2flash: refused: {e}', file=sys.stderr)
        return 1
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f'q2flash: adb: {e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
