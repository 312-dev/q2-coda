#!/usr/bin/env python3
"""The App Store's list, sources.list (docs/internals.md#q2-shelf): its pins, its serial and its signature.

Each app's line pins the exact zip a Q2 installs, `owner/repo TAG ASSET SIZE SHA256 [Name]`: the release's tag, its
asset's name, size and SHA-256. `serial N` numbers the list, and a Q2 takes no list numbered below the one it has.
The last line, `signature <128 hex>`, is the q2-coda release key's signature of everything before it
(tools/signing.py). The Q2 parses the list itself (patch/netupdate.c shelf_sources); this is the maintainer's side.

  python3 tools/shelf.py pin [OWNER/REPO ...]   each app's (or these apps') latest release: fetched, hashed, pinned
  python3 tools/shelf.py digest [--env NAME]   the command that signs the list, for a secret broker (stdout)
  python3 tools/shelf.py sign --signature B64  that signature checked and written as the list's last line
  python3 tools/shelf.py verify                whether the list's signature checks against the release key

pin reads public GitHub releases only, and changes nothing but sources.list. See docs/releasing.md#the-app-list."""
import argparse
import dataclasses
import hashlib
import json
import os
import pathlib
import re
import sys
import tempfile
import urllib.request

import signing

ROOT = pathlib.Path(__file__).resolve().parents[1]
LIST = ROOT/'sources.list'
CAP = 1 << 30  # the most a zip may be, as the most an install unpacks (netupdate.c SH_CAP)
REPO = r'[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]+'
PIN = r'(?P<tag>[A-Za-z0-9._-]{1,31}) (?P<asset>[A-Za-z0-9._-]{1,59}\.zip) (?P<size>[0-9]{1,10}) (?P<sha>[0-9a-f]{64})'
ENTRY = re.compile(rf'(?P<dropped>-?)(?P<repo>{REPO})(?: {PIN})?(?: (?P<name>\S.*))?')
SERIAL = re.compile(r'serial ([0-9]{1,9})')


@dataclasses.dataclass
class App:
    repo: str
    name: str = ''
    dropped: bool = False
    tag: str = ''
    asset: str = ''
    size: int = 0
    sha256: str = ''

    def line(self):
        pin = f' {self.tag} {self.asset} {self.size} {self.sha256}' if self.tag and not self.dropped else ''
        return f'{"-" if self.dropped else ""}{self.repo}{pin}{f" {self.name}" if self.name else ""}'


def parse(text):
    """sources.list's lines as written: an App for each app's, the serial as an int, anything else (comments, blank
    lines) as its text. The signature line is not among them (signing.signed_list)."""
    out: list[App | int | str] = []
    for line in text.splitlines():
        m = SERIAL.fullmatch(line)
        entry = ENTRY.fullmatch(line) if not line.startswith('#') else None
        if m:
            out.append(int(m[1]))
        elif entry:
            out.append(App(entry['repo'], entry['name'] or '', bool(entry['dropped']), entry['tag'] or '',
                           entry['asset'] or '', int(entry['size'] or 0), entry['sha'] or ''))
        else:
            out.append(line)
    return out


def text_of(items):
    return ''.join(f'{x.line() if isinstance(x, App) else f"serial {x}" if isinstance(x, int) else x}\n' for x in items)


def problems(items):
    """What a Q2 would refuse or skip in the list: no serial, two, an app unpinned, a zip past CAP, an id twice."""
    found, ids = [], set()
    serials = [x for x in items if isinstance(x, int)]
    if len(serials) != 1:
        found.append(f'{len(serials)} serial lines, not one')
    for app in (x for x in items if isinstance(x, App)):
        name = app.repo.split('/')[1]
        id_ = name[3:] if len(name) > 3 and name.startswith('q2-') else name
        if id_ in ids:
            found.append(f'{app.repo}: another app has the id {id_}')
        ids.add(id_)
        if not app.dropped and not app.tag:
            found.append(f'{app.repo}: not pinned (tools/shelf.py pin {app.repo})')
        if app.size > CAP:
            found.append(f'{app.repo}: {app.size} bytes, over {CAP}')
    return found


def bump(items):
    """The list with its serial one higher (serial 1 before the first app when it has none)."""
    if not any(isinstance(x, int) for x in items):
        first = next((i for i, x in enumerate(items) if isinstance(x, App)), len(items))
        return items[:first] + [1] + items[first:]
    return [x + 1 if isinstance(x, int) else x for x in items]


def get(url, accept='application/vnd.github+json'):
    request = urllib.request.Request(url, headers={'User-Agent': 'q2-coda-shelf', 'Accept': accept})
    return urllib.request.urlopen(request, timeout=60)


def latest(repo):
    """repo's latest release: its tag and its first asset whose name ends in .zip, as {name, size, url, digest}."""
    release = json.load(get(f'https://api.github.com/repos/{repo}/releases/latest'))
    asset = next((a for a in release.get('assets', []) if a['name'].endswith('.zip')), None)
    if not asset:
        raise ValueError(f'{repo}: its latest release, {release.get("tag_name")}, has no .zip')
    return release['tag_name'], dict(name=asset['name'], size=asset['size'], url=asset['browser_download_url'],
                                     digest=asset.get('digest'))


