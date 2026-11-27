#!/usr/bin/env python3
"""q2pkgd's stage (pkgd/q2pkgd.c, docs/pkgd.md) built for this machine with a test release key, on a real image and a
vendor set from the stock ZIP:

    test/pkgd.py BUILD --zip ZIP          # BUILD: a tools/vampos.py build --out folder (its vampos.squashfs)

A release made by tools/vampos.py release (signed with a key made here) is staged from a stock-shaped root filesystem
and from Vamp OS's (the vendor set under /vendor), and each must give rootfs.squashfs byte for byte the image
tools/vampos.py compose gives; and when BUILD's image has its own mksquashfs, that MIPS mksquashfs, run under QEMU with
q2pkgd's flags, must give the same bytes too (a Q2 composing what the build machine composed); then each refusal: another key's signature, release.txt changed, the image changed, a
vendor file changed, a composed hash that is not the result; after any refusal nothing is staged. Then download, from a
local HTTPS feed shaped as GitHub's, into the card's .vamp/os/, and each of its refusals. Needs openssl,
mksquashfs and unsquashfs. Q2PKGD_SOURCE builds another copy, to show a broken one fails."""
import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import signing  # noqa: E402
import vampos  # noqa: E402

FAILS = []


def check(ok, what):
    print(('ok    ' if ok else 'FAIL  ') + what, flush=True)
    if not ok:
        FAILS.append(what)


def keypair(d, name):
    pem = d/f'{name}.pem'
    subprocess.run(['openssl', 'genpkey', '-algorithm', 'ed25519', '-out', str(pem)], check=True, capture_output=True)
    pub = subprocess.run(['openssl', 'pkey', '-in', str(pem), '-pubout'], check=True, capture_output=True).stdout
    return pem, signing.public_key(pub)


def compile_q2pkgd(out, key='{0}', source=None, perm=None):
    """q2pkgd for this machine (test/apps.py and test/launch.py build theirs here too): key the release key's public half
    as a C initialiser, perm q2perm.h's folder, linked with tools/player.py's host mbedTLS (the image's release and settings) for download"""
    src = pathlib.Path(source or os.environ.get('Q2PKGD_SOURCE') or ROOT/'pkgd/q2pkgd.c')
    mono = ROOT/'patch/monocypher'
    # in a process of its own: a suite importing this one may have test/thirdparty.py where tools/thirdparty.py goes
    tls = pathlib.Path(subprocess.run([sys.executable, '-c', "import player; print(player.mbedtls('host'))"], cwd=ROOT/'tools',
                                      check=True, capture_output=True, text=True).stdout.strip().splitlines()[-1])
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', f'-I{mono}', '-I', str(perm or ROOT/'perm'), '-isystem', str(tls/'include'),
                    f'-DRELEASE_KEY={key}', '-DFEED="https://feed.invalid/o/r"', '-o', str(out), str(src),
                    str(mono/'monocypher.c'), str(mono/'monocypher-ed25519.c'),
                    *(str(tls/'lib'/a) for a in ('libmbedtls.a', 'libmbedx509.a', 'libmbedcrypto.a'))], check=True)
    return out


def build(work, pub):
    return compile_q2pkgd(work/'q2pkgd', '{' + ','.join(str(b) for b in pub) + '}')


