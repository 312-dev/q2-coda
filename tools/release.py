#!/usr/bin/env python3
"""Package the release locally; optionally upload and publish it, verified."""
import argparse
import hashlib
import importlib.util
import itertools
import json
import os
import pathlib
import re
import subprocess
import sys
import tarfile
import tempfile
import shelf
import signing
from signing import PUBLIC_KEY, public_key
from build import ROOT, REPO, VERSION, BETA, TAG, ZIP_SHA, DEMO_SHA, BUNDLED_APPS, BUNDLED_DIR, build, check, need, run, sha, source_sha256, version_ok

# Network Update (patch/netupdate.c) fetches releases/download/<tag>/<ASSET> and <SIGNATURE>, so the
# names can't carry the version.
ASSET = 'update.tar'
SIGNATURE = ASSET + '.sig'  # the release key's signature of signing.update_message (docs/releasing.md#signing-a-release)
# Rebuilt from docs/manual every release; the README links releases/latest/download/<GUIDE>.
GUIDE = 'Q2-Muse-User-Guide.pdf'


def notes():
    """This version's entry from docs/changelog.md, so a release edits only VERSION (BETA for a pre-release) and the changelog:
    its bold version heading, then one bullet a sentence, or its nested bullets as written."""
    prefix = f'- **{VERSION}**'  # entries up to V1.0 read **V1.0**, up to V7.6 **V7.6R / V7.6I**
    lines = (ROOT/'docs/changelog.md').read_text().splitlines()
    i = need(next((i for i, l in enumerate(lines) if l.startswith(prefix)), None), f'docs/changelog.md has no {prefix} entry')
    check(not any(l.startswith('- **') for l in lines[:i]), f"{VERSION} is not docs/changelog.md's newest entry")
    title, text = lines[i][2:].split(':', 1)
    if not text.strip():  # a nested entry: its indented bullets, one level out
        return '\n'.join([title, *(l[2:] for l in itertools.takewhile(lambda l: l.startswith('  '), lines[i+1:]))])
    return '\n'.join([title, *('- '+s for s in re.split(r'(?<=[.!?]) +(?=[A-Z*"]|iPod)', text.strip()))])


def release_body(text, assets, apps=None):
    """The version heading and its bullets (notes()) plus the SHA-256 block used by previous releases, with a line for
    each card app the image carries (bundled_apps()): its zip, named for the commit it was built from."""
    carried = [f'- {app["zip"]} (carried as {BUNDLED_DIR}/{app_id}): `{app["sha256"]}`'
               for app_id, app in sorted((apps or {}).items())]
    return '\n'.join([text, '', 'SHA-256:', f'- {ASSET} ({TAG}): `{assets[ASSET]}`', *carried]) + '\n'


def bundled_apps(manifest):
    """A build's card apps (manifest.json bundled_apps) as release.json records them, each checked against its pin in
    BUNDLED_APPS: the zip and its SHA-256, the program's SHA-256 in the image and the zip's version stamp."""
    apps = manifest.get('bundled_apps') or {}
    check(set(apps) == set(BUNDLED_APPS), f'The build carries {sorted(apps)}, not the pinned {sorted(BUNDLED_APPS)}')
    out = {}
    for app_id, (_, zip_name, _, digest, _) in BUNDLED_APPS.items():
        app = apps[app_id]
        check(app.get('zip') == zip_name and app.get('sha256') == digest and app.get('binary_sha256'),
              f'The build\'s {app_id} is not the pinned {zip_name}')
        out[app_id] = {k: app[k] for k in ('zip', 'sha256', 'binary_sha256', 'build')}
    return out


def validate(directory):
    m = json.loads((directory/'manifest.json').read_text())
    check(m['version'] == TAG, 'Wrong build identity')
    check(m['update_repo'] == REPO, f"Network Update fetches {m['update_repo']}'s releases, not {REPO}'s")
    check(m.get('signing_key') == public_key(PUBLIC_KEY.read_bytes()).hex(),
          f'Built with another release key than {PUBLIC_KEY.relative_to(ROOT)}')
    check(m['input_zip_sha256'] == ZIP_SHA and m['stock_demo_sha256'] == DEMO_SHA, 'Wrong stock identity')
    bundled_apps(m)  # test/build.py --build (below) checks the image holds each one's program
    # test/patch.py (below) checks the sources and the demo, stock-demo and patch.bin hashes.
    for name, key in [('rootfs.squashfs', 'rootfs_sha256'), ('update.tar', 'update_sha256')]:
        check(sha((directory/name).read_bytes()) == m[key], f'{directory/name}: hash mismatch')
    with tarfile.open(directory/'update.tar') as t:
        check(t.getnames() == ['firmware_v20.info', 'recovery-update', 'recovery-update/xImage',
                               'recovery-update/rootfs.squashfs'], 'Unexpected update members')
        info = need(t.extractfile('firmware_v20.info'), 'Unreadable firmware_v20.info').read().decode().splitlines()
        check(info[:2] == ['Shanling Q2', TAG] and len(info) == 4, 'Wrong update identity')
        for line, name, key in zip(info[2:], ['recovery-update/xImage', 'recovery-update/rootfs.squashfs'],
                                  ['kernel_sha256', 'rootfs_sha256']):
            data = need(t.extractfile(name), f'Unreadable {name}').read()
            check(line.split() == [hashlib.md5(data).hexdigest(), name] and sha(data) == m[key], 'Update payload mismatch')
    suites(directory)


