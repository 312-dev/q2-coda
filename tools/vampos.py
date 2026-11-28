#!/usr/bin/env python3
"""Build Vamp OS's base image from os/ (docs/vamp-os-base.md), and compose it with the vendor set.

    python3 tools/vampos.py build --out DIR [--worktree] [--jobs N]   # DIR/vampos.squashfs and DIR/manifest.json
    python3 tools/vampos.py compose --image vampos.squashfs (--zip ZIP | --rootfs DIR) --out composed.squashfs
    python3 tools/vampos.py vendor-manifest ZIP                       # (re)write os/vendor/V1.36.tsv
    python3 tools/vampos.py qemu                                      # the static qemu-mipsel test/os_image.py boots with
    python3 tools/vampos.py card --image composed.squashfs --card DIR [--always | --no-switch]
    python3 tools/vampos.py release --image vampos.squashfs --zip ZIP --tag TAG --out DIR [--key PEM]
    python3 tools/vampos.py zones OUT                                 # q2sysd's zone table (os/board/q2/post-build.sh)

`build` runs Buildroot (BUILDROOT, pinned by SHA-256, with os/patches/buildroot applied) over os/ as a
BR2_EXTERNAL tree. Its sources are exported from git first: this tree's os/, tools/, player/, playd/, sysd/, libd/, netd/, pkgd/, patch/, assets/signing and
assets/fonts at HEAD, and q2ui's ui/ and patch/ at Q2UI_COMMIT, so the image is a function of the commits
it names (manifest.json); --worktree exports the working tree instead, for a try, and says so. It needs
Linux and a case-sensitive file system: on a Mac it runs under tools/mac/q2env, whose q2-os volume holds
CACHE. Downloads go to DL.

`card` lays a composed image out on a card (DIR, its root) for the card boot of a --dev build of today's Vamp
(patch/q2vampos.sh, docs/vamp-os-base.md#card-boot): .vamp/os/vampos.squashfs, its .sha256, and boot-once (one
boot, removed before the hand-off), boot-always, or no switch.

`release` makes what a Q2's q2pkgd stages (docs/pkgd.md): DIR/vampos.squashfs, DIR/release.txt (the image's SHA-512,
the vendor manifest's files and the SHA-256 the image composes to with V1.36's vendor set) and DIR/release.sig, the
release key's Ed25519 signature of it (the key's PEM from --key, or tools/signing.py's Q2_SIGNING_KEY).

`vampos.squashfs` holds no file of Shanling's. `compose` appends /vendor to a copy of it: the files
os/vendor/V1.36.tsv names, each checked against its SHA-256, taken from the user's own stock firmware ZIP or
from an unpacked root filesystem (on a Q2, its own running one: the installer's seam, docs/vamp-os.md#installer-and-updates).

`zones` writes the zone page's index to zone name table q2sysd reads (docs/sysd.md#the-clock), a name a line.
"""
import argparse
import hashlib
import io
import json
import os
import pathlib
import platform
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
# git as the build container's user, who may not own the worktree's files (git's safe.directory check)
GIT = ['git', '-c', 'safe.directory=*', '-C', str(ROOT)]
# GRAYTHINKPAD's path on both machines (tools/mac/q2env mounts the q2-os volume there on a Mac): the
# build directory's paths end up in the toolchain and its output.
CACHE = pathlib.Path(os.environ.get('Q2_OS_CACHE') or '/home/gray/.cache/q2-os')
DL = pathlib.Path(os.environ.get('Q2_OS_DL') or pathlib.Path.home()/'.cache/q2-os/dl')
JOBS = os.environ.get('Q2_OS_JOBS', '4')

# The 2026.02 long-term release; the hash is the one in buildroot.org's signed .sign file
BUILDROOT = ('2026.02.3', 'https://buildroot.org/downloads/buildroot-2026.02.3.tar.xz',
             '5a59e7501b0b4ec52c41f4bfa79412320e0b37eae5f719605a258e8d0c6fc7fb')
