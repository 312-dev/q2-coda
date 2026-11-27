#!/usr/bin/env python3
"""q2pkgd's stage (pkgd/q2pkgd.c, docs/pkgd.md) built for this machine with a test release key, on a real image and a
vendor set from the stock ZIP:

    test/pkgd.py BUILD --zip ZIP          # BUILD: a tools/vampos.py build --out folder (its vampos.squashfs)

A release made by tools/vampos.py release (signed with a key made here) is staged from a stock-shaped root filesystem
and from Vamp OS's (the vendor set under /vendor), and each must give rootfs.squashfs byte for byte the image
tools/vampos.py compose gives; and when BUILD's image has its own mksquashfs, that MIPS mksquashfs, run under QEMU with
q2pkgd's flags, must give the same bytes too (a Q2 composing what the build machine composed); then each refusal: another key's signature, release.txt changed, the image changed, a
vendor file changed, a composed hash that is not the result; after any refusal nothing is staged. Needs openssl,
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


def build(work, pub):
    src = pathlib.Path(os.environ.get('Q2PKGD_SOURCE') or ROOT/'pkgd/q2pkgd.c')
    mono = ROOT/'patch/monocypher'
    key = '{' + ','.join(str(b) for b in pub) + '}'
    out = work/'q2pkgd'
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', f'-I{mono}', f'-DRELEASE_KEY={key}', '-o', str(out), str(src),
                    str(mono/'monocypher.c'), str(mono/'monocypher-ed25519.c')], check=True)
    return out


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
    code, said = pkgd(binary, 'confirm', '--card', card)
    check(code == 0 and recovery(card) == kept and not (rescue/'pending').exists(), 'confirm: recovery-update/ holds the rescue copy again')
    code, _ = pkgd(binary, 'confirm', '--card', card)
    check(code == 0 and recovery(card) == kept, 'confirm with nothing pending changes nothing')


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