def stage(binary, rel, rootfs, card):
    """q2pkgd stage onto the card folder card; (status, what it said). The staged image is card/.vamp/stage/recovery-update/"""
    r = subprocess.run([str(binary), 'stage', str(rel), '--rootfs', str(rootfs), '--card', str(card)], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def pkgd(binary, *args):
    r = subprocess.run([str(binary), *map(str, args)], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def stock_recovery(card):
    """the owner's rescue copy: stand-ins for stock's xImage and rootfs.squashfs in the card's recovery-update/"""
    d = card/'recovery-update'
    d.mkdir(parents=True, exist_ok=True)
    (d/'xImage').write_bytes(b'stock kernel ' * 1000)
    (d/'rootfs.squashfs').write_bytes(b'stock rootfs ' * 5000)
    return {n: (d/n).read_bytes() for n in ('xImage', 'rootfs.squashfs')}


def recovery(card):
    d = card/'recovery-update'
    return {n: (d/n).read_bytes() for n in ('xImage', 'rootfs.squashfs') if (d/n).exists()}


def rescue_checks(binary, card, want):
    """install keeps the rescue copy before it puts the staged image in recovery-update/, and confirm puts it back"""
    kept = recovery(card)
    rescue = card/'.vamp/rescue'
    # a failed install: the rescue folder cannot be written, so nothing may change
    rescue.mkdir(parents=True)
    rescue.chmod(0o555)
    code, said = pkgd(binary, 'install', '--card', card)
    rescue.chmod(0o755)
    check(code == 1 and recovery(card) == kept, f'install refused when the rescue copy cannot be kept; recovery-update/ as it was ({said.strip()[-70:]})')
    # no stock copy on the card to keep: refused
    bare = card.parent/'card-bare'
    (bare/'.vamp/stage/recovery-update').mkdir(parents=True)
    (bare/'.vamp/stage/recovery-update/rootfs.squashfs').write_bytes(b'x')
    code, said = pkgd(binary, 'install', '--card', bare)
    check(code == 1 and not (bare/'recovery-update').exists(), f'install refused on a card with no recovery to keep ({said.strip()[-70:]})')
    # the install
    code, said = pkgd(binary, 'install', '--card', card)
    now = recovery(card)
    check(code == 0 and {n: (rescue/n).read_bytes() for n in kept if (rescue/n).exists()} == kept and (rescue/'pending').exists(),
          'install: the rescue copy is kept whole in .vamp/rescue/ first, and pending marked')
    check(vampos.sha256(card/'recovery-update/rootfs.squashfs') == want and now['xImage'] == kept['xImage'],
          "install: recovery-update/ holds the staged image beside the rescue's own xImage")
    code, said = pkgd(binary, 'install', '--card', card)  # again, pending: the rescue copy must stay the stock one
    check(code == 0 and {n: (rescue/n).read_bytes() for n in kept if (rescue/n).exists()} == kept, 'a second install while pending keeps the first rescue copy')
    # the recovery flashes and removes recovery-update/ (seen on a Q2); the next boot's rcS runs restore first
    shutil.rmtree(card/'recovery-update')
    code, said = pkgd(binary, 'restore', '--card', card)
    check(code == 0 and recovery(card) == kept and (rescue/'pending').exists(),
          'restore, after the recovery removed recovery-update/: the rescue copy back, the install still pending')
    code, said = pkgd(binary, 'confirm', '--card', card)
    check(code == 0 and recovery(card) == kept and not (rescue/'pending').exists(), 'confirm: recovery-update/ holds the rescue copy, nothing pending')
    code, _ = pkgd(binary, 'confirm', '--card', card)
    check(code == 0 and recovery(card) == kept, 'confirm with nothing pending changes nothing')


def certs(d):
    """a CA, a leaf for localhost it signs, and a stranger CA that signed nothing here"""
    d.mkdir()
    o = lambda *a: subprocess.run(['openssl', *a], cwd=d, check=True, capture_output=True)  # noqa: E731
    for ca in ('ca', 'stranger'):
        o('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', f'{ca}.key', '-out', f'{ca}.pem', '-days', '2', '-subj', f'/CN=Test {ca}')
    o('req', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'leaf.key', '-out', 'leaf.csr', '-subj', '/CN=localhost')
    (d/'ext').write_text('subjectAltName=DNS:localhost\n')
    o('x509', '-req', '-in', 'leaf.csr', '-CA', 'ca.pem', '-CAkey', 'ca.key', '-CAcreateserial', '-out', 'leaf.pem', '-days', '2', '-extfile', 'ext')
    return d


class Feed:
    """A release feed shaped as GitHub serves one: OWNER/NAME/releases/latest a 302 to .../releases/tag/TAG, and
    .../releases/download/TAG/ASSET a 302 to the asset on another host (here another port), which answers 200 with its
    Content-Length. self.releases maps a tag to its folder; self.latest names the tag; self.mode breaks one answer."""
    def __init__(self, certs):
        import http.server
        import ssl
        import threading
        self.releases, self.latest, self.mode, self.got = {}, None, {}, []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def answer(self, code, body=b'', **headers):
                self.send_response(code)
                for k, v in headers.items():
                    self.send_header(k.replace('_', '-'), v)
                if 'Content_Length' not in headers:
                    self.send_header('Content-Length', str(len(body)))
                self.send_header('Connection', 'close')
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:  # q2pkgd hung up first: a refusal on the head, as meant
                    pass

            def do_GET(self):
                outer.got.append(self.path)
                parts = self.path.split('/')
                if self.path == '/o/r/releases/latest':
                    if outer.mode.get('latest') == '200':
                        return self.answer(200, b'<html>')
                    tag = outer.latest or ''
                    return self.answer(302, Location=f'https://localhost:{outer.port}/o/r/releases/tag/{tag}')
                if self.path.startswith('/o/r/releases/download/') and len(parts) == 7:
                    scheme = 'http' if outer.mode.get(parts[6]) == 'http' else 'https'
                    return self.answer(302, Location=f'{scheme}://localhost:{outer.assets.port}/assets/{parts[5]}/{parts[6]}')
                if self.path.startswith('/assets/') and len(parts) == 4 and (outer.releases.get(parts[2], pathlib.Path('/-'))/parts[3]).exists():
                    body = (outer.releases[parts[2]]/parts[3]).read_bytes()
                    mode = outer.mode.get(parts[3])
                    if mode == 'altered':
                        body = body[:-1] + bytes([body[-1] ^ 1])
                    if mode == 'shorter':
                        body = body[:-1]
                    if mode == 'cut':  # the length promised, the connection closed early
                        return self.answer(200, body[:len(body) // 2], Content_Length=str(len(body)))
                    if mode == 'stall':
                        self.send_response(200)
                        self.send_header('Content-Length', str(len(body)))
                        self.end_headers()
                        self.wfile.write(body[:4096])
                        self.wfile.flush()
                        import time
                        time.sleep(8)
                        return
                    return self.answer(200, body)
                self.answer(404, b'Not Found')

            def log_message(self, *a):
                pass

        def server(this):
            srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
            srv.daemon_threads = True
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(certs/'leaf.pem', certs/'leaf.key')
            srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            this.srv, this.port = srv, srv.server_address[1]
        server(self)

        class Assets:
            pass
        self.assets = Assets()
        server(self.assets)

    def fetched(self, name):
        return [g for g in self.got if g.startswith('/assets/') and g.endswith('/' + name)]


def download_checks(binary, work, rel, rootfs, stock, want, pem, other):
    """q2pkgd download: a release fetched from the feed over verified HTTPS, checked as stage checks it, and the composed
    image put where the next card boot reads it (.vamp/os/, docs/vamp-os-base.md#card-boot); every refusal leaves the
    card's image, its .sha256 and its switch as they were, and nothing of the download behind"""
    import fcntl
    import hashlib
    c = certs(work/'certs')
    feed = Feed(c)
    tag = 'v9.9.9-test'
    text = (rel/'release.txt').read_text()

    def release(name, new_tag, body=None, key=pem):
        d = work/'feed'/name
        shutil.copytree(rel, d)
        body = body if body is not None else text.replace(f'tag {tag}\n', f'tag {new_tag}\n')
        (d/'release.txt').write_text(body)
        msg = f'q2-coda-vampos-v1 {new_tag} {hashlib.sha512(body.encode()).hexdigest()}\n'.encode()
        (d/'release.sig').write_bytes(signing.sign(msg, pathlib.Path(key).read_text()))
        return d
    feed.releases[tag] = rel
    card = work/'card-dl'
    kept = stock_recovery(card)
    os_dir = card/'.vamp/os'
    os_dir.mkdir(parents=True)

    def old_image():
        for n in list(os_dir.iterdir()):
            n.unlink()
        (os_dir/'vampos.squashfs').write_bytes(b'the image the card boots today ' * 100)
        (os_dir/'vampos.squashfs.sha256').write_text(f'{vampos.sha256(os_dir/"vampos.squashfs")}  vampos.squashfs\n')
        (os_dir/'boot-always').write_text('')
        return {n.name: n.read_bytes() for n in os_dir.iterdir()}

    def dl(*extra, ca=c/'ca.pem', root=rootfs, timeout=None):
        args = [str(binary), 'download', '--feed', f'https://localhost:{feed.port}/o/r', '--card', str(card), '--rootfs', str(root),
                '--ca', str(ca), *map(str, extra)]
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout or 300)
        return r.returncode, (r.stdout + r.stderr).strip()

    def state():
        return {n.name: n.read_bytes() for n in os_dir.iterdir()}

    before = old_image()
    feed.latest = tag
    code, said = dl()
    sums = (os_dir/'vampos.squashfs.sha256').read_text() if (os_dir/'vampos.squashfs.sha256').exists() else ''
    check(code == 0 and (os_dir/'vampos.squashfs').exists() and vampos.sha256(os_dir/'vampos.squashfs') == want,
          f"download: the latest release's composed image is the card's .vamp/os/vampos.squashfs ({said[-120:]})")
    check(sums == f'{want}  vampos.squashfs\n', f'download: vampos.squashfs.sha256 names it as q2vampos reads it ({sums.strip()[:20]})')
    check((os_dir/'release.txt').exists() and (os_dir/'release.txt').read_bytes() == (rel/'release.txt').read_bytes(),
          'download: the release.txt it came with is kept beside it')
    check((os_dir/'boot-always').exists() and not (os_dir/'boot-once').exists(), 'download: the switch is as it was (boot-always kept, no boot-once made)')
    check(not (card/'.vamp/download').exists(), 'download: its work folder on the card is gone')
    check(recovery(card) == kept and not (card/'.vamp/stage').exists(), "download: recovery-update/ untouched and nothing staged for the recovery")
    check(feed.got[:1] == ['/o/r/releases/latest'] and len(feed.fetched('vampos.squashfs')) == 1, f'download: latest asked first, the image fetched once ({feed.got[:4]})')

    got = state()
    feed.got.clear()
    code, said = dl()
    check(code == 0 and 'already' in said and not feed.fetched('vampos.squashfs') and state() == got,
          f'download again: the card has that release already, the image not fetched ({said[-80:]})')

    # an older release of another image (its composed hash another), so the card does not have it already
    feed.releases['v1.0.0'] = release('older', 'v1.0.0', text.replace('tag v9.9.9-test\n', 'tag v1.0.0\n').replace(want, '1' * 64))
    feed.latest = 'v1.0.0'
    feed.got.clear()
    code, said = dl()
    check(code == 1 and 'not newer' in said and not feed.fetched('vampos.squashfs') and state() == got,
          f'refused: a latest release older than the card\'s ({said[-80:]})')

    def refused(what, words, fetched_image=None, **kw):
        before = old_image()
        feed.got.clear()
        code, said = dl(*kw.pop('extra', ()), **kw)
        image = feed.fetched('vampos.squashfs')
        ok = code == 1 and words in said and state() == before and not (card/'.vamp/download').exists() and recovery(card) == kept
        if fetched_image is not None:
            ok = ok and bool(image) == fetched_image
        check(ok, f'refused: {what} ({said[-100:]})')

    feed.releases['v9.9.10'] = release('other-key', 'v9.9.10', key=other)
    feed.latest = 'v9.9.10'
    refused("another key's signature, the image never fetched", 'release.sig', fetched_image=False)
    feed.releases['v9.9.11'] = rel  # signed as v9.9.9-test, served as v9.9.11
    feed.latest = 'v9.9.11'
    refused('a release signed for another tag, the image never fetched', 'tag', fetched_image=False)
    feed.releases['v9.9.12'] = release('huge', 'v9.9.12', text.replace(f'tag {tag}\n', 'tag v9.9.12\n').replace(
        next(l for l in text.splitlines() if l.startswith('image ')), f'image {"0" * 128} {1 << 50}'))
    feed.latest = 'v9.9.12'
    refused('a release larger than the card has room for, the image never fetched', 'free', fetched_image=False)
    feed.releases['v9.9.13'] = release('unsigned', 'v9.9.13')
    (feed.releases['v9.9.13']/'release.sig').unlink()
    feed.latest = 'v9.9.13'
    refused('a release with no signature', 'not signed', fetched_image=False)
    feed.latest = tag
    for mode, words in (('altered', 'not the image'), ('shorter', 'bytes'), ('cut', 'short'), ('http', 'https')):
        feed.mode = {'vampos.squashfs': mode}
        refused(f'the image {mode} on the way', words)
    feed.mode = {'vampos.squashfs': 'stall'}
    import time
    t0 = time.monotonic()
    refused('a transfer that stalls (--timeout 2)', 'timed out', extra=('--timeout', '2'))
    check(time.monotonic() - t0 < 30, f'the stall ended by the timeout ({time.monotonic() - t0:.0f} s)')
    feed.mode = {'latest': '200'}
    refused('a latest that is not a redirect to a tag', 'no release', fetched_image=False)
    feed.mode = {}
    feed.latest = '..%2fx'
    refused('a tag that is not plain', 'no release', fetched_image=False)
    feed.latest = tag
    refused('a server whose certificate the CA bundle does not sign', 'TLS', fetched_image=False, ca=c/'stranger.pem')
    first = sorted(vampos.read_manifest()[0])[0]
    saved = (stock/first).read_bytes()
    (stock/first).write_bytes(saved + b'x')
    refused('a vendor file changed', f'{first} is not the file', root=stock)
    (stock/first).write_bytes(saved)
    lock = open(card/'.vamp/download.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX)
    refused('another download running', 'another download', fetched_image=False)
    lock.close()
    old_image()
    (card/'.vamp/download').mkdir()
    (card/'.vamp/download/vampos.squashfs.part').write_bytes(b'left by a download that was killed')
    code, said = dl()
    check(code == 0 and vampos.sha256(os_dir/'vampos.squashfs') == want and not (card/'.vamp/download').exists(),
          f"a killed download's leftovers are cleared and the next one lands ({said[-60:]})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('build', type=pathlib.Path)
    ap.add_argument('--zip', type=pathlib.Path, required=True)
    a = ap.parse_args()
    with tempfile.TemporaryDirectory(dir='/tmp') as tmp:
        work = pathlib.Path(tmp)
        pem, pub = keypair(work, 'release')
        other, _ = keypair(work, 'other')
        binary = build(work, pub)
        rel = work/'release'
        vampos.release(a.build/'vampos.squashfs', a.zip, 'v9.9.9-test', rel, key=pem)
        want = next(l.split()[2] for l in (rel/'release.txt').read_text().splitlines() if l.startswith('composed '))
        # a stock root filesystem's vendor set, and the same under Vamp OS's /vendor
        files, _ = vampos.read_manifest()
        stock = work/'stock'
        sq = vampos.stock_rootfs(a.zip, work)
        subprocess.run(['unsquashfs', '-q', '-n', '-d', str(stock), str(sq), *files], check=True, capture_output=True)
        vfs = work/'vampos'
        for n in files:
            dst = vfs/vampos.place(n)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(stock/n, dst)
        for label, rootfs in (('a stock root filesystem', stock), ("Vamp OS's /vendor", vfs)):
            card = work/f'card-{label[0]}'
            kept = stock_recovery(card)
            code, said = stage(binary, rel, rootfs, card)
            out = card/'.vamp/stage/recovery-update'
            got = vampos.sha256(out/'rootfs.squashfs') if (out/'rootfs.squashfs').exists() else None
            check(code == 0 and got == want, f'staged from {label}: rootfs.squashfs is the composed image tools/vampos.py gives ({said.strip()[-80:]})')
            check(not pathlib.Path(str(out) + '.q2pkgd').exists(), f'from {label}: its work folder is gone')
            check(recovery(card) == kept, f'from {label}: the card\'s recovery-update/ (the rescue copy) is untouched by stage')
        rescue_checks(binary, work/'card-a', want)
        download_checks(binary, work, rel, vfs, stock, want, pem, other)

        image_fs = work/'imagefs'
        subprocess.run(['unsquashfs', '-q', '-n', '-ignore-errors', '-no-xattrs', '-d', str(image_fs), str(a.build/'vampos.squashfs')],
                       capture_output=True)
        if (image_fs/'usr/bin/mksquashfs').exists():
            w = image_fs/'w'
            w.mkdir()
            for n, (_, mode) in files.items():
                dst = w/'stage'/vampos.place(n)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(stock/n, dst)
                dst.chmod(mode)
            for d in sorted((w/'stage').rglob('*'), reverse=True):
                if d.is_dir():
                    d.chmod(0o755)
            shutil.copyfile(a.build/'vampos.squashfs', w/'c.squashfs')
            epoch = next(l.split()[1] for l in (rel/'release.txt').read_text().splitlines() if l.startswith('epoch '))
            qemu = vampos.qemu()
            r = subprocess.run(['bwrap', '--bind', str(image_fs), '/', '--proc', '/proc', '--dev', '/dev', '--unshare-pid',
                                '--ro-bind', str(qemu), '/usr/libexec/qemu-binfmt/mipsel-binfmt-P', '/usr/bin/mksquashfs', '/w/stage',
                                '/w/c.squashfs', '-no-progress', '-no-recovery', '-processors', '1', '-all-root', '-all-time', epoch,
                                '-mkfs-time', epoch], capture_output=True, text=True)
            got = vampos.sha256(w/'c.squashfs')
            check(r.returncode == 0 and got == want, f"the image's own mksquashfs (MIPS, under QEMU) composes the same bytes ({got[:16]}, {r.stderr.strip()[-300:]})")
        else:
            print('skip  the image has no mksquashfs')

        def refused(name, edit, words):
            bad = work/name
            shutil.copytree(rel, bad)
            edit(bad)
            card = work/f'card-{name}'
            kept = stock_recovery(card)
            code, said = stage(binary, bad, stock, card)
            check(code == 1 and words in said and not (card/'.vamp/stage/recovery-update/rootfs.squashfs').exists() and recovery(card) == kept,
                  f'refused: {name} ({said.strip()[-90:]})')

        def resign(d, text, key=pem):
            (d/'release.txt').write_text(text)
            import hashlib
            msg = f'q2-coda-vampos-v1 v9.9.9-test {hashlib.sha512(text.encode()).hexdigest()}\n'.encode()
            (d/'release.sig').write_bytes(signing.sign(msg, pathlib.Path(key).read_text()))

        text = (rel/'release.txt').read_text()
        refused('another key', lambda d: resign(d, text, other), 'release.sig')
        refused('release.txt changed', lambda d: (d/'release.txt').write_text(text.replace('composed V1.36', 'composed V1.37')), 'release.sig')
        refused('the image changed', lambda d: (d/'vampos.squashfs').write_bytes((d/'vampos.squashfs').read_bytes()[:-1] + b'\1'), 'vampos.squashfs')
        refused('a composed hash that is not the result', lambda d: resign(d, text.replace(want, '0' * 64)), 'not one this release was tested as')
        first = sorted(files)[0]
        for rootfs in (stock, vfs):
            (rootfs/first).parent.mkdir(parents=True, exist_ok=True)
        saved = (stock/first).read_bytes()
        (stock/first).write_bytes(saved + b'x')
        card = work/'card-vendor'
        code, said = stage(binary, rel, stock, card)
        check(code == 1 and f'{first} is not the file this release was tested with' in said
              and not (card/'.vamp/stage/recovery-update/rootfs.squashfs').exists(),
          f'refused: a vendor file changed, named ({said.strip()[-90:]})')
        (stock/first).write_bytes(saved)
    print(f'{len(FAILS)} failed' if FAILS else 'all passed')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
