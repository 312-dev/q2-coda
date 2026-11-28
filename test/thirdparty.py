#!/usr/bin/env python3
"""The rebuilt third-party components (tools/thirdparty.py) in a build's root filesystem, run under
qemu-mipsel in a bwrap chroot of it. Usage: test/thirdparty.py BUILD QEMU_MIPSEL [--offline]
(QEMU_MIPSEL statically linked: the chroot has no host libraries)

Each replaced program reports the version the manifest records; every stock program that links a
replaced library still resolves all of its libraries, as do an --own-bluez or --own-bluealsa build's own
daemons; the curl tool built beside libcurl verifies
GitHub, Last.fm and radio-browser against the firmware's CA bundle, refuses example.com (whose root
the bundle lacks), a wrong host and a self-signed certificate, and verifies example.com against the
host's bundle (needs the network; --offline skips those); a small C program reads a stock PNG through
libpng 1.2 and round-trips zlib; SQLite runs an FTS3 and an R*Tree query; ffmpeg decodes, stacks and
encodes as the payload's art and photo paths do. Needs bwrap, unsquashfs, and the toolchain and
component outputs tools/thirdparty.py caches (a build has made them).
"""
import json, os, pathlib, re, shutil, subprocess, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/'tools'))
import thirdparty
from build import BLUEALSA, BLUETOOTHD, stock_program  # noqa: E402

# q2-heavy preloads tools/ci/nodump.c, which makes bwrap non-dumpable, and the kernel then refuses the write of
# its uid map ("bwrap: setting up uid map: Permission denied"). The sandbox runs without the preload.
NO_PRELOAD = {k: v for k, v in os.environ.items() if k != 'LD_PRELOAD'}

PNG_TEST = r'''
#include <stdio.h>
#include <string.h>
#include <png.h>
#include <zlib.h>
int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 2;
    png_structp png = png_create_read_struct(PNG_LIBPNG_VER_STRING, 0, 0, 0);
    png_infop info = png_create_info_struct(png);
    if (setjmp(png_jmpbuf(png))) return 3;
    png_init_io(png, f);
    png_read_png(png, info, PNG_TRANSFORM_EXPAND, 0);
    printf("png %s %ux%u\n", png_get_libpng_ver(png), (unsigned)png_get_image_width(png, info),
           (unsigned)png_get_image_height(png, info));
    static unsigned char in[65536], packed[70000], out[65536];
    for (unsigned i = 0; i < sizeof in; i++) in[i] = (unsigned char)(i * 7 / 3);
    uLongf n = sizeof packed, m = sizeof out;
    if (compress2(packed, &n, in, sizeof in, 9) || uncompress(out, &m, packed, n) || m != sizeof in || memcmp(in, out, m)) return 4;
    printf("zlib %s %lu\n", zlibVersion(), (unsigned long)n);
    return 0;
}
'''