def suites(directory):
    """Every release suite at once. Each is a process of its own that only reads the build (the player suites work
    in folders of their own under it), so together they take the longest one's time, the MIPS suite's."""
    # ponytail: no limit on how many run (docs/releasing.md has the peak memory); a pool sized to the machine's
    # memory if one with less ever packages a release.
    runs = [(test, subprocess.Popen([sys.executable, ROOT/f'test/{test}.py', *map(str, args)]))
            for test, *args in (('peq',), ('build', '--build', directory), ('patch', directory),
                                ('playback', directory), ('m4b', directory), ('ldac', directory),
                                ('headset_io', directory), ('bluealsa', directory),
                                ('coverflow',), ('player/run',), ('remote',))]
    failed = [f'test/{test}.py' for test, suite in runs if suite.wait()]
    check(not failed, f'Failed: {", ".join(failed)}')


def package(stock, out):
    check(version_ok(VERSION, BETA), f'VERSION must be three single digits (x.y.z) and BETA 0-9: {VERSION}, {BETA}')
    check(not out.exists(), 'Release output must be a fresh directory')
    text = notes()  # its changelog entry is the newest, before anything is built
    check(not run('git', '-C', ROOT, 'status', '--porcelain', '--', 'patch', 'tools', 'assets', 'docs', 'test', 'requirements.txt').strip(),
          'Commit the build inputs before packaging')
    check(shelf.verify(), 'sources.list is not signed by the release key: docs/releasing.md#the-app-list')
    # the record of each patched stock site (docs/rebasing.md), which only the stock ZIP can check; -I: it reads stock's files
    check(subprocess.run([sys.executable, '-I', ROOT/'docs/rebasing/sitenotes.py', stock, '--check']).returncode == 0,
          f"tools/stock_sites.tsv is not current: run python3 -I docs/rebasing/sitenotes.py '{stock}' and commit it")
    out.mkdir(parents=True)
    source = source_sha256()
    revision = run('git', '-C', ROOT, 'rev-parse', 'HEAD').strip()
    for suffix in ('', '-repeat'):
        build(stock, out/('build'+suffix), ROOT/'assets/boot-logo.jpg')
    # The byte comparison below proves the repeat build; validate the first only.
    validate(out/'build')
    data = (out/'build/update.tar').read_bytes()
    check(data == (out/'build-repeat/update.tar').read_bytes(), 'Non-reproducible update')
    (out/ASSET).write_bytes(data)
    checksums = {ASSET: sha(data)}
    subprocess.run([sys.executable, str(ROOT/'docs/manual/build.py'), str(out/GUIDE)], check=True)
    check(source == source_sha256() and revision == run('git', '-C', ROOT, 'rev-parse', 'HEAD').strip(),
          'Source changed during release build')
    (out/'SHA256SUMS').write_text(''.join(f'{digest}  {name}\n' for name, digest in checksums.items()))
    apps = bundled_apps(json.loads((out/'build/manifest.json').read_text()))
    (out/'release-notes.md').write_text(release_body(text, checksums, apps))
    record = dict(tag=VERSION, revision=revision, source_sha256=source, assets=checksums, bundled_apps=apps,
                  guide_sha256=sha((out/GUIDE).read_bytes()), notes_sha256=sha((out/'release-notes.md').read_bytes()))
    (out/'release.json').write_text(json.dumps(record, indent=2)+'\n')
    print(f'Validated and reproducible: {out}')


def digest(out, env=signing.ENV):
    """The broker's command that signs the packaged update.tar (docs/releasing.md#signing-a-release)."""
    record = json.loads((out/'release.json').read_text())
    check(record['tag'] == VERSION and sha((out/ASSET).read_bytes()) == record['assets'][ASSET], 'Wrong release tag or update')
    return signing.broker_command(signing.update_message(VERSION, out/ASSET), env)


def sign(out, signature=None, key=None):
    """update.tar.sig beside the packaged update.tar: signature (the broker's base64) checked against the release
    key, or one made with key (a private key's PEM); its hash in release.json."""
    record = json.loads((out/'release.json').read_text())
    check(record['tag'] == VERSION and sha((out/ASSET).read_bytes()) == record['assets'][ASSET], 'Wrong release tag or update')
    message = signing.update_message(VERSION, out/ASSET)
    sig = signing.signature_b64(signature) if signature is not None else signing.sign(message, key)
    check(signing.verify(message, sig), f'That signature does not check against {PUBLIC_KEY.relative_to(ROOT)}')
    (out/SIGNATURE).write_bytes(sig)
    record['signature_sha256'] = sha(sig)
    (out/'release.json').write_text(json.dumps(record, indent=2)+'\n')