PATCHES = ROOT/'os/patches/buildroot'
# feat/render-engine's ui/ (another worker's; read-only here): moved on by changing this commit
Q2UI_COMMIT = '9af5a6f5'
VAMP_PATHS = ['os', 'tools', 'player', 'playd', 'sysd', 'libd', 'netd', 'btd', 'pkgd', 'dacloop', 'scrobd', 'dlnad', 'sendd', 'carrd', 'gate', 'perm', 'patch', 'assets/signing', 'assets/fonts', 'assets/scrobble-ca.pem', 'LICENSE']
Q2UI_PATHS = ['ui', 'patch', 'LICENSE', 'tools', 'assets/themes', 'assets/icons', 'assets/fonts']  # tools and assets: its art
MANIFEST = ROOT/'os/vendor/V1.36.tsv'
# A static qemu-mipsel for the boot test (test/os_image.py), the host's architecture: Ubuntu 24.04's, from
# Launchpad's permanent copy of the package
QEMU = {'aarch64': ('https://launchpad.net/ubuntu/+archive/primary/+files/qemu-user-static_8.2.2+ds-0ubuntu1.20_arm64.deb',
                    'd48d16638d07e0224bd4649dcd61e24be646e835a7aa16e01b65529c9765be8c')}
ROOTFS_ROOM = 72 << 20  # mtd2, as tools/build.py's
STOCK_ZIP_MEMBER = 'Q2 Firmware V1.36/update.tar'
STOCK_ROOTFS_MEMBER = 'recovery-update/rootfs.squashfs'
# Stock path prefix -> its place under /vendor
VENDOR_PLACES = [('module_driver/', 'vendor/module_driver/'), ('firmware/', 'vendor/firmware/'),
                 ('usr/data/', 'vendor/mcu/'), ('usr/bin/rtk_hciattach', 'vendor/bin/rtk_hciattach')]
# Shanling's programs in the set: rtk_hciattach, the RTL8733BS's Bluetooth attach (its firmware loader and line
# discipline set-up), which nothing of Vamp's replaces yet (docs/vamp-os-base.md#bluetooth)
VENDOR_PROGRAMS = ['usr/bin/rtk_hciattach']


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def run(*cmd, **kw):
    print('+', ' '.join(map(str, cmd)), file=sys.stderr, flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def fetch(url, digest):
    path = DL/url.rsplit('/', 1)[-1]
    if not (path.exists() and sha256(path) == digest):
        import urllib.request
        DL.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(path.suffix + '.part')
        with urllib.request.urlopen(url, timeout=600) as r, open(part, 'wb') as f:
            shutil.copyfileobj(r, f, 1 << 20)
        if sha256(part) != digest:
            part.unlink()
            sys.exit(f'os: {url} does not have SHA-256 {digest}')
        part.replace(path)
    return path


def buildroot():
    """Buildroot's tree, unpacked once with os/patches/buildroot applied; again when either changes."""
    version, url, digest = BUILDROOT
    tree = CACHE/f'buildroot-{version}'
    patches = sorted(PATCHES.glob('*.patch'))
    stamp = hashlib.sha256(repr((digest, [(p.name, sha256(p)) for p in patches])).encode()).hexdigest()
    if (tree/'.vamp-stamp').exists() and (tree/'.vamp-stamp').read_text() == stamp:
        return tree
    archive = fetch(url, digest)
    if tree.exists():
        shutil.rmtree(tree)
    CACHE.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as t:
        t.extractall(CACHE, filter='tar')
    for p in patches:
        run('patch', '-p1', '-d', tree, '-i', p)
    (tree/'.vamp-stamp').write_text(stamp)
    return tree


def export(commit, paths, dest, worktree=False):
    """paths at commit into dest (replaced), with a COMMIT file naming what it is; the working tree's files
    instead with worktree, named COMMIT-dirty when they differ from it."""
    full = subprocess.check_output([*GIT, 'rev-parse', commit], text=True).strip()
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    if worktree:
        files = subprocess.check_output([*GIT, 'ls-files', '-co', '--exclude-standard', '-z', '--', *paths]).split(b'\0')
        for f in filter(None, files):
            src = ROOT/f.decode()
            if src.is_file():
                (dest/f.decode()).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest/f.decode())
        dirty = subprocess.run([*GIT, 'diff', '--quiet', 'HEAD', '--', *paths]).returncode != 0
        dirty = dirty or bool(subprocess.check_output([*GIT, 'ls-files', '-o', '--exclude-standard', '--', *paths]))
        full += '-dirty' if dirty else ''
    else:
        archive = subprocess.check_output([*GIT, 'archive', '--format=tar', full, '--', *paths])
        with tarfile.open(fileobj=io.BytesIO(archive)) as t:
            t.extractall(dest, filter='tar')
    (dest/'COMMIT').write_text(full + '\n')
    return full