def fetch_sha256(url, size):
    """The SHA-256 of url's bytes and their count, read once into a temporary file that is then deleted."""
    h, n = hashlib.sha256(), 0
    with get(url, 'application/octet-stream') as r, tempfile.TemporaryFile() as f:
        for chunk in iter(lambda: r.read(1 << 20), b''):
            h.update(chunk)
            f.write(chunk)
            n += len(chunk)
            if n > CAP:
                raise ValueError(f'{url}: over {CAP} bytes')
    if n != size:
        raise ValueError(f'{url}: {n} bytes, the release says {size}')
    return h.hexdigest(), n


def pin(path=LIST, repos=(), resolve=latest, hash_url=fetch_sha256):
    """Each listed app (or each of repos) pinned to its latest release's zip; the serial up and the signature
    dropped once anything changes, so the list is signed again (digest, sign). The pins that changed."""
    body, _ = signing.signed_list(path.read_bytes())
    items, changed = parse(body.decode()), []
    for app in (x for x in items if isinstance(x, App) and not x.dropped and (not repos or x.repo in repos)):
        tag, asset = resolve(app.repo)
        if not re.fullmatch(PIN, f'{tag} {asset["name"]} {asset["size"]} {"0" * 64}'):
            raise ValueError(f'{app.repo}: {tag} {asset["name"]} would not parse on a Q2')
        sha256, size = hash_url(asset['url'], asset['size'])
        if asset.get('digest') and asset['digest'] != f'sha256:{sha256}':
            raise ValueError(f'{app.repo}: downloaded {sha256}, GitHub says {asset["digest"]}')
        new = dataclasses.replace(app, tag=tag, asset=asset['name'], size=size, sha256=sha256)
        if new != app:
            changed.append(new)
            items[items.index(app)] = new
    if changed:
        path.write_text(text_of(bump(items)))
    return changed


def digest(path=LIST, env=signing.ENV):
    """The broker's command that signs the list as it is (stdout), or None when its signature already checks. A
    list edited since it was signed has its serial raised first, and its old signature dropped."""
    body, sig = signing.signed_list(path.read_bytes())
    if sig is not None and signing.verify(signing.list_message(body), sig):
        return None
    items = parse(body.decode())
    if sig is not None or not any(isinstance(x, int) for x in items):
        items = bump(items)
    found = problems(items)
    if found:
        raise ValueError('; '.join(found))
    path.write_text(text_of(items))
    return signing.broker_command(signing.list_message(path.read_bytes()), env)


def sign(path=LIST, signature=None, key=None):
    """The list's signature line written: signature (the broker's base64) checked against the release key, or a
    signature made with key (a private key's PEM)."""
    body, _ = signing.signed_list(path.read_bytes())
    found = problems(parse(body.decode()))
    if found:
        raise ValueError('; '.join(found))
    message = signing.list_message(body)
    sig = signing.signature_b64(signature) if signature is not None else signing.sign(message, key)
    if not signing.verify(message, sig):
        raise ValueError(f'That signature does not check against {signing.PUBLIC_KEY.relative_to(ROOT)}')
    path.write_bytes(body + f'{signing.SIG_LINE}{sig.hex()}\n'.encode())


def verify(path=LIST):
    """Whether the list's signature checks against the release key, with nothing a Q2 would refuse in it."""
    body, sig = signing.signed_list(path.read_bytes())
    return sig is not None and not problems(parse(body.decode())) and signing.verify(signing.list_message(body), sig)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--list', type=pathlib.Path, default=LIST)
    commands = ap.add_subparsers(dest='command', required=True)
    p = commands.add_parser('pin')
    p.add_argument('repos', nargs='*')
    p = commands.add_parser('digest')
    p.add_argument('--env', default=signing.ENV, help='the variable the broker puts the PEM in (default %(default)s)')
    p = commands.add_parser('sign')
    p.add_argument('--signature', help=f"the broker's base64; without it, the PEM in ${signing.ENV} signs")
    commands.add_parser('verify')
    a = ap.parse_args(argv)
    try:
        if a.command == 'pin':
            for app in pin(a.list, a.repos):
                print(f'{app.repo}: {app.tag} {app.asset} {app.size} {app.sha256}')
        elif a.command == 'digest':
            command = digest(a.list, a.env)
            print(command if command else f'{a.list}: signed already', file=sys.stdout if command else sys.stderr)
        elif a.command == 'sign':
            if a.signature is None and not os.environ.get(signing.ENV):
                ap.error(f'--signature, or the private key in ${signing.ENV}')
            sign(a.list, a.signature, os.environ.get(signing.ENV))
            print(f'{a.list}: signed')
        else:
            ok = verify(a.list)
            print(f'{a.list}: {"signed by the release key" if ok else "NOT signed by the release key"}')
            return 0 if ok else 1
    except (OSError, ValueError, KeyError) as exc:
        ap.error(str(exc))
    return 0


if __name__ == '__main__':
    sys.exit(main())