def upload(out, publish=False):
    check(not (publish and BETA), 'A beta is a pre-release: set BETA = 0 in tools/build.py to publish')
    record = json.loads((out/'release.json').read_text())
    check(record['tag'] == VERSION and record['source_sha256'] == source_sha256(), 'Wrong release source/tag')
    check(set(record['assets']) == {ASSET}, f'The release is {ASSET} alone')
    # package() validated this tree; the update bytes and source hash prove it is unchanged.
    data = (out/ASSET).read_bytes()
    check(sha(data) == record['assets'][ASSET] and data == (out/'build/update.tar').read_bytes(),
          'Release update mismatch')
    check(sha((out/GUIDE).read_bytes()) == record['guide_sha256'], 'User guide mismatch')
    # The notes packaged from the changelog then, which the source hash doesn't cover.
    check(sha((out/'release-notes.md').read_bytes()) == record.get('notes_sha256'),
          'Release notes mismatch, or release.json is from an older package: package again')
    sums = f'{record["assets"][ASSET]}  {ASSET}\n'
    check((out/'SHA256SUMS').read_text() == sums, 'Release checksums mismatch')
    # A Q2 refuses a release without its signature: never upload one.
    sig = (out/SIGNATURE).read_bytes() if (out/SIGNATURE).exists() else b''
    check(sig and sha(sig) == record.get('signature_sha256') and signing.verify(signing.update_message(VERSION, out/ASSET), sig),
          f'No {SIGNATURE} that checks against {PUBLIC_KEY.relative_to(ROOT)}: tools/release.py digest, then sign')
    gh = ['gh', '--repo', REPO, 'release']
    # Listing distinguishes an absent tag from an authentication/network failure.
    releases = json.loads(run(*gh, 'list', '--limit', '1000', '--json', 'tagName,isDraft'))
    existing = next((r for r in releases if r['tagName'] == VERSION), None)
    if existing:
        check(existing['isDraft'], 'Refusing to overwrite a published release')
    # GitHub leaves an existing tag where it is and ignores --target, so a tag a deleted pre-release left behind
    # would put this release on that commit. The last line is an annotated tag's commit.
    tag = run('git', 'ls-remote', f'https://github.com/{REPO}.git', f'refs/tags/{VERSION}', f'refs/tags/{VERSION}^{{}}').split()
    check(not tag or tag[-2] == record['revision'],
          f'Tag {VERSION} is on another commit: gh release delete {VERSION} --cleanup-tag, or delete the tag')
    run(*gh, 'edit' if existing else 'create', VERSION, '--draft', '--target', record['revision'], '--title', VERSION,
        '--notes-file', out/'release-notes.md')
    files = [ASSET, SIGNATURE, GUIDE]
    run(*gh, 'upload', VERSION, *(out/name for name in files), '--clobber')
    with tempfile.TemporaryDirectory(prefix='q2-release-verify-') as tmp:
        run(*gh, 'download', VERSION, '--dir', tmp)
        for name in files:
            check((pathlib.Path(tmp)/name).read_bytes() == (out/name).read_bytes(), f'Remote asset mismatch: {name}')
    if publish:
        run(*gh, 'edit', VERSION, '--draft=false')
    print(f'{"Published" if publish else "Verified draft"}: https://github.com/{REPO}/releases/tag/{VERSION}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    commands = ap.add_subparsers(dest='command', required=True)
    p = commands.add_parser('package')
    p.add_argument('zip', type=pathlib.Path)
    p.add_argument('--out', type=pathlib.Path, required=True)
    p = commands.add_parser('digest', help="print the secret broker's command that signs the packaged update.tar")
    p.add_argument('out', type=pathlib.Path)
    p.add_argument('--env', default=signing.ENV, help='the variable the broker puts the PEM in (default %(default)s)')
    p = commands.add_parser('sign', help=f'check and write {SIGNATURE}')
    p.add_argument('out', type=pathlib.Path)
    p.add_argument('--signature', help=f"the broker's base64; without it, the PEM in ${signing.ENV} signs")
    p = commands.add_parser('upload')
    p.add_argument('out', type=pathlib.Path)
    p.add_argument('--publish', action='store_true')
    a = ap.parse_args()
    try:
        if a.command == 'package':
            # Validation runs the MIPS suite, so fail before two builds, not after them.
            check(importlib.util.find_spec('unicorn') is not None,
                  'Release validation needs the requirements.txt environment: pip install -r requirements.txt')
            package(a.zip, a.out.resolve())
        elif a.command == 'digest':
            print(digest(a.out.resolve(), a.env))
        elif a.command == 'sign':
            check(a.signature is not None or os.environ.get(signing.ENV), f'--signature, or the private key in ${signing.ENV}')
            sign(a.out.resolve(), a.signature, os.environ.get(signing.ENV))
            print(f'Signed: {a.out/SIGNATURE}')
        else:
            upload(a.out.resolve(), publish=a.publish)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        ap.error(str(exc))