def ui_art(tree):
    """q2ui's glyphs (ui/image.c icon: oled_<name>_w.png and the rest in --icons), drawn by the pin's own tools/art.py
    from its themes, as today's Vamp's tools/build.py draws them into demo's images: into tree/icons, which the q2ui
    package installs in /usr/share/vamp/icons"""
    icons = tree/'icons'
    icons.mkdir()
    code = ('import pathlib, sys; sys.path.insert(0, "tools"); from theme import builtins; from art import theme_images\n'
            'for n, d in theme_images(builtins()).items(): pathlib.Path("icons", n + ".png").write_bytes(d)\n')
    subprocess.run([sys.executable, '-c', code], cwd=tree, check=True)


def build(out, worktree=False, jobs=JOBS):
    if platform.system() != 'Linux':
        sys.exit('os: build runs on Linux; on a Mac, under tools/mac/q2env')
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    tree = buildroot()
    src = CACHE/'src'
    vamp = export('HEAD', VAMP_PATHS, src/'vamp', worktree)
    q2ui = export(Q2UI_COMMIT, Q2UI_PATHS, src/'q2ui')
    ui_art(src/'q2ui')
    work = CACHE/'out'
    env = dict(os.environ, BR2_DL_DIR=str(DL))
    common = [f'O={work}', f'BR2_EXTERNAL={src}/vamp/os', f'VAMP_SRC={src}']
    run('make', '-C', tree, *common, 'vamp_q2_defconfig', env=env)
    run('nice', 'make', '-C', tree, *common, f'BR2_JLEVEL={jobs}', env=env)
    image = work/'images/rootfs.squashfs'
    shutil.copy(image, out/'vampos.squashfs')
    size = image.stat().st_size
    manifest = {'image': 'vampos.squashfs', 'sha256': sha256(image), 'bytes': size, 'room': ROOTFS_ROOM,
                'buildroot': BUILDROOT[0], 'vamp_commit': vamp, 'q2ui_commit': q2ui,
                'build_seconds': round(time.monotonic() - started)}
    (out/'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    if size > ROOTFS_ROOM:
        sys.exit(f'os: vampos.squashfs is {size:,} bytes, over the {ROOTFS_ROOM:,} mtd2 holds')
    print(out/'vampos.squashfs')


# The zone page's saved index (stock's [SYSSET] TIMEZONE) to the zone q2sysd sets, from tools/build.py's TIMEZONES, which
# today's Vamp sets as demo's TZ. Its POSIX rules stand in for zones the stock rootfs's tzdata (older than 2022) has wrong;
# the image's own tzdata (Buildroot's) has them right, so those take their names here (docs/sysd.md#the-clock).
ZONE_FOR_RULE = {'<-12>12': 'Etc/GMT+12', 'CST6': 'America/Mexico_City', '<+0330>-3:30': 'Asia/Tehran',
                 '<+12>-12': 'Pacific/Fiji'}


def zone_names():
    sys.path.insert(0, str(ROOT/'tools'))
    import build
    return [ZONE_FOR_RULE.get(tz, tz) for _, tz in build.TIMEZONES]


def read_manifest(path=MANIFEST):
    """{stock path: (sha256, mode)} and the ZIP's SHA-256 from os/vendor/V1.36.tsv"""
    files, zip_sha = {}, None
    for line in path.read_text().splitlines():
        if line.startswith('# zip-sha256 '):
            zip_sha = line.split()[2]
        elif line and not line.startswith('#'):
            name, digest, mode = line.split('\t')
            files[name] = (digest, int(mode, 8))
    return files, zip_sha


def place(name):
    for stock, vendor in VENDOR_PLACES:
        if name.startswith(stock):
            return vendor + name[len(stock):]
    raise ValueError(f'os: {name} has no place under /vendor')


def stock_rootfs(zip_path, dest, zip_sha=None):
    """The stock rootfs.squashfs out of the user's firmware ZIP, into dest; its path."""
    raw = pathlib.Path(zip_path).read_bytes()
    if zip_sha and hashlib.sha256(raw).hexdigest() != zip_sha:
        sys.exit(f'os: {zip_path} is not the stock firmware the vendor manifest was made from')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        update = z.read(STOCK_ZIP_MEMBER)
    with tarfile.open(fileobj=io.BytesIO(update)) as t:
        member = t.extractfile(STOCK_ROOTFS_MEMBER)
        sq = dest/'stock.squashfs'
        sq.write_bytes(member.read())
    return sq


def vendor_manifest(zip_path):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        sq = stock_rootfs(zip_path, tmp)
        run('unsquashfs', '-q', '-n', '-d', tmp/'fs', sq, 'module_driver', 'firmware', 'usr/data/libmcu-bare.bin',
            *VENDOR_PROGRAMS)
        rows = []
        for f in sorted((tmp/'fs').rglob('*')):
            if f.is_file() and not f.is_symlink():
                name = f.relative_to(tmp/'fs').as_posix()
                place(name)
                rows.append(f'{name}\t{sha256(f)}\t{f.stat().st_mode & 0o7777:04o}')
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text('# The vendor set (docs/vamp-os-base.md#the-vendor-set): stock V1.36 path, SHA-256, mode.\n'
                        '# Hashes are facts about Shanling\'s files; the files are never committed or shipped.\n'
                        f'# zip-sha256 {hashlib.sha256(pathlib.Path(zip_path).read_bytes()).hexdigest()}\n'
                        + ''.join(r + '\n' for r in rows))
    print(f'{MANIFEST}: {len(rows)} files')


def qemu():
    """The static qemu-mipsel of QEMU for this machine, unpacked once beside the downloads; its path."""
    url, digest = QEMU.get(platform.machine(), (None, None))
    if not url:
        sys.exit(f'os: no pinned qemu-user-static for {platform.machine()}; pass test/os_image.py --qemu')
    out = DL.parent/'qemu'/digest[:12]
    binary = out/'usr/bin/qemu-mipsel-static'
    if not binary.exists():
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        # the one file: the package's other paths include read-only folders of links
        tar = subprocess.check_output(['dpkg-deb', '--fsys-tarfile', fetch(url, digest)])
        with tarfile.open(fileobj=io.BytesIO(tar)) as t:
            t.extract('./usr/bin/qemu-mipsel-static', out, filter='tar')
    return binary


def mksquashfs():
    built = CACHE/'out/host/bin/mksquashfs'
    return built if built.exists() else 'mksquashfs'


def compose(image, out, zip_path=None, rootfs=None):
    files, zip_sha = read_manifest()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        if zip_path:
            sq = stock_rootfs(zip_path, tmp, zip_sha)
            run('unsquashfs', '-q', '-n', '-d', tmp/'fs', sq, *files)
            rootfs = tmp/'fs'
        stage = tmp/'stage'
        for name, (digest, mode) in files.items():
            src = pathlib.Path(rootfs)/name
            if not src.is_file():
                sys.exit(f'os: the vendor set has no {name}')
            if sha256(src) != digest:
                sys.exit(f'os: {name} is not the file the vendor manifest names (an unsupported stock version, or damaged)')
            dst = stage/place(name)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            dst.chmod(mode)
        for d in sorted(stage.rglob('*'), reverse=True):
            if d.is_dir():
                d.chmod(0o755)
        epoch = struct.unpack_from('<I', pathlib.Path(image).read_bytes(), 8)[0]  # the base image's mkfs time
        shutil.copyfile(image, out)
        # append: /vendor joins the image's root; one thread and fixed owners and times, so the bytes depend
        # on the inputs alone (a Q2 composing must give what this machine gives)
        run(mksquashfs(), stage, out, '-no-progress', '-no-recovery', '-processors', '1', '-all-root',
            '-all-time', epoch, '-mkfs-time', epoch)
    size = pathlib.Path(out).stat().st_size
    if size > ROOTFS_ROOM:
        sys.exit(f'os: the composed image is {size:,} bytes, over the {ROOTFS_ROOM:,} mtd2 holds')
    print(f'{out}: {size:,} bytes, sha256 {sha256(out)}')


def card(image, root, switch='boot-once'):
    d = pathlib.Path(root)/'.vamp/os'
    d.mkdir(parents=True, exist_ok=True)
    for name in ('boot-once', 'boot-always'):
        if (d/name).exists():
            (d/name).unlink()
    shutil.copyfile(image, d/'vampos.squashfs.part')
    (d/'vampos.squashfs.part').replace(d/'vampos.squashfs')
    (d/'vampos.squashfs.sha256').write_text(f'{sha256(d/"vampos.squashfs")}  vampos.squashfs\n')
    if switch:
        (d/switch).write_text('')
    print(f'{d}: vampos.squashfs, vampos.squashfs.sha256{", " + switch if switch else ""}')


def release(image, zip_path, tag, out, key=None):
    import signing
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    files, _ = read_manifest()
    with tempfile.TemporaryDirectory() as tmp:
        composed = pathlib.Path(tmp)/'composed.squashfs'
        compose(image, composed, zip_path=zip_path)
        digest = sha256(composed)
    shutil.copyfile(image, out/'vampos.squashfs')
    h = hashlib.sha512(pathlib.Path(image).read_bytes()).hexdigest()
    epoch = struct.unpack_from('<I', pathlib.Path(image).read_bytes(), 8)[0]
    lines = ['Q2REL/1', f'tag {tag}', f'image {h} {pathlib.Path(image).stat().st_size}', f'epoch {epoch}',
             *(f'file {n} {d} {m:o}' for n, (d, m) in files.items()), f'composed V1.36 {digest}']
    text = ''.join(l + '\n' for l in lines).encode()
    (out/'release.txt').write_bytes(text)
    pem = pathlib.Path(key).read_text() if key else os.environ.get(signing.ENV)
    if not pem:
        sys.exit('os: no signing key (--key, or Q2_SIGNING_KEY)')
    message = f'q2-coda-vampos-v1 {tag} {hashlib.sha512(text).hexdigest()}\n'.encode()
    (out/'release.sig').write_bytes(signing.sign(message, pem))
    print(f'{out}: release {tag}, composes to {digest}')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    b.add_argument('--out', type=pathlib.Path, required=True)
    b.add_argument('--worktree', action='store_true')
    b.add_argument('--jobs', default=JOBS)
    c = sub.add_parser('compose')
    c.add_argument('--image', type=pathlib.Path, required=True)
    c.add_argument('--out', type=pathlib.Path, required=True)
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument('--zip', type=pathlib.Path)
    g.add_argument('--rootfs', type=pathlib.Path)
    v = sub.add_parser('vendor-manifest')
    v.add_argument('zip', type=pathlib.Path)
    sub.add_parser('qemu')
    r = sub.add_parser('release')
    r.add_argument('--image', type=pathlib.Path, required=True)
    r.add_argument('--zip', type=pathlib.Path, required=True)
    r.add_argument('--tag', required=True)
    r.add_argument('--out', type=pathlib.Path, required=True)
    r.add_argument('--key')
    z = sub.add_parser('zones')
    z.add_argument('out', type=pathlib.Path)
    k = sub.add_parser('card')
    k.add_argument('--image', type=pathlib.Path, required=True)
    k.add_argument('--card', type=pathlib.Path, required=True)
    s = k.add_mutually_exclusive_group()
    s.add_argument('--always', action='store_true')
    s.add_argument('--no-switch', action='store_true')
    a = ap.parse_args()
    if a.cmd == 'build':
        build(a.out, a.worktree, a.jobs)
    elif a.cmd == 'compose':
        compose(a.image, a.out, a.zip, a.rootfs)
    elif a.cmd == 'release':
        release(a.image, a.zip, a.tag, a.out, a.key)
    elif a.cmd == 'card':
        card(a.image, a.card, None if a.no_switch else 'boot-always' if a.always else 'boot-once')
    elif a.cmd == 'zones':
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(''.join(name + '\n' for name in zone_names()))
    elif a.cmd == 'qemu':
        print(qemu())
    else:
        vendor_manifest(a.zip)


if __name__ == '__main__':
    main()