def main(argv):
    offline = '--offline' in argv
    args = [a for a in argv if a != '--offline']
    build, qemu = pathlib.Path(args[0]).resolve(), pathlib.Path(args[1]).resolve()
    manifest = json.loads((build/'manifest.json').read_text())
    versions = manifest['thirdparty']['versions']
    failures = []

    def expect(label, ok, detail=''):
        print(('ok   ' if ok else 'FAIL ') + label + (f': {detail}' if detail and not ok else ''))
        if not ok:
            failures.append(label)

    with tempfile.TemporaryDirectory(prefix='q2-thirdparty-') as d:
        tmp = pathlib.Path(d)
        root = tmp/'root'
        subprocess.run(['unsquashfs', '-q', '-no-xattrs', '-d', root, build/'rootfs.squashfs'], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (root/'usr/libexec/qemu-binfmt').mkdir(parents=True, exist_ok=True)
        (root/'usr/libexec/qemu-binfmt/mipsel-binfmt-P').touch()
        tools = tmp/'tools'
        tools.mkdir()
        # The tools built beside the libraries, run against the rootfs's copies of them
        for name, rel in (('curl', 'usr/bin/curl'), ('openssl', 'usr/bin/openssl'), ('sqlite', 'usr/bin/sqlite3')):
            if name in versions:
                shutil.copy(thirdparty.build(name)/rel, tools/pathlib.Path(rel).name)
        shutil.copy('/etc/ssl/certs/ca-certificates.crt', tools/'host-ca.pem')
        if 'libpng' in versions:
            png, z = thirdparty.build('libpng'), thirdparty.build('zlib')
            (tmp/'png.c').write_text(PNG_TEST)
            subprocess.run([*thirdparty.Env({}, 0).cc.split(), '-O2', f'-I{png}/usr/include/libpng12', f'-I{z}/usr/include',
                            tmp/'png.c', '-o', tools/'pngtest', f'-L{png}/usr/lib', f'-L{z}/usr/lib', '-lpng12', '-lz', '-lm'],
                           check=True)
        resolv = pathlib.Path('/etc/resolv.conf').resolve()

        def mips(*cmd, timeout=120):
            p = subprocess.run(['bwrap', '--die-with-parent', '--unshare-pid', '--unshare-user', '--uid', '0', '--gid', '0',
                                '--bind', root, '/', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--tmpfs', '/run',
                                '--ro-bind', qemu, '/usr/libexec/qemu-binfmt/mipsel-binfmt-P', '--ro-bind', resolv, '/tmp/resolv.conf',
                                '--ro-bind', tools, '/opt/q2test', '--setenv', 'PATH', '/usr/bin:/bin:/usr/sbin:/sbin', *cmd],
                               capture_output=True, text=True, timeout=timeout, env=NO_PRELOAD)
            return p.returncode, p.stdout + p.stderr

        (root/'opt').mkdir(exist_ok=True)
        (root/'opt/q2test').mkdir(exist_ok=True)

        if 'openssl' in versions:
            _, out = mips('/opt/q2test/openssl', 'version')
            expect(f'openssl version is {versions["openssl"]}', out.startswith(f'OpenSSL {versions["openssl"]} '), out)
        if 'curl' in versions:
            _, out = mips('/opt/q2test/curl', '--version')
            expect(f'curl {versions["curl"]} with mbedTLS {versions["mbedtls"]} and zlib {versions["zlib"]}',
                   f'libcurl/{versions["curl"]} mbedTLS/{versions["mbedtls"]} zlib/{versions["zlib"]}' in out, out)
            expect('curl protocols are file, http, https', re.search(r'^Protocols: file http https$', out, re.M) is not None, out)
            if not offline:
                ca = ['--cacert', '/etc/scrobble-ca.pem', '-sS', '-o', '/dev/null', '-w', '%{http_code}', '--max-time', '60']
                # The firmware's bundle pins the roots of the hosts it talks to (not example.com's): GitHub for
                # Network Update and the App Store, Last.fm, radio-browser.
                for url in ('https://api.github.com/', 'https://ws.audioscrobbler.com/2.0/', 'https://all.api.radio-browser.info/json/stats'):
                    code, out = mips('/opt/q2test/curl', *ca, '--compressed', url)
                    expect(f'{url} verifies against /etc/scrobble-ca.pem', code == 0 and out.strip().isdigit() and out.strip() != '000', f'exit {code}: {out}')
                code, out = mips('/opt/q2test/curl', *ca, 'https://example.com/')
                expect('https://example.com, whose root the bundle lacks, is refused', code == 60, f'exit {code}: {out}')
                code, out = mips('/opt/q2test/curl', *ca[2:], '--cacert', '/opt/q2test/host-ca.pem', 'https://example.com/')
                expect('https://example.com verifies against the host\'s CA bundle', code == 0 and out.strip() == '200', f'exit {code}: {out}')
                code, out = mips('/opt/q2test/curl', *ca, 'https://wrong.host.badssl.com/')
                expect('a certificate for another host is refused', code in (51, 60), f'exit {code}: {out}')
                code, out = mips('/opt/q2test/curl', *ca, 'https://self-signed.badssl.com/')
                expect('a self-signed certificate is refused', code == 60, f'exit {code}: {out}')
        if 'busybox' in versions:
            _, out = mips('/bin/busybox', '--list')
            listed = set(out.split()) - {'busybox'}  # 1.38 lists the busybox applet itself; 1.31.1 did not
            expect('busybox has exactly stock\'s applets', listed == set(thirdparty.BUSYBOX_APPLETS),
                   f'missing {sorted(set(thirdparty.BUSYBOX_APPLETS) - listed)}, extra {sorted(listed - set(thirdparty.BUSYBOX_APPLETS))}')
            _, out = mips('/bin/busybox')
            expect(f'busybox is {versions["busybox"]}', out.startswith(f'BusyBox v{versions["busybox"]} '), out[:100])
            _, out = mips('/bin/sh', '-c', 'echo $((6*7)) $(echo ok | sed s/o/O/) $(ls /bin/busybox)')
            expect('busybox ash, sed and ls work', out.strip() == '42 Ok /bin/busybox', out)
        if 'wpa_supplicant' in versions:
            for exe in ('/usr/sbin/wpa_supplicant', '/usr/sbin/wpa_cli'):
                _, out = mips(exe, '-v')
                expect(f'{exe} is {versions["wpa_supplicant"]}', f' v{versions["wpa_supplicant"]}' in out, out[:200])
        if 'sqlite' in versions:
            _, out = mips('/opt/q2test/sqlite3', '--version')
            expect(f'sqlite is {versions["sqlite"]}', out.startswith(versions['sqlite'] + ' '), out)
            sql = ("create virtual table t using fts3(x); insert into t values ('shanling q2'),('ipod classic');"
                   "select x from t where t match 'q2';"
                   "create virtual table r using rtree(id, x0, x1); insert into r values (1, 0, 10);"
                   "select id from r where x0 < 5;")
            _, out = mips('/opt/q2test/sqlite3', ':memory:', sql)
            expect('sqlite FTS3 and R*Tree work', out.splitlines() == ['shanling q2', '1'], out)
        icon = 'release/assets/default/raw/images/xx/menu_music.png'
        if 'libpng' in versions:
            code, out = mips('/opt/q2test/pngtest', '/' + icon)
            expect(f'libpng {versions["libpng"]} reads a stock icon and zlib {versions["zlib"]} round-trips',
                   code == 0 and f'png {versions["libpng"]} ' in out and f'zlib {versions["zlib"]} ' in out, f'exit {code}: {out}')
        if 'ffmpeg' in versions:
            _, out = mips('/usr/bin/ffmpeg', '-hide_banner', '-version')
            expect(f'ffmpeg is {versions["ffmpeg"]}', out.startswith(f'ffmpeg version {versions["ffmpeg"]} '), out[:100])
            code, out = mips('/bin/sh', '-c', 'cd /tmp && /usr/bin/ffmpeg -nostdin -v error -i /' + icon +
                             ' -i /' + icon + ' -filter_complex "[0][1]hstack,scale=80:40" -frames:v 1 -y a.jpg'
                             ' && /usr/bin/ffmpeg -nostdin -v error -i a.jpg -f rawvideo -pix_fmt rgb24 - | wc -c', timeout=300)
            expect('ffmpeg decodes PNG, stacks, scales, writes and reads JPEG', code == 0 and out.split()[-1:] == ['9600'],
                   f'exit {code}: {out}')
        # Every stock program on a replaced library still finds all of its libraries: stock's bluetoothd and bluealsa
        # where an --own-bluez or --own-bluealsa build moved them (its launcher, a shell script, stands at their
        # paths), and that build's own daemons too, which link the same rootfs libraries.
        own = ([manifest['own_bluez']['path']] if 'own_bluez' in manifest else []) + \
              [f for f in manifest.get('own_bluealsa', {}).get('files', {}) if f.rsplit('/', 1)[1] == 'bluealsa']
        for exe in ('release/bin/demo', 'usr/bin/hciplayer', stock_program(BLUETOOTHD, manifest),
                    stock_program(BLUEALSA, manifest), 'usr/sbin/wpa_supplicant', 'usr/bin/ntpdate', 'usr/bin/shairport',
                    'usr/bin/ffmpeg', *own):
            code, out = mips('/lib/ld-linux-mipsn8.so.1', '--list', '/' + exe)
            expect(f'{exe} links', code == 0 and 'not found' not in out and '=>' in out, out[-300:])
    print(f'{len(failures)} failed' if failures else 'all passed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
