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


def stage(binary, rel, rootfs, out):
    r = subprocess.run([str(binary), 'stage', str(rel), '--rootfs', str(rootfs), '--out', str(out)], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


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
            out = work/f'out-{label[0]}'
            code, said = stage(binary, rel, rootfs, out)
            got = vampos.sha256(out/'rootfs.squashfs') if (out/'rootfs.squashfs').exists() else None
            check(code == 0 and got == want, f'staged from {label}: rootfs.squashfs is the composed image tools/vampos.py gives ({said.strip()[-80:]})')
            check(not (work/f'out-{label[0]}.q2pkgd').exists(), f'from {label}: its work folder is gone')

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
            out = work/f'out-{name}'
            code, said = stage(binary, bad, stock, out)
            check(code == 1 and words in said and not (out/'rootfs.squashfs').exists(), f'refused: {name} ({said.strip()[-90:]})')

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
        out = work/'out-vendor'
        code, said = stage(binary, rel, stock, out)
        check(code == 1 and f'{first} is not the file this release was tested with' in said and not (out/'rootfs.squashfs').exists(),
          f'refused: a vendor file changed, named ({said.strip()[-90:]})')
        (stock/first).write_bytes(saved)
    print(f'{len(FAILS)} failed' if FAILS else 'all passed')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
