#!/usr/bin/env python3
"""Rebuild the rootfs's outdated third-party libraries and programs from pinned upstream sources.

Shanling built OpenSSL, curl, zlib and the rest years before V1.36 shipped (docs/third-party.md). Each
component here is fetched from its upstream site, checked against the SHA-256 below, compiled with
the toolchain Shanling used (Codescape GNU Tools 2020.06-01 for MIPS MTI Linux, glibc 2.28, MIPS32r2
little-endian, o32, -mfp64, -mnan=2008) and installed under the stock file names, so every symlink
and every consumer's DT_NEEDED resolves to it. Results are cached under ~/.cache/q2-thirdparty,
keyed by the source hash, the recipe's text and the toolchain, so a rebuild only recompiles what
changed. `check_abi` refuses a replacement that drops a symbol any stock program imports.

    python3 tools/thirdparty.py build [NAME...]   # build (or reuse) components, print their outputs
    python3 tools/thirdparty.py check STOCKFS     # check_abi of every INSTALL entry against an unpacked stock rootfs
"""
import ast, hashlib, inspect, json, os, pathlib, re, shutil, subprocess, sys, tarfile, urllib.request

CACHE = pathlib.Path(os.environ.get('Q2_THIRDPARTY_CACHE') or pathlib.Path.home()/'.cache/q2-thirdparty')
JOBS = os.environ.get('Q2_THIRDPARTY_JOBS', '2')

# The toolchain stock was built with (its glibc is the same 2.28 build as the rootfs's). Its
# mipsel-r2-hard-nan2008 multilib is chosen by -EL -mnan=2008; -mfp64 gives stock's FP ABI.
TOOLCHAIN = ('https://codescape.mips.com/components/toolchain/2020.06-01/'
             'Codescape.GNU.Tools.Package.2020.06-01.for.MIPS.MTI.Linux.CentOS-6.x86_64.tar.gz',
             'da43736458a13de323d198a8d17316898e7bf15c2a19023d1d83b7afa7213b9f')
TRIPLE = 'mips-mti-linux-gnu'
ARCH = ['-EL', '-march=mips32r2', '-mabi=32', '-mhard-float', '-mfp64', '-mnan=2008', '-mabs=2008']
# The same bytes from the same sources: no build dates (SOURCE_DATE_EPOCH, the release's own date, is
# read by OpenSSL's buildinf.h and busybox's banner) and no build paths in debug info or __FILE__.
# OpenSSL's and FFmpeg's version strings still name the compiler's path, so two machines agree only
# with the cache at the same path (Q2_THIRDPARTY_CACHE).
REPRODUCIBLE = [f'-ffile-prefix-map={CACHE}=/q2-thirdparty']

# name: (version, url, sha256). Each upstream signs its releases; docs/third-party.md lists the keys
# the hashes were checked against.
SOURCES = {
    'openssl': ('1.1.1w', 'https://github.com/openssl/openssl/releases/download/OpenSSL_1_1_1w/openssl-1.1.1w.tar.gz',
                'cf3098950cb4d853ad95c0841f1f9c6d3dc102dccfcacd521d93925208b76ac8'),
    'zlib': ('1.4', 'https://zlib.net/zlib-1.4.tar.gz',
             '02dd159783677a749c04adbdd02b55b8a2bcc7e054f51b76b549199a916ad584'),
    'curl': ('8.23.0', 'https://curl.se/download/curl-8.23.0.tar.xz',
             '8dadd7d2536a314f982ad05a651b713f426bf7d02cc2c76cffb9796f2218e6c5'),
    'mbedtls': ('3.6.8', 'https://github.com/Mbed-TLS/mbedtls/releases/download/mbedtls-3.6.8/mbedtls-3.6.8.tar.bz2',
                'bc54b818e87f49c0b3281dd85d24adaede0e63b4e822e3fbd49c005a38b9595b'),
    'busybox': ('1.38.0', 'https://busybox.net/downloads/busybox-1.38.0.tar.bz2',
                '34f9ea6ff8636f2c9241153b9114eefa9e65674a45318ae1ef95bb5f31c53bb2'),
    'libnl': ('3.5.0', 'https://github.com/thom311/libnl/releases/download/libnl3_5_0/libnl-3.5.0.tar.gz',
              '352133ec9545da76f77e70ccb48c9d7e5324d67f6474744647a7ed382b5e05fa'),
    'wpa_supplicant': ('2.12', 'https://w1.fi/releases/wpa_supplicant-2.12.tar.gz',
                       '08e23937e16d0155e55cab2b51f51fbe10d80a1aa91c4e15442645059b737ef6'),
    'ffmpeg': ('4.2.11', 'https://ffmpeg.org/releases/ffmpeg-4.2.11.tar.xz',
               'd53f0ec8ebb8b03a9ca1efcb93f38ba3f1137b3dcc4ad3239809d511553c97f8'),
    'sqlite': ('3.54.1', 'https://www.sqlite.org/2026/sqlite-autoconf-3540100.tar.gz',
               '926ef8d3eab93fbf6e06011901273eec076b25594a4e1cdadd50c987f4f6d64e'),
    'libpng': ('1.2.59', 'https://download.sourceforge.net/libpng/libpng-1.2.59.tar.xz',
               'b4635f15b8adccc8ad0934eea485ef59cc4cae24d0f0300a9a941e51974ffcc7'),
    # Vamp's own bluetoothd (docs/bluez.md), installed only by a build made with --own-bluez: upstream BlueZ, compiled
    # against GLib's and D-Bus's headers for the versions the rootfs carries (the two entries below: headers only)
    'bluez': ('5.88', 'https://www.kernel.org/pub/linux/bluetooth/bluez-5.88.tar.xz',
              '6952c39eb5f8b0ee712cc50ab3bcff03be17c410dd97ca640f4849d9a1653ba3'),
    'glib': ('2.74.4', 'https://download.gnome.org/sources/glib/2.74/glib-2.74.4.tar.xz',
             '0e82da5ea129b4444227c7e4a9e598f7288d1994bf63f129c44b90cfd2432172'),
    'dbus': ('1.12.28', 'https://dbus.freedesktop.org/releases/dbus/dbus-1.12.28.tar.gz',
             '9da1e3f2b73f75eec0a9e4509d64be43909d1f2853fe809528a0a53984d76420'),
    # --dev builds' adbd (docs/adb.md), not a replacement: Debian's android-tools source, as Buildroot fetches it
    'adbd': ('4.2.2+git20130218', 'https://launchpad.net/ubuntu/+archive/primary/+files/android-tools_4.2.2+git20130218.orig.tar.xz',
             '9bfba987e1351b12aa983787b9ae4424ab752e9e646d8e93771538dc1e5d932f'),
    # Vamp's own bluealsa (docs/bluealsa.md), installed only by a build made with --own-bluealsa: upstream
    # bluez-alsa, its SBC, aptX and LDAC encoders linked in (sbc, freeaptx, ldac), compiled against the headers of the
    # libraries the rootfs carries (stocksdk, whose archive here is GLib's; BLUEALSA_HEADERS the rest)
    'bluealsa': ('5.0.0', 'https://github.com/arkq/bluez-alsa/archive/v5.0.0/bluez-alsa-5.0.0.tar.gz',
                 'e1249cbebd24925c977814f62adab71f2ebd771e28e6a4ac58bb1ac97ce3beb5'),
    'stocksdk': ('2.74.4', 'https://download.gnome.org/sources/glib/2.74/glib-2.74.4.tar.xz',
                 '0e82da5ea129b4444227c7e4a9e598f7288d1994bf63f129c44b90cfd2432172'),
    'sbc': ('2.1', 'https://www.kernel.org/pub/linux/bluetooth/sbc-2.1.tar.xz',
            '426633cabd7c798236443516dfa8335b47e004b0ef37ff107e0c7ead3299fcc2'),
    'ldac': ('2.0.2.3', 'https://github.com/EHfive/ldacBT/releases/download/v2.0.2.3/ldacBT-2.0.2.3.tar.gz',
             '4bd8eece78bb5c1361fab95743e7100506e2408a25c4a592a0f8d349746dc5b4'),
    'freeaptx': ('0.2.2', 'https://github.com/regularhunter/libfreeaptx/archive/0.2.2/libfreeaptx-0.2.2.tar.gz',
                 '5ab5ebddf3f2eb7ce47a505b87460b00fc1ede99c70010796d3575ab31ea80bf'),
    # --dev builds' squashfuse_ll, the card boot's mount of a VampOS image where the kernel has no loop device
    # (docs/boot.md#vampos-card-boot): squashfuse's low-level FUSE driver, libfuse 2 and LZO (the image's
    # compression) linked in. libfuse's release is signed by its maintainer; LZO's is not signed (its hash is the
    # one every distribution carries); squashfuse's is the digest GitHub records for its release asset.
    'squashfuse': ('0.6.5', 'https://github.com/vasi/squashfuse/releases/download/0.6.5/squashfuse-0.6.5.tar.gz',
                   '9ec44b8ee6309e09bdcdeba422edc745e876db16136acacb74ecc7e7131e5763'),
    'libfuse': ('2.9.9', 'https://github.com/libfuse/libfuse/releases/download/fuse-2.9.9/fuse-2.9.9.tar.gz',
                'd0e69d5d608cc22ff4843791ad097f554dd32540ddc9bed7638cc6fea7c1b4b5'),
    'lzo': ('2.10', 'https://www.oberhumer.com/opensource/lzo/download/lzo-2.10.tar.gz',
            'c0f892943208266f9b6543b3ae308fab6284c5c90e627931446fb49b4221a072'),
}


def run(*cmd, cwd=None, env=None, log=None):
    """Run a build command, its output to log (or inherited); stop the build on failure."""
    out = open(log, 'a') if log else None
    try:
        subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, check=True, stdout=out, stderr=subprocess.STDOUT if out else None)
    except subprocess.CalledProcessError as e:
        tail = pathlib.Path(log).read_text(errors='replace')[-3000:] if log else ''
        sys.exit(f'thirdparty: {" ".join(map(str, cmd))} failed ({e.returncode})\n{tail}')
    finally:
        if out: out.close()


def need(value, message):
    """value, or stop with message when it is None."""
    if value is None:
        sys.exit(f'thirdparty: {message}')
    return value


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def fetch(url, digest, name):
    """The file at url, downloaded once into the cache and checked against digest."""
    path = CACHE/'dl'/name
    if path.exists() and sha256(path) == digest:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f'thirdparty: fetching {url}', file=sys.stderr)
    part = path.with_suffix(path.suffix + '.part')
    with urllib.request.urlopen(url, timeout=600) as r, open(part, 'wb') as f:
        shutil.copyfileobj(r, f, 1 << 20)
    got = sha256(part)
    if got != digest:
        part.unlink()
        sys.exit(f'thirdparty: {url} has SHA-256 {got}, expected {digest}')
    part.rename(path)
    return path


def toolchain():
    """The toolchain's bin directory, unpacked on first use."""
    root = CACHE/'toolchain'
    bindir = root/TRIPLE/'2020.06-01/bin'
    stamp = root/'.sha256'
    if not (stamp.exists() and stamp.read_text() == TOOLCHAIN[1]):
        archive = fetch(*TOOLCHAIN, 'mti-linux-2020.06-01.tar.gz')
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        run('tar', '-C', root, '-xzf', archive)
        stamp.write_text(TOOLCHAIN[1])
    return bindir


def unpack(name, work):
    """A fresh copy of name's source tree in work/src."""
    version, url, digest = SOURCES[name]
    archive = fetch(url, digest, url.rsplit('/', 1)[-1])
    src = work/'src'
    shutil.rmtree(src, ignore_errors=True)
    src.mkdir(parents=True)
    with tarfile.open(archive) as t:
        top = {m.name.split('/', 1)[0] for m in t.getmembers()}
        if len(top) != 1:
            sys.exit(f'thirdparty: {archive.name} has no single top directory')
        t.extractall(src, filter='data')
    return src/top.pop()


def epoch(name):
    """The newest modification time in name's source archive: its release date."""
    version, url, digest = SOURCES[name]
    with tarfile.open(fetch(url, digest, url.rsplit('/', 1)[-1])) as t:
        return max(m.mtime for m in t.getmembers())


class Env:
    """Cross-compilation settings for a recipe: tool paths and flags, staged dependencies."""
    def __init__(self, deps, epoch):
        bindir = toolchain()
        self.bin = bindir
        self.cc = f'{bindir}/{TRIPLE}-gcc ' + ' '.join(ARCH + REPRODUCIBLE)
        self.deps = deps
        inc = ' '.join(f'-I{d}/usr/include' for d in deps.values())
        lib = ' '.join(f'-L{d}/usr/lib -Wl,-rpath-link,{d}/usr/lib' for d in deps.values())
        self.vars = dict(os.environ, CC=self.cc, CPP=self.cc + ' -E', AR=f'{bindir}/{TRIPLE}-ar',
                         RANLIB=f'{bindir}/{TRIPLE}-ranlib', STRIP=f'{bindir}/{TRIPLE}-strip',
                         NM=f'{bindir}/{TRIPLE}-nm', LD=f'{bindir}/{TRIPLE}-ld',
                         CFLAGS='-O2 -pipe', CPPFLAGS=inc, LDFLAGS=lib, SOURCE_DATE_EPOCH=str(epoch), TZ='UTC',
                         PKG_CONFIG_LIBDIR=':'.join(f'{d}/usr/lib/pkgconfig' for d in deps.values()) or '/nonexistent',
                         PKG_CONFIG_SYSROOT_DIR='')
        self.vars.pop('PKG_CONFIG_PATH', None)
        self.host = 'mipsel-linux-gnu'


# Recipes: each builds one component from src and installs it under stage (a DESTDIR with prefix
# /usr). A recipe's source text is part of its cache key, so editing one rebuilds that component.

# Parts of OpenSSL no program in the rootfs calls (check_abi proves it: each takes its exports with
# it), and where most advisories since 1.1.1w's end of life are: binary-field curves, SM2, DTLS, CMS,
# time stamping, certificate transparency. (Stock libcurl imports SRP, so it stays.)
OPENSSL_OMIT = ['no-ec2m', 'no-sm2', 'no-dtls', 'no-cms', 'no-ts', 'no-ct']


def build_openssl(src, stage, env, log):
    # Stock's configuration (strings in its libcrypto): linux-generic32, so no assembly, -O3.
    # OPENSSLDIR is /etc/ssl, where the rootfs keeps openssl.cnf; stock's named its build host.
    run('./Configure', 'linux-generic32', 'shared', '--prefix=/usr', '--openssldir=/etc/ssl',
        '--libdir=lib', '-DOPENSSL_USE_NODELETE', 'no-tests', *OPENSSL_OMIT, *ARCH, *REPRODUCIBLE, '-O3',
        cwd=src, env=dict(env.vars, CC=f'{env.bin}/{TRIPLE}-gcc', CFLAGS='', CPPFLAGS='', LDFLAGS=''), log=log)
    run('make', f'-j{JOBS}', cwd=src, env=env.vars, log=log)
    run('make', 'install_sw', f'DESTDIR={stage}', cwd=src, env=env.vars, log=log)


def build_zlib(src, stage, env, log):
    run('./configure', '--prefix=/usr', '--shared', cwd=src, env=dict(env.vars, CHOST=env.host), log=log)
    run('make', f'-j{JOBS}', 'libz.so.1.4', cwd=src, env=env.vars, log=log)
    run('make', 'install', f'DESTDIR={stage}', cwd=src, env=env.vars, log=log)


def autotools(src, stage, env, log, *options, make=()):
    """configure, make and make install for a cross build to stage."""
    run('./configure', f'--host={env.host}', '--prefix=/usr', '--disable-static', '--enable-shared', *options,
        cwd=src, env=env.vars, log=log)
    run('make', f'-j{JOBS}', *make, cwd=src, env=env.vars, log=log)
    run('make', 'install', f'DESTDIR={stage}', cwd=src, env=env.vars, log=log)


# mbedTLS features libcurl's TLS client does not use: the server side, DTLS, ciphers and modes no
# TLS 1.2 or 1.3 suite needs, certificate writing, self tests. Fewer bytes, less to attack.
MBEDTLS_UNSET = '''
    MBEDTLS_HAVE_TIME_DATE MBEDTLS_SSL_SRV_C MBEDTLS_SSL_PROTO_DTLS MBEDTLS_SSL_DTLS_ANTI_REPLAY
    MBEDTLS_SSL_DTLS_HELLO_VERIFY MBEDTLS_SSL_DTLS_CLIENT_PORT_REUSE MBEDTLS_SSL_DTLS_CONNECTION_ID
    MBEDTLS_SSL_DTLS_SRTP MBEDTLS_SSL_COOKIE_C MBEDTLS_SSL_TICKET_C MBEDTLS_SSL_CACHE_C
    MBEDTLS_CAMELLIA_C MBEDTLS_ARIA_C MBEDTLS_DES_C MBEDTLS_ECJPAKE_C MBEDTLS_KEY_EXCHANGE_ECJPAKE_ENABLED
    MBEDTLS_NIST_KW_C MBEDTLS_CMAC_C MBEDTLS_RIPEMD160_C MBEDTLS_LMS_C MBEDTLS_PKCS7_C
    MBEDTLS_CIPHER_MODE_CFB MBEDTLS_CIPHER_MODE_OFB MBEDTLS_CIPHER_MODE_XTS
    MBEDTLS_X509_CRT_WRITE_C MBEDTLS_X509_CSR_WRITE_C MBEDTLS_X509_CREATE_C MBEDTLS_X509_CSR_PARSE_C
    MBEDTLS_PEM_WRITE_C MBEDTLS_SELF_TEST MBEDTLS_VERSION_FEATURES
    MBEDTLS_PSA_CRYPTO_STORAGE_C MBEDTLS_PSA_ITS_FILE_C MBEDTLS_TIMING_C MBEDTLS_NET_C
    MBEDTLS_ECP_DP_SECP192R1_ENABLED MBEDTLS_ECP_DP_SECP224R1_ENABLED MBEDTLS_ECP_DP_SECP192K1_ENABLED
    MBEDTLS_ECP_DP_SECP224K1_ENABLED MBEDTLS_ECP_DP_SECP256K1_ENABLED MBEDTLS_ECP_DP_BP256R1_ENABLED
    MBEDTLS_ECP_DP_BP384R1_ENABLED MBEDTLS_ECP_DP_BP512R1_ENABLED MBEDTLS_ECP_DP_CURVE448_ENABLED
    MBEDTLS_CCM_C MBEDTLS_SHA3_C MBEDTLS_PKCS5_C MBEDTLS_PKCS12_C MBEDTLS_PK_RSA_ALT_SUPPORT
    MBEDTLS_KEY_EXCHANGE_PSK_ENABLED MBEDTLS_KEY_EXCHANGE_DHE_PSK_ENABLED MBEDTLS_KEY_EXCHANGE_ECDHE_PSK_ENABLED
    MBEDTLS_KEY_EXCHANGE_RSA_PSK_ENABLED MBEDTLS_KEY_EXCHANGE_ECDH_ECDSA_ENABLED MBEDTLS_KEY_EXCHANGE_ECDH_RSA_ENABLED
    MBEDTLS_SSL_TLS1_3_KEY_EXCHANGE_MODE_PSK_ENABLED MBEDTLS_SSL_RENEGOTIATION
'''.split()


def build_mbedtls(src, stage, env, log):
    # A static, position-independent library linked into libcurl only (docs/third-party.md#curl-and-mbedtls).
    # Without MBEDTLS_HAVE_TIME_DATE no certificate's dates are checked: the Q2's clock may be unset
    # or years off, and past 2038 its 32-bit time wraps.
    for option in MBEDTLS_UNSET:
        run('python3', 'scripts/config.py', 'unset', option, cwd=src, log=log)
    run('make', f'-j{JOBS}', '-C', 'library', 'static', cwd=src,
        env=dict(env.vars, CFLAGS='-O2 -fPIC -ffunction-sections -fdata-sections'), log=log)
    for d in ('include', 'lib'):
        (stage/'usr'/d).mkdir(parents=True, exist_ok=True)
    shutil.copytree(src/'include', stage/'usr/include', dirs_exist_ok=True)
    for a in ('libmbedtls.a', 'libmbedx509.a', 'libmbedcrypto.a'):
        shutil.copy(src/'library'/a, stage/'usr/lib'/a)


def build_curl(src, stage, env, log):
    # Stock's libcurl 7.73.0 was built with OpenSSL 1.1.1, which curl 8.18 and later do not support,
    # so TLS is mbedTLS, linked in statically and kept out of libcurl's exports (gc-sections drops
    # what curl does not call). Symbols stay versioned CURL_OPENSSL_4, the version demo imports.
    # Protocols: http, https and file, the schemes demo and the payload use (stock built 18 more);
    # no NTLM, Kerberos or SPNEGO; no DNS over HTTPS, Alt-Svc, MIME posts or .netrc, none of which demo
    # sets (it imports the easy and slist calls alone). CA paths come from the caller (CURLOPT_CAINFO), as stock's.
    env.vars['CFLAGS'] += ' -ffunction-sections -fdata-sections'
    env.vars['LDFLAGS'] += ' -Wl,--gc-sections'
    autotools(src, stage, env, log, f'--with-mbedtls={env.deps["mbedtls"]}/usr', f'--with-zlib={env.deps["zlib"]}/usr',
              '--enable-versioned-symbols=OPENSSL_', '--enable-threaded-resolver', '--enable-ipv6',
              *(f'--disable-{p}' for p in ('dict', 'ftp', 'gopher', 'imap', 'pop3', 'rtsp', 'smb', 'smtp', 'telnet',
                                           'tftp', 'mqtt', 'ipfs', 'websockets', 'ldap', 'ldaps', 'ntlm',
                                           'kerberos-auth', 'negotiate-auth', 'aws', 'docs', 'doh', 'alt-svc',
                                           'mime', 'form-api', 'netrc')),
              '--without-libpsl', '--without-brotli', '--without-zstd', '--without-nghttp2', '--without-libidn2',
              '--without-libssh2', '--without-librtmp', '--without-ca-bundle', '--without-ca-path',
              '--without-ca-fallback')


def build_sqlite(src, stage, env, log):
    # Stock's options (sqlite3_compileoption_get): FTS3, R*Tree, synchronous FULL, the system malloc.
    # Nothing in the rootfs uses JSON, the math functions or extensions, which 3.19.3 did not build in.
    autotools(src, stage, env, log, '--fts3', '--rtree', '--disable-json', '--disable-math', '--disable-load-extension',
              '--disable-readline', '--disable-static-shell')


def build_libpng(src, stage, env, log):
    autotools(src, stage, env, log)


def build_libnl(src, stage, env, log):
    # Only to compile and link wpa_supplicant against: the rootfs keeps stock's libnl 3 (the same
    # sonames, libnl-3.so.200 and libnl-genl-3.so.200; check_abi confirms stock's provides every import).
    run('./configure', f'--host={env.host}', '--prefix=/usr', '--disable-static', '--disable-cli', '--disable-debug',
        # configure insists on flex and bison, which only libnl-route's grammars (not built) use
        cwd=src, env=dict(env.vars, FLEX='flex', YACC='bison -y'), log=log)
    run('make', f'-j{JOBS}', 'lib/libnl-3.la', 'lib/libnl-genl-3.la', cwd=src, env=env.vars, log=log)
    (stage/'usr/lib').mkdir(parents=True)
    shutil.copytree(src/'include/netlink', stage/'usr/include/libnl3/netlink')
    for so in ('libnl-3.so', 'libnl-genl-3.so'):
        real = sorted((src/'lib/.libs').glob(so + '.200.*'))[0]
        shutil.copy(real, stage/'usr/lib'/(so + '.200'))
        (stage/'usr/lib'/so).symlink_to(so + '.200')


# Stock 2.9's features, as its strings show them: nl80211 and wext, WPA2/WPA3 (SAE, OWE), WEP, the
# common EAP methods over OpenSSL, WPS, 802.11r, Hotspot 2.0, AP and mesh modes, TDLS, FILS,
# autoscan and bgscan; no D-Bus (the rootfs's has no D-Bus service for it), no P2P or DPP.
WPA_CONFIG = '''
CONFIG_DRIVER_WEXT=y
CONFIG_DRIVER_NL80211=y
CONFIG_LIBNL32=y
CONFIG_IEEE8021X_EAPOL=y
CONFIG_EAP_MD5=y
CONFIG_EAP_MSCHAPV2=y
CONFIG_EAP_TLS=y
CONFIG_EAP_PEAP=y
CONFIG_EAP_TTLS=y
CONFIG_EAP_GTC=y
CONFIG_EAP_OTP=y
CONFIG_EAP_LEAP=y
CONFIG_WPS=y
CONFIG_PKCS12=y
CONFIG_IPV6=y
CONFIG_CTRL_IFACE=y
CONFIG_SAE=y
CONFIG_OWE=y
CONFIG_WEP=y
CONFIG_BACKEND=file
CONFIG_IEEE80211R=y
CONFIG_IEEE80211AC=y
CONFIG_DEBUG_SYSLOG=y
CONFIG_INTERWORKING=y
CONFIG_HS20=y
CONFIG_AP=y
CONFIG_MESH=y
CONFIG_TDLS=y
CONFIG_FILS=y
CONFIG_AUTOSCAN_EXPONENTIAL=y
CONFIG_AUTOSCAN_PERIODIC=y
CONFIG_BGSCAN_SIMPLE=y
CONFIG_BGSCAN_LEARN=y
CONFIG_TLS=openssl
CONFIG_BUILD_WPA_CLIENT_SO=y
'''


def build_wpa_supplicant(src, stage, env, log):
    d = src/'wpa_supplicant'
    ssl, nl = env.deps['openssl'], env.deps['libnl']
    (d/'.config').write_text(WPA_CONFIG + f'CFLAGS += -I{ssl}/usr/include -I{nl}/usr/include/libnl3\n'
                             f'LIBS += -L{ssl}/usr/lib -L{nl}/usr/lib\n')
    make = {k: v for k, v in env.vars.items() if k not in ('CFLAGS', 'CPPFLAGS', 'LDFLAGS')}
    make.update(PKG_CONFIG='false', CFLAGS='-O2 -pipe -MMD')  # from the environment, so .config's CFLAGS += add to it
    run('make', f'-j{JOBS}', 'wpa_supplicant', 'wpa_cli', 'libwpa_client.so', f'CC={env.cc}', 'V=1', cwd=d, env=make, log=log)
    (stage/'usr/sbin').mkdir(parents=True)
    (stage/'usr/lib').mkdir(parents=True)
    for exe in ('wpa_supplicant', 'wpa_cli'):
        shutil.copy(d/exe, stage/'usr/sbin'/exe)
    shutil.copy(d/'libwpa_client.so', stage/'usr/lib/libwpa_client.so')


# Stock FFmpeg 4.2.2's configuration (ffmpeg -version, a buildroot build) less the build-host paths.
# A 4.2 point release keeps every library's version (libavcodec 58.54.100 and the rest).
FFMPEG_CONFIG = '''
    --disable-static --enable-shared --prefix=/usr --enable-avfilter --disable-version3 --enable-logging
    --enable-optimizations --disable-extra-warnings --enable-avdevice --enable-avcodec --enable-avformat
    --enable-network --disable-gray --enable-swscale-alpha --disable-small --enable-dct --enable-fft
    --enable-mdct --enable-rdft --disable-crystalhd --disable-dxva2 --enable-runtime-cpudetect
    --disable-hardcoded-tables --disable-mipsdsp --disable-mipsdspr2 --disable-msa --enable-hwaccels
    --disable-cuda --disable-cuvid --disable-nvenc --disable-avisynth --disable-frei0r
    --disable-libopencore-amrnb --disable-libopencore-amrwb --disable-libdc1394 --disable-libgsm
    --disable-libilbc --disable-libvo-amrwbenc --disable-symver --disable-doc --disable-gpl --disable-nonfree
    --enable-ffmpeg --disable-ffplay --enable-avresample --disable-ffprobe --disable-postproc --enable-swscale
    --enable-indevs --disable-alsa --enable-outdevs --enable-pthreads --enable-zlib --disable-bzlib
    --disable-libfdk-aac --disable-libcdio --disable-gnutls --enable-openssl --disable-libdrm
    --disable-libopenh264 --disable-vaapi --disable-vdpau --disable-mmal --disable-omx --disable-omx-rpi
    --disable-libopencv --disable-libopus --disable-libvpx --disable-libass --disable-libbluray --disable-libmfx
    --disable-librtmp --disable-libmp3lame --disable-libmodplug --disable-libspeex --disable-libtheora
    --disable-libwavpack --disable-iconv --disable-libfreetype --disable-fontconfig --disable-libopenjpeg
    --disable-libx264 --disable-libx265 --disable-libdav1d --disable-x86asm --disable-mmx --disable-sse
    --disable-sse2 --disable-sse3 --disable-ssse3 --disable-sse4 --disable-sse42 --disable-avx --disable-avx2
    --disable-armv6 --disable-armv6t2 --disable-vfp --disable-neon --enable-mipsfpu --disable-altivec
    --extra-libs=-latomic --enable-pic --cpu=generic
'''.split()


def build_ffmpeg(src, stage, env, log):
    ssl, z = env.deps['openssl'], env.deps['zlib']
    run('./configure', '--enable-cross-compile', f'--cross-prefix={env.bin}/{TRIPLE}-', '--arch=mipsel',
        '--target-os=linux', '--host-cc=gcc', f'--cc={env.cc}', '--pkg-config=pkg-config', *FFMPEG_CONFIG,
        f'--extra-cflags=-I{ssl}/usr/include -I{z}/usr/include',
        f'--extra-ldflags=-L{ssl}/usr/lib -L{z}/usr/lib -Wl,-rpath-link,{ssl}/usr/lib:{z}/usr/lib',
        # FFmpeg 4.2 finds OpenSSL 1.1 by its openssl.pc alone (its fallback test names 1.0's SSL_library_init)
        cwd=src, env=dict(env.vars, PKG_CONFIG_LIBDIR=f'{ssl}/usr/lib/pkgconfig', PKG_CONFIG_SYSROOT_DIR=str(ssl)), log=log)
    run('make', f'-j{JOBS}', cwd=src, env=env.vars, log=log)
    run('make', 'install', f'DESTDIR={stage}', cwd=src, env=env.vars, log=log)


# Stock busybox 1.31.1's applets (busybox --list); the rebuilt one has exactly these.
BUSYBOX_APPLETS = '''
    [ [[ addgroup adduser ar arch arp arping ash awk base64 basename bc blkid bunzip2 bzcat cat chattr
    chgrp chmod chown chroot chrt chvt cksum clear cmp cp cpio crond crontab cut date dc dd deallocvt
    delgroup deluser devmem df dhcprelay diff dirname dmesg dnsd dnsdomainname dos2unix du dumpkmap
    dumpleases echo egrep eject env ether-wake expr factor fallocate false fbset fdflush fdformat fdisk
    fgrep find flock fold free freeramdisk fsck fsfreeze fstrim fuser getopt getty grep gunzip gzip halt
    hdparm head hexdump hexedit hostid hostname hwclock i2cdetect i2cdump i2cget i2cset i2ctransfer id
    ifconfig ifdown ifup inetd init insmod install ip ipaddr ipcrm ipcs iplink ipneigh iproute iprule
    iptunnel kill killall killall5 last less link linux32 linux64 linuxrc ln loadfont loadkmap login
    logname losetup ls lsattr lsmod lsof lspci lsscsi lsusb lzcat lzma lzopcat makedevs md5sum mdev mesg
    microcom mkdir mkdosfs mke2fs mkfifo mknod mkpasswd mkswap mktemp modinfo more mount mountpoint mt
    mv nameif netstat nice nl nohup nologin nproc nslookup nuke od openvt partprobe passwd paste patch
    pgrep pidof ping ping6 pipe_progress pivot_root poweroff printenv printf ps pwd rdate readlink
    readprofile realpath reboot renice reset resize resume rm rmdir rmmod route rtcwake run-init
    run-parts runlevel sed seq setarch setconsole setfattr setkeycodes setlogcons setpriv setserial setsid
    sh sha1sum sha256sum sha3sum sha512sum shred sleep sort start-stop-daemon strings stty su sulogin
    svc svok swapoff swapon switch_root sync tail tar tc tee telnet test tftp time top touch tr
    traceroute true truncate ts tty ubirename udhcpc udhcpd uevent umount uname uniq unix2dos unlink
    unlzma unlzop unxz unzip uptime usleep uudecode uuencode vconfig vi vlock w watch watchdog wc wget
    which who whoami xargs xxd xz xzcat yes zcat
'''.split()


def build_busybox(src, stage, env, log):
    # defconfig (every applet, every feature) less the applets stock does not have: stock's set,
    # each with at least stock's features. Dynamically linked, as stock's.
    keep = set(BUSYBOX_APPLETS) | {'busybox'}  # busybox --list itself, which stock's lists not
    owner = {}  # applet -> the config symbol that builds it
    for f in src.rglob('*.c'):
        # //applet:IF_DEPMOD(IF_NOT_MODPROBE_SMALL(APPLET(depmod, ...))): each IF_ but the IF_NOT_ ones
        for chain, name in re.findall(r'//applet:\s*((?:IF_\w+\(\s*)+)APPLET\w*\(\s*([^,\s]+)', f.read_text(errors='replace')):
            owner.setdefault(name, set()).update(s for s in re.findall(r'IF_(\w+)', chain) if not s.startswith('NOT_'))
    run('make', 'defconfig', cwd=src, env=env.vars, log=log)
    applets = {}  # config symbol -> the applets it builds
    for name, syms in owner.items():
        for sym in syms:
            applets.setdefault(sym, set()).add(name)
    # A symbol is on when every applet it builds is stock's: INSMOD, not MODPROBE_SMALL (which adds modprobe and depmod).
    wanted = {sym for sym, names in applets.items() if names <= keep}
    config = (src/'.config').read_text()
    for sym in set().union(*owner.values()) - wanted:
        config = re.sub(rf'^CONFIG_{sym}=y$', f'# CONFIG_{sym} is not set', config, flags=re.M)
    for sym in wanted:  # defconfig leaves a few of stock's off (ar, nuke, unlzop)
        config = re.sub(rf'^# CONFIG_{sym} is not set$', f'CONFIG_{sym}=y', config, flags=re.M)
    flags = ' '.join(ARCH + REPRODUCIBLE)
    config = re.sub(r'^CONFIG_EXTRA_CFLAGS=.*$', f'CONFIG_EXTRA_CFLAGS="{flags}"', config, flags=re.M)
    config = re.sub(r'^CONFIG_EXTRA_LDFLAGS=.*$', f'CONFIG_EXTRA_LDFLAGS="{flags}"', config, flags=re.M)
    config = re.sub(r'^CONFIG_CROSS_COMPILER_PREFIX=.*$', f'CONFIG_CROSS_COMPILER_PREFIX="{env.bin}/{TRIPLE}-"', config, flags=re.M)
    (src/'.config').write_text(config)
    run('sh', '-c', 'yes "" | make oldconfig', cwd=src, env=env.vars, log=log)
    plain = {k: v for k, v in env.vars.items() if k not in ('CC', 'CPP', 'AR', 'RANLIB', 'STRIP', 'NM', 'LD', 'CFLAGS', 'CPPFLAGS', 'LDFLAGS')}
    run('make', f'-j{JOBS}', 'busybox', cwd=src, env=plain, log=log)
    names = (src/'include/applet_tables.h').read_text()
    table = need(re.search(r'applet_names\[\] ALIGN1 = ""(.*?);', names, re.S), 'busybox: no applet table').group(1)
    built = sorted(re.findall(r'^"([^"]+)" "\\0"$', table, re.M))
    if built != sorted(keep):
        sys.exit(f'thirdparty: busybox applets differ from stock: missing {sorted(keep - set(built))}, extra {sorted(set(built) - keep)}')
    (stage/'bin').mkdir(parents=True)
    shutil.copy(src/'busybox', stage/'bin/busybox')
    shutil.copy(src/'.config', stage/'busybox.config')


# adbd's other inputs: Debian's packaging, whose patches add adbd's non-Android build, and AOSP's libmincrypt
# (BSD-3-Clause) at the same release, android-4.2.2_r1, for the RSA check the Debian build left out
# (url, sha256). Then tools/adbd.patch, which carries Buildroot's fixes and the Q2's changes.
ADBD_DEBIAN = ('https://launchpad.net/ubuntu/+archive/primary/+files/android-tools_4.2.2+git20130218-3ubuntu41.debian.tar.gz',
               '73c3078de3e44d8a3cadf7a360863c63155d9d558c2f0933cf38ad901a3f5998')
ADBD_MINCRYPT = 'https://raw.githubusercontent.com/aosp-mirror/platform_system_core/android-4.2.2_r1/libmincrypt/'
ADBD_MINCRYPT_FILES = {'rsa.c': '865378dc08eb8ca78d0490016ad1ec75d4d1d9140548303f4b539760d01b2fda',
                       'rsa_e_3.c': '8f8ac513313a888ecbcf62a061596453041e19476ab130fb8e78c935db930c27',
                       'rsa_e_f4.c': 'bdebdf8ca1dd41a13162e9e1f4e20fa598b5c06191860a3deec383b23c5a3efe',
                       'sha.c': '439f002fcdf064e85e853e5bfec4fe6668adffe143f442a518e75de75f2b3720',
                       'NOTICE': 'b220fd3c79e0906eda798b23699305ab05576e849f45810eca74afcb2c7f2311'}
ADBD_PATCH = (pathlib.Path(__file__).resolve().parent/'adbd.patch').read_text()
# What adbd links, by directory under core/: Debian's adbd.mk less the services tools/adbd.patch removes.
ADBD_SOURCES = {'adbd': ['adb', 'fdevent', 'transport', 'transport_usb', 'transport_local', 'adb_auth_client',
                         'sockets', 'services', 'file_sync_service', 'usb_linux_client', 'utils', 'base64'],
                'libcutils': ['socket_inaddr_any_server', 'socket_local_client', 'socket_loopback_client',
                              'socket_loopback_server', 'socket_local_server', 'list'],
                'libmincrypt': ['rsa', 'rsa_e_3', 'rsa_e_f4', 'sha']}
ADBD_DEFINES = ['-DADB_HOST=0', '-DHAVE_FORKEXEC=1', '-D_XOPEN_SOURCE', '-D_GNU_SOURCE', '-DADBD_NON_ANDROID',
                '-DALLOW_ADBD_ROOT=1', '-DHAVE_SYMLINKS', '-DHAVE_TERMIO_H']


def adbd_tree(src, log):
    """src (android-tools' source) made adbd's: Debian's patches, libmincrypt, tools/adbd.patch."""
    with tarfile.open(fetch(*ADBD_DEBIAN, ADBD_DEBIAN[0].rsplit('/', 1)[-1])) as t:
        t.extractall(src, filter='data')
    for p in (src/'debian/patches/series').read_text().split():
        run('patch', '-p1', '-i', src/'debian/patches'/p, cwd=src, log=log)
    (src/'core/libmincrypt').mkdir()
    for name, digest in ADBD_MINCRYPT_FILES.items():
        shutil.copy(fetch(ADBD_MINCRYPT + name, digest, f'mincrypt-4.2.2_r1-{name}'), src/'core/libmincrypt'/name)
    (src/'q2-adbd.patch').write_text(ADBD_PATCH)
    run('patch', '-p1', '-i', 'q2-adbd.patch', cwd=src, log=log)


def build_adbd(src, stage, env, log):
    # -Os, unused sections dropped, linked against the toolchain's glibc 2.28, which is the rootfs's own build
    # (libc.so.6 and libpthread.so.0 alone): about 64 KB. Static, the same program is 617 KB, which the rootfs
    # has no room for.
    adbd_tree(src, log)
    objs = []
    for d, names in ADBD_SOURCES.items():
        for n in names:
            objs.append(src/f'{d}-{n}.o')
            run(*env.cc.split(), '-Os', '-ffunction-sections', '-fdata-sections', '-Wall', '-Wno-unused-parameter',
                *ADBD_DEFINES, f'-I{src}/core/adbd', f'-I{src}/core/include', '-c', src/'core'/d/f'{n}.c', '-o', objs[-1],
                cwd=src, log=log)
    (stage/'usr/bin').mkdir(parents=True)
    run(*env.cc.split(), '-Wl,--gc-sections', '-o', stage/'usr/bin/adbd', *objs, '-lpthread', cwd=src, log=log)
    shutil.copy(src/'core/libmincrypt/NOTICE', stage/'mincrypt.NOTICE')
    shutil.copy(src/'debian/copyright', stage/'copyright')


# bluetoothd links stock's GLib 2.74.4 and D-Bus 1.12 (and, for the stock base, Shanling's libbtctl.so and the
# libraries it leans on: BLUEZ_SHANLING_STUBS), which nothing here builds: it is compiled against their
# own headers (glibconfig.h as GLib's meson writes it for this target, tools/bluez/glibconfig.h;
# dbus-arch-deps.h filled in below) and linked against stubs that carry each library's soname and every
# symbol stock's copy exports (tools/bluez/stubs.tsv: library, name, kind, size, read from the stock rootfs),
# so the binary names the libraries the rootfs has. check_abi (`check-bluez`) then resolves its imports
# against the real ones, data objects' sizes included.
BLUEZ_DIR = pathlib.Path(__file__).resolve().parent/'bluez'
GLIBCONFIG = (BLUEZ_DIR/'glibconfig.h').read_text()
STUBS = (BLUEZ_DIR/'stubs.tsv').read_text()
# Vamp's changes to BlueZ that any base takes (docs/bluez.md#bases): [GATT] Server, so coda-rc keeps the LE
# attribute channel
BLUEZ_VAMP_PATCH = (BLUEZ_DIR/'vamp.patch').read_text()
# The base bluetoothd is built for (docs/bluez.md#bases). 'stock', today's rootfs: Shanling's hooks
# (tools/bluez/shanling.patch) go on top of vamp.patch, and bluetoothd links libbtctl.so and the libraries it
# leans on without naming them, as stock's does, against stubs of each (stub_library; libbtctl.so's symbols are
# in stubs.tsv). Any other base leaves the hooks and those libraries out.
BLUEZ_BASE = 'stock'
BLUEZ_SHANLING_PATCH = (BLUEZ_DIR/'shanling.patch').read_text()
BLUEZ_SHANLING_STUBS = ['libbtctl.so', 'libncurses.so.6', 'libgio-2.0.so.0', 'libgmodule-2.0.so.0',
                        'libgobject-2.0.so.0', 'libpcre2-8.so.0', 'libffi.so.8', 'libgthread-2.0.so.0']  # stock's NEEDED
BLUETOOTHD = 'usr/bin/bluetoothd'  # stock's, whose ELF flags and FP ABI the rebuild must have
# Where stock's bluetoothd keeps its adapter and device records, pairing keys among them (upstream's default
# is /var/lib/bluetooth, which is not on the Q2's writable partition): one store for both daemons, so a
# pairing made under either is there under the other (docs/bluez.md#the-pairing-store)
BLUEZ_STORAGE = '/mnt/data/bluetooth'
# The plugins left are stock's own set (hostname, autopair, policy,
# a2dp, avrcp, gap, scanparam, deviceinfo, battery) and hfp, rap, fastpair; no HID, HoG, network, LE Audio, ASHA, MIDI,
# OBEX, mesh, tools or client. hfp (the hands-free unit's side, new in 5.8x) stays compiled because 5.88
# does not link with --disable-hfp (media.c calls hfp_hf_sco_remove unguarded); it is an experimental
# profile, so it is left out at run time, with rap and fastpair, which have no configure switch:
# --noplugin=hfp,rap,fastpair (docs/bluez.md#build)
BLUEZ_OPTIONS = ['--sysconfdir=/etc', '--localstatedir=/var', '--libexecdir=/usr/libexec',
                 '--with-dbusconfdir=/etc', '--with-dbussystembusdir=/usr/share/dbus-1/system-services',
                 '--with-dbussessionbusdir=/usr/share/dbus-1/services',
                 *(f'--disable-{f}' for f in ('network', 'hid', 'hog', 'bap', 'bass', 'mcp', 'ccp', 'vcp', 'micp',
                                               'csip', 'tmap', 'gmap', 'asha', 'tools', 'monitor', 'udev',
                                               'cups', 'obex', 'client', 'systemd', 'manpages', 'datafiles', 'mesh',
                                               'midi', 'test'))]


def stub_library(env, lib, out):
    """A shared library named lib (its soname) under out defining what STUBS lists for it: each function an
    empty one, each data object as many bytes as stock's; for linking only, never installed."""
    out.mkdir(parents=True, exist_ok=True)
    c = out/f'{lib}.stub.c'
    lines = []
    for row in STUBS.splitlines():
        owner, name, kind, size = row.split('\t')
        if owner == lib:
            lines.append(f'void {name}(void) {{}}' if kind == 'FUNC' else f'char {name}[{size}];')
    c.write_text('\n'.join(lines) + '\n')
    run(*env.cc.split(), '-shared', '-fPIC', f'-Wl,-soname,{lib}', '-o', out/lib, c)
    base = lib.split('.so')[0] + '.so'
    if base != lib:
        (out/base).symlink_to(lib)


def build_glib(src, stage, env, log):
    # headers only, and a stub to link against (stub_library)
    inc = stage/'usr/include/glib-2.0'
    for d in ('glib', 'glib/deprecated', 'gobject'):
        (inc/d).mkdir(parents=True, exist_ok=True)
        for h in (src/d).glob('*.h'):
            shutil.copy(h, inc/d/h.name)
    shutil.move(inc/'glib/glib.h', inc/'glib.h')
    shutil.move(inc/'glib/glib-object.h', inc/'glib-object.h')
    (stage/'usr/lib/glib-2.0/include').mkdir(parents=True)
    (stage/'usr/lib/glib-2.0/include/glibconfig.h').write_text(GLIBCONFIG)
    stub_library(env, 'libglib-2.0.so.0', stage/'usr/lib')
    pc = stage/'usr/lib/pkgconfig'
    pc.mkdir()
    (pc/'glib-2.0.pc').write_text(f'prefix={stage}/usr\nName: GLib\nDescription: stock\'s GLib\nVersion: 2.74.4\n'
                                  'Libs: -L${prefix}/lib -lglib-2.0\n'
                                  'Cflags: -I${prefix}/include/glib-2.0 -I${prefix}/lib/glib-2.0/include\n')


def build_dbus(src, stage, env, log):
    # headers only, and a stub to link against (stub_library)
    inc = stage/'usr/include/dbus-1.0/dbus'
    inc.mkdir(parents=True)
    for h in (src/'dbus').glob('dbus*.h'):
        shutil.copy(h, inc/h.name)
    arch = (src/'dbus/dbus-arch-deps.h.in').read_text()
    for k, v in {'DBUS_HAVE_INT64': '#define DBUS_HAVE_INT64 1', 'DBUS_INT64_TYPE': 'long long',
                 'DBUS_INT64_CONSTANT': '(val##LL)', 'DBUS_UINT64_CONSTANT': '(val##ULL)', 'DBUS_INT32_TYPE': 'int',
                 'DBUS_INT16_TYPE': 'short', 'DBUS_MAJOR_VERSION': '1', 'DBUS_MINOR_VERSION': '12',
                 'DBUS_MICRO_VERSION': '28', 'DBUS_VERSION': '1.12.28'}.items():
        arch = arch.replace(f'@{k}@', v)
    (stage/'usr/lib/dbus-1.0/include/dbus').mkdir(parents=True)
    (stage/'usr/lib/dbus-1.0/include/dbus/dbus-arch-deps.h').write_text(arch)
    stub_library(env, 'libdbus-1.so.3', stage/'usr/lib')
    pc = stage/'usr/lib/pkgconfig'
    pc.mkdir()
    (pc/'dbus-1.pc').write_text(f'prefix={stage}/usr\nName: dbus\nDescription: stock\'s D-Bus\nVersion: 1.12.28\n'
                                'Libs: -L${prefix}/lib -ldbus-1\n'
                                'Cflags: -I${prefix}/include/dbus-1.0 -I${prefix}/lib/dbus-1.0/include\n')


def build_bluez(src, stage, env, log):
    # position independent, as stock's is: by hand, as configure's --enable-pie probe takes this compiler's
    # warning for a refusal and leaves the flags out
    vars = dict(env.vars, CFLAGS=f'{env.vars["CFLAGS"]} -fPIC', LDFLAGS=f'{env.vars["LDFLAGS"]} -pie -Wl,-z,now')
    (src/'vamp.patch').write_text(BLUEZ_VAMP_PATCH)
    run('patch', '-p1', '-i', 'vamp.patch', cwd=src, log=log)
    if BLUEZ_BASE == 'stock':
        (src/'shanling.patch').write_text(BLUEZ_SHANLING_PATCH)
        run('patch', '-p1', '-i', 'shanling.patch', cwd=src, log=log)
        run('autoreconf', '-fi', cwd=src, log=log)  # it adds sources and libraries to Makefile.am
        stubs = src/'q2-stubs'
        for lib in BLUEZ_SHANLING_STUBS:
            stub_library(env, lib, stubs)
        vars['LDFLAGS'] += f' -L{stubs}'
    run('./configure', f'--host={env.host}', '--prefix=/usr', *BLUEZ_OPTIONS, cwd=src, env=vars, log=log)
    config = src/'config.h'
    text, n = re.subn(r'^#define STORAGEDIR ".*"$', f'#define STORAGEDIR "{BLUEZ_STORAGE}"', config.read_text(), flags=re.M)
    need(n == 1 or None, 'bluez: config.h has no STORAGEDIR to point at the Q2\'s store')
    config.write_text(text)
    run('make', f'-j{JOBS}', cwd=src, env=vars, log=log)  # BUILT_SOURCES (lib/bluetooth) first
    (stage/'usr/bin').mkdir(parents=True)
    shutil.copy(src/'src/bluetoothd', stage/'usr/bin/bluetoothd')
    (stage/'etc/bluetooth').mkdir(parents=True)
    shutil.copy(src/'src/main.conf', stage/'etc/bluetooth/main.conf')  # upstream's commented defaults, for reference
    shutil.copy(src/'COPYING', stage/'COPYING')


# Vamp's own bluealsa (docs/bluealsa.md#build). It links the libraries the rootfs already has (GLib 2.74.4,
# D-Bus 1.12, ALSA 1.1.6, BlueZ 5.66's libbluetooth and fdk-aac 2.0.2), which nothing here
# builds: stocksdk is their headers, from each one's own release at the rootfs's version, with a pkg-config file
# apiece that links the stock library itself, read from an unpacked stock rootfs (STOCKFS) and refused unless it
# is the file V1.36 ships (BLUEALSA_STOCK_LIBS, SHA-256). So the binary names the sonames the rootfs has and
# imports only what its libraries export.
STOCKFS = pathlib.Path(os.environ.get('Q2_STOCKFS') or CACHE/'stockfs')
BLUEALSA_STOCK_LIBS = {
    'usr/lib/libglib-2.0.so.0.7400.4': 'aab8e379d595d98ee16c99a080a1caf98d243fe6e73922bdc46dd4dd6c6da0ce',
    'usr/lib/libgobject-2.0.so.0.7400.4': '1da22edc67df699643c7b13c36272f4261d88d251f917d01ca8de8cc66143196',
    'usr/lib/libgio-2.0.so.0.7400.4': '8897b15fdc9f2f11cf2ab6743f66ba485cbd1c527c1f5697251672cd3c102ae3',
    'usr/lib/libdbus-1.so.3.19.15': '13664b0698c933830c36087d5a3832f308ed4efe444952d5c8a147c8b9a8b952',
    'usr/lib/libasound.so.2.0.0': '9f125c2164028b8d728168168b9ad90cc38f6758d256ac57f3d87aab406eba46',
    'usr/lib/libbluetooth.so.3.19.8': '09f8aef89eda2924e6fd178beb3773847f5db85d5327364c4efb893220ef329f',
    'usr/lib/libfdk-aac.so.2.0.2': 'c15d57967908319945f1fb7e7d0583fdfdbea085cac71425b09c01199293d310'}
# The headers stocksdk takes beside GLib's: name: (url, sha256, the directory in the archive)
BLUEALSA_HEADERS = {
    'dbus': ('https://dbus.freedesktop.org/releases/dbus/dbus-1.12.28.tar.gz',
             '9da1e3f2b73f75eec0a9e4509d64be43909d1f2853fe809528a0a53984d76420', 'dbus-1.12.28'),
    'alsa': ('https://www.alsa-project.org/files/pub/lib/alsa-lib-1.1.6.tar.bz2',
             '5f2cd274b272cae0d0d111e8a9e363f08783329157e8dd68b3de0c096de6d724', 'alsa-lib-1.1.6'),
    'bluez': ('https://www.kernel.org/pub/linux/bluetooth/bluez-5.66.tar.xz',
              '39fea64b590c9492984a0c27a89fc203e1cdc74866086efb8f4698677ab2b574', 'bluez-5.66'),
    'fdk-aac': ('https://downloads.sourceforge.net/opencore-amr/fdk-aac-2.0.2.tar.gz',
                'c9e8630cf9d433f3cead74906a1520d2223f89bcd3fa9254861017440b8eb22f', 'fdk-aac-2.0.2'),
}
# What bluez-alsa is built with: the four codecs stock's has beside SBC (LDAC as a source only: the decoder
# is not in Sony's published library), bluealsa-aplay (Guest Play's player) and bluealsactl; nothing of
# oFono, UPower, MIDI, mSBC, LC3 or ASHA, none of which the rootfs has a library or a user for.
# The base bluealsa is built for: the recipe that stands for the root file system's own libraries. `stocksdk` is
# V1.36's; another base (docs/bluealsa.md#bases) is another recipe of the same shape, and nothing else changes.
BLUEALSA_BASE = 'stocksdk'
# Vamp's changes to bluez-alsa, each a patch file under tools/bluealsa with its reason at its head, in this order
BLUEALSA_PATCHES = {n: (pathlib.Path(__file__).resolve().parent/'bluealsa'/n).read_text()
                    for n in ('alsa-1.1.6-ioplug-avail.patch', 'shanling.patch')}
BLUEALSA_OPTIONS = ['--enable-aac', '--enable-aptx', '--enable-aptx-hd', '--with-libfreeaptx', '--enable-ldac',
                    '--enable-aplay', '--enable-ctl', '--disable-payloadcheck', '--disable-systemd',
                    # its per-device store (volume, mute, delay) on the tmpfs: /var/lib is read-only on the Q2, and
                    # the volume kept from one connection to the next is Vamp's own (BT_VOLUME)
                    '--localstatedir=/tmp/q2-bluealsa',
                    '--with-alsaplugindir=/usr/lib/alsa-lib', '--with-alsaconfdir=/usr/share/alsa/alsa.conf.d',
                    '--with-dbusconfdir=/etc/dbus-1/system.d']


def pkgconfig(stage, name, version, libs, cflags, requires=''):
    """stage's pkg-config file for name: libs and cflags as given, ${prefix} the stage's usr."""
    pc = stage/'usr/lib/pkgconfig'
    pc.mkdir(parents=True, exist_ok=True)
    (pc/f'{name}.pc').write_text(f'prefix={stage}/usr\nName: {name}\nDescription: {name} for the Q2\n'
                                 f'Version: {version}\nRequires: {requires}\nLibs: {libs}\nCflags: {cflags}\n')


def copy_headers(src, dst, pattern='*.h'):
    dst.mkdir(parents=True, exist_ok=True)
    for h in sorted(src.glob(pattern)):
        shutil.copy(h, dst/h.name)


def meson_files(text, name):
    """The file names in meson.build text's `name = files(...)` and each `name += files(...)` after it."""
    return [f for block in re.findall(rf'^{name} \+?= files\((.*?)\)', text, re.M | re.S)
            for f in re.findall(r"'([^']+)'", block)]


def build_stocksdk(src, stage, env, log):
    for lib, digest in BLUEALSA_STOCK_LIBS.items():
        need((STOCKFS/lib).is_file() or None, f'{STOCKFS/lib}: no stock rootfs to link against (unsquashfs one, or set Q2_STOCKFS)')
        need(sha256(STOCKFS/lib) == digest or None, f'{STOCKFS/lib} is not V1.36\'s')
    stock = f'-L{STOCKFS}/usr/lib'
    # GLib, GObject and GIO: the headers its meson build installs, the generated ones made as it makes them
    # (glibconfig.h for this target is tools/bluez/glibconfig.h; the enum headers by its own glib-mkenums)
    inc = stage/'usr/include/glib-2.0'
    for d in ('glib', 'glib/deprecated', 'gobject', 'gio'):
        copy_headers(src/d, inc/d)
    for top in ('glib/glib.h', 'glib/glib-object.h', 'glib/glib-unix.h'):
        shutil.move(inc/top, inc/pathlib.Path(top).name)
    shutil.copy(src/'gmodule/gmodule.h', inc/'gmodule.h')
    copy_headers(src/'gio', stage/'usr/include/gio-unix-2.0/gio', 'gunix*.h')
    (stage/'usr/lib/glib-2.0/include').mkdir(parents=True)
    (stage/'usr/lib/glib-2.0/include/glibconfig.h').write_text(GLIBCONFIG)
    tools = stage/'usr/bin'
    tools.mkdir(parents=True)
    mkenums = tools/'glib-mkenums'
    mkenums.write_text((src/'gobject/glib-mkenums.in').read_text().replace('@PYTHON@', 'python3').replace('@VERSION@', '2.74.4'))
    gio = (src/'gio/meson.build').read_text()
    gio_headers = [h for n in ('gio_headers', 'gdbus_headers', 'application_headers', 'settings_headers') for h in meson_files(gio, n)]
    need(len(gio_headers) > 150 or None, 'stocksdk: gio/meson.build does not list its headers as expected')
    for out, template, headers in ((inc/'gobject/glib-enumtypes.h', src/'gobject/glib-enumtypes.h.template', [src/'glib/gunicode.h']),
                                   (inc/'gio/gioenumtypes.h', src/'gio/gioenumtypes.h.template', [src/'gio'/h for h in gio_headers])):
        out.write_bytes(subprocess.run([sys.executable, mkenums, '--template', template, *headers],
                                       check=True, stdout=subprocess.PIPE).stdout)
    # gdbus-codegen, which bluez-alsa's own generator runs: GLib's, from the same release
    codegen = stage/'usr/share/glib-2.0/codegen'
    shutil.copytree(src/'gio/gdbus-2.0/codegen', codegen, ignore=shutil.ignore_patterns('*.in', 'meson.build'))
    (codegen/'config.py').write_text('VERSION = "2.74.4"\nMAJOR_VERSION = 2\nMINOR_VERSION = 74\n')
    # 2.74's sorts "since" versions with distutils' LooseVersion, and distutils is gone from Python 3.12 (the Mac
    # image's): the same ordering from a key of its own, numbers as numbers and the rest as text, as LooseVersion's
    utils = codegen/'utils.py'
    text = utils.read_text()
    need(text.count('import distutils.version\n') == 1 and text.count('distutils.version.LooseVersion(v)') == 1 or None,
         'stocksdk: gdbus-codegen\'s utils.py has no single distutils use to replace')
    utils.write_text(text.replace('import distutils.version\n', 'import re as _re\n\n\n'
                                  'def _loose(v):\n'
                                  '    return [int(p) if p.isdigit() else p for p in _re.split(r"(\\d+|[a-z]+|\\.)", v) if p and p != "."]\n')
                     .replace('distutils.version.LooseVersion(v)', '_loose(v)'))
    (tools/'gdbus-codegen').write_text(f'#!/usr/bin/env python3\nimport sys\nsys.path.insert(0, {str(codegen.parent)!r})\n'
                                       'from codegen import codegen_main\nsys.exit(codegen_main.codegen_main())\n')
    (tools/'gdbus-codegen').chmod(0o755)
    glib_c = '-I${prefix}/include/glib-2.0 -I${prefix}/lib/glib-2.0/include'
    pkgconfig(stage, 'glib-2.0', '2.74.4', f'{stock} -lglib-2.0', glib_c)
    pkgconfig(stage, 'gobject-2.0', '2.74.4', f'{stock} -lgobject-2.0', '', 'glib-2.0')
    pkgconfig(stage, 'gio-2.0', '2.74.4', f'{stock} -lgio-2.0', '', 'gobject-2.0')
    pkgconfig(stage, 'gio-unix-2.0', '2.74.4', '', '-I${prefix}/include/gio-unix-2.0', 'gio-2.0')
    # the stock libraries' own dependencies (libffi, libpcre2, libz), which the linker looks for and nothing links
    (stage/'ldflags').write_text(f'-Wl,-rpath-link,{STOCKFS}/usr/lib:{STOCKFS}/lib\n')
    extra = {}
    for name, (url, digest, top) in BLUEALSA_HEADERS.items():
        d = src.parent/name
        d.mkdir()
        with tarfile.open(fetch(url, digest, url.rsplit('/', 1)[-1])) as t:
            t.extractall(d, filter='data')
        extra[name] = d/top
    copy_headers(extra['dbus']/'dbus', stage/'usr/include/dbus-1.0/dbus', 'dbus*.h')
    arch = (extra['dbus']/'dbus/dbus-arch-deps.h.in').read_text()
    for k, v in {'DBUS_HAVE_INT64': '#define DBUS_HAVE_INT64 1', 'DBUS_INT64_TYPE': 'long long',
                 'DBUS_INT64_CONSTANT': '(val##LL)', 'DBUS_UINT64_CONSTANT': '(val##ULL)', 'DBUS_INT32_TYPE': 'int',
                 'DBUS_INT16_TYPE': 'short', 'DBUS_MAJOR_VERSION': '1', 'DBUS_MINOR_VERSION': '12',
                 'DBUS_MICRO_VERSION': '28', 'DBUS_VERSION': '1.12.28'}.items():
        arch = arch.replace(f'@{k}@', v)
    (stage/'usr/lib/dbus-1.0/include/dbus').mkdir(parents=True)
    (stage/'usr/lib/dbus-1.0/include/dbus/dbus-arch-deps.h').write_text(arch)
    pkgconfig(stage, 'dbus-1', '1.12.28', f'{stock} -ldbus-1', '-I${prefix}/include/dbus-1.0 -I${prefix}/lib/dbus-1.0/include')
    copy_headers(extra['alsa']/'include', stage/'usr/include/alsa')
    copy_headers(extra['alsa']/'include/sound', stage/'usr/include/alsa/sound')
    (stage/'usr/include/sys').mkdir()
    shutil.copy(extra['alsa']/'include/sys.h', stage/'usr/include/sys/asoundlib.h')
    pkgconfig(stage, 'alsa', '1.1.6', f'{stock} -lasound', '-I${prefix}/include')
    copy_headers(extra['bluez']/'lib', stage/'usr/include/bluetooth')
    pkgconfig(stage, 'bluez', '5.66', f'{stock} -lbluetooth', '-I${prefix}/include')
    for d in ('libSYS/include', 'libAACenc/include', 'libAACdec/include'):
        copy_headers(extra['fdk-aac']/d, stage/'usr/include/fdk-aac')
    pkgconfig(stage, 'fdk-aac', '2.0.2', f'{stock} -lfdk-aac', '-I${prefix}/include')


def build_sbc(src, stage, env, log):
    # a static library alone: the rootfs has no libsbc (stock's bluealsa has the encoder built in too)
    run('./configure', f'--host={env.host}', '--prefix=/usr', '--enable-static', '--disable-shared', '--with-pic',
        '--disable-tools', '--disable-tester', cwd=src, env=env.vars, log=log)
    run('make', f'-j{JOBS}', cwd=src, env=env.vars, log=log)
    run('make', 'install', f'DESTDIR={stage}', cwd=src, env=env.vars, log=log)
    pkgconfig(stage, 'sbc', '2.1', '-L${prefix}/lib -lsbc', '-I${prefix}/include')


def build_freeaptx(src, stage, env, log):
    # a static library alone, at the Makefile's own -O3: the aptX and aptX HD codec, the fork of libopenaptx 0.2.0
    # (docs/bluealsa.md#codecs-and-licences)
    (stage/'usr/lib').mkdir(parents=True)
    run(*env.cc.split(), '-O3', '-fPIC', '-I.', '-c', 'freeaptx.c', '-o', 'freeaptx.o', cwd=src, log=log)
    run(env.vars['AR'], 'rcs', stage/'usr/lib/libfreeaptx.a', 'freeaptx.o', cwd=src, log=log)
    copy_headers(src, stage/'usr/include', 'freeaptx.h')
    shutil.copy(src/'COPYING', stage/'COPYING.freeaptx')
    pkgconfig(stage, 'libfreeaptx', '0.2.2', '-L${prefix}/lib -lfreeaptx', '-I${prefix}/include')


def build_ldac(src, stage, env, log):
    # Sony's encoder and its adaptive bit rate library as Android publishes them, static: the rootfs's
    # libldacBT_ABR.so.2 is another build of it with another interface (its ldac_ABR_get_handle takes a library's
    # path and loads it), so upstream bluez-alsa cannot use it (docs/bluealsa.md#shanlings-changes). ldaclib.c and ldacBT.c
    # include the rest of src/, as Android's own build compiles them.
    lib = src/'libldac'
    (stage/'usr/lib').mkdir(parents=True)
    for name, sources in (('ldacBT_enc', ['src/ldaclib.c', 'src/ldacBT.c']), ('ldacBT_abr', ['abr/src/ldacBT_abr.c'])):
        objs = []
        for s in sources:
            objs.append(pathlib.Path(s).stem + '.o')
            run(*env.cc.split(), '-O2', '-fPIC', '-Iinc', '-Isrc', '-Iabr/inc', '-c', s, '-o', objs[-1], cwd=lib, log=log)
        run(env.vars['AR'], 'rcs', stage/f'usr/lib/lib{name}.a', *objs, cwd=lib, log=log)
    for h in ('inc/ldacBT.h', 'abr/inc/ldacBT_abr.h'):
        copy_headers(lib/pathlib.Path(h).parent, stage/'usr/include/ldac', pathlib.Path(h).name)
    for n in ('LICENSE', 'NOTICE'):
        shutil.copy(lib/n, stage/f'{n}.ldac')
    pkgconfig(stage, 'ldacBT-enc', '2.0.2.3', '-L${prefix}/lib -lldacBT_enc -lm', '-I${prefix}/include/ldac')
    pkgconfig(stage, 'ldacBT-abr', '2.0.2.3', '-L${prefix}/lib -lldacBT_abr', '-I${prefix}/include/ldac', 'ldacBT-enc')


def build_bluealsa(src, stage, env, log):
    # Nothing here names the stock rootfs: the base is whichever dependency carries the pkg-config files for
    # alsa, bluez, dbus-1, gio-unix-2.0, glib-2.0 and fdk-aac, gdbus-codegen in its usr/bin, and in `ldflags`
    # what else its libraries need at link time (BLUEALSA_BASE; docs/bluealsa.md#bases).
    base = env.deps[BLUEALSA_BASE]
    more = (base/'ldflags').read_text().strip() if (base/'ldflags').exists() else ''
    vars = dict(env.vars, PATH=f'{base}/usr/bin:{env.vars["PATH"]}', LDFLAGS=f'{env.vars["LDFLAGS"]} {more}')
    for name, text in BLUEALSA_PATCHES.items():
        (src/name).write_text(text)
        run('patch', '-p1', '-i', name, cwd=src, log=log)
    run('autoreconf', '--install', '--force', cwd=src, env=vars, log=log)
    run('./configure', f'--host={env.host}', '--prefix=/usr', *BLUEALSA_OPTIONS, cwd=src, env=vars, log=log)
    run('make', f'-j{JOBS}', cwd=src, env=vars, log=log)
    run('make', 'install', f'DESTDIR={stage}', cwd=src, env=vars, log=log)
    shutil.copy(src/'LICENSE', stage/'LICENSE')

def build_lzo(src, stage, env, log):
    # A static library for squashfuse_ll only
    run('./configure', f'--host={env.host}', '--prefix=/usr', '--enable-static', '--disable-shared',
        cwd=src, env=env.vars, log=log)
    run('make', f'-j{JOBS}', cwd=src, env=env.vars, log=log)
    run('make', 'install', f'DESTDIR={stage}', cwd=src, env=env.vars, log=log)


def build_libfuse(src, stage, env, log):
    # A static library for squashfuse_ll only. No fusermount: squashfuse_ll runs as root, and libfuse then mounts
    # with mount(2) itself. No mtab: libfuse would otherwise run mount -f to record the mount in /etc/mtab.
    run('./configure', f'--host={env.host}', '--prefix=/usr', '--enable-static', '--disable-shared',
        '--enable-lib', '--disable-util', '--disable-example', '--disable-mtab',
        cwd=src, env=env.vars, log=log)
    run('make', f'-j{JOBS}', cwd=src, env=env.vars, log=log)
    run('make', 'install', f'DESTDIR={stage}', cwd=src, env=env.vars, log=log)


def build_squashfuse(src, stage, env, log):
    # squashfuse_ll alone, static (libc.a included: nothing for the rootfs's own libraries to break; -all-static, as
    # libtool drops a plain -static for a program), LZO only, one
    # thread (one card to read, and less memory on a Q2 with 115 MB). LIBS puts libfuse.a before the libraries it needs;
    # given --with-fuse, configure leaves FUSE_USE_VERSION unset, and libfuse 2's headers then give the 2.1 API.
    lzo, fuse = env.deps['lzo'], env.deps['libfuse']
    run('./configure', f'--host={env.host}', '--prefix=/usr', '--enable-static', '--disable-shared',
        '--disable-high-level', '--disable-demo', '--disable-multithreading',
        '--without-zlib', '--without-xz', '--without-lz4', '--without-zstd',
        f'--with-lzo={lzo}/usr', f'--with-fuse={fuse}/usr',
        cwd=src, env=dict(env.vars, CPPFLAGS=env.vars['CPPFLAGS'] + ' -DFUSE_USE_VERSION=26',
                          LIBS='-lfuse -lpthread -ldl -lrt'), log=log)
    run('make', f'-j{JOBS}', 'squashfuse_ll', f"LDFLAGS={env.vars['LDFLAGS']} -all-static", cwd=src, env=env.vars, log=log)
    (stage/'usr/bin').mkdir(parents=True)
    shutil.copy(src/'squashfuse_ll', stage/'usr/bin/squashfuse_ll')
    shutil.copy(src/'LICENSE', stage/'squashfuse.LICENSE')


RECIPES = {'openssl': (build_openssl, []),
           'zlib': (build_zlib, []),
           'mbedtls': (build_mbedtls, []), 'curl': (build_curl, ['mbedtls', 'zlib']),
           'sqlite': (build_sqlite, []),
           'libpng': (build_libpng, ['zlib']),
           'busybox': (build_busybox, []),
           'libnl': (build_libnl, []), 'wpa_supplicant': (build_wpa_supplicant, ['openssl', 'libnl']),
           'ffmpeg': (build_ffmpeg, ['openssl', 'zlib']),
           'adbd': (build_adbd, []),
           'glib': (build_glib, []), 'dbus': (build_dbus, []), 'bluez': (build_bluez, ['glib', 'dbus']),
           'stocksdk': (build_stocksdk, []), 'sbc': (build_sbc, []), 'freeaptx': (build_freeaptx, []),
           'ldac': (build_ldac, []),
           'bluealsa': (build_bluealsa, [BLUEALSA_BASE, 'sbc', 'freeaptx', 'ldac']),
           'lzo': (build_lzo, []), 'libfuse': (build_libfuse, []), 'squashfuse': (build_squashfuse, ['lzo', 'libfuse'])}


# Each top-level function's and class's text as this module was imported (inspect.getsource rereads
# the file, which may have been edited since), for the cache key.
_TEXT = pathlib.Path(__file__).read_text()
CODE = {n.name: ast.get_source_segment(_TEXT, n) for n in ast.parse(_TEXT).body
        if isinstance(n, (ast.FunctionDef, ast.ClassDef))}


def key(name, deps):
    """The cache key: the source, the recipe's text and that of the module-level helpers and constants
    it names (autotools, BUSYBOX_APPLETS), the toolchain and flags, and each dependency's key."""
    fn, _ = RECIPES[name]
    parts = [SOURCES[name][2], TOOLCHAIN[1], ' '.join(ARCH + REPRODUCIBLE), CODE['Env'], *sorted(deps.items())]
    seen, todo = set(), [fn]
    while todo:
        f = todo.pop()
        if f.__name__ in seen:
            continue
        seen.add(f.__name__)
        parts.append(CODE[f.__name__])
        for n in f.__code__.co_names:
            v = globals().get(n)
            if inspect.isfunction(v) and v.__module__ == __name__ and n not in ('run', 'toolchain'):
                todo.append(v)
            elif isinstance(v, (str, list, tuple, dict)) and n not in ('SOURCES', 'TOOLCHAIN', 'ARCH', 'CACHE', 'JOBS'):
                parts.append(f'{n}={v!r}')
    h = hashlib.sha256()
    for part in parts:
        h.update(str(part).encode() + b'\0')
    return h.hexdigest()[:16]


def build(name, done=None):
    """name's staged install tree, built (with its dependencies) unless cached."""
    done = {} if done is None else done
    if name in done:
        return done[name]
    fn, needs = RECIPES[name]
    deps = {d: build(d, done) for d in needs}
    k = key(name, {d: p.name for d, p in deps.items()})
    stage = CACHE/'out'/f'{name}-{SOURCES[name][0]}-{k}'
    if not (stage/'.done').exists():
        work = CACHE/'work'/name
        shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        log = work/'build.log'
        print(f'thirdparty: building {name} {SOURCES[name][0]} (log {log})', file=sys.stderr)
        src = unpack(name, work)
        fn(src, stage, Env(deps, epoch(name)), log)
        (stage/'.done').write_text(json.dumps(dict(version=SOURCES[name][0], sha256=SOURCES[name][2])))  # the stage is whole
        shutil.rmtree(work)
    done[name] = stage
    return stage


class Elf:
    """What the dynamic linker sees of one ELF file: its flags, NEEDED, and dynamic symbols."""
    def __init__(self, path):
        self.path = path
        header = subprocess.run(['readelf', '-hdA', '-W', path], capture_output=True, text=True).stdout
        self.flags = int(need(re.search(r'Flags:\s+(0x[0-9a-f]+)', header), f'{path}: not an ELF file').group(1), 16)
        fp = re.search(r'^FP ABI: (.+)$', header, re.M) or re.search(r'Tag_GNU_MIPS_ABI_FP: (.+)$', header, re.M)
        self.fp = fp.group(1).strip() if fp else None
        self.needed = re.findall(r'\(NEEDED\)\s+Shared library: \[(.+)\]', header)
        self.runpath = [p for m in re.findall(r'\((?:RPATH|RUNPATH)\)\s+Library r(?:un)?path: \[(.*)\]', header)
                        for p in m.split(':') if p]
        self.defined, self.undefined = {}, []
        table = subprocess.run(['readelf', '--dyn-syms', '-W', path], capture_output=True, text=True).stdout
        for line in table.splitlines():
            f = line.split()
            if len(f) < 8 or not f[0][:-1].isdigit() or f[3] in ('SECTION', 'FILE') or f[4] == 'LOCAL':
                continue
            name, version = need(re.match(r'([^@\s]+)(?:@@?([^\s]+))?', f[7]), f'{path}: {line}').groups()
            if f[6] == 'UND':
                self.undefined.append((name, version, f[4] == 'WEAK', f[3], int(f[2], 0)))
            else:
                self.defined.setdefault(name, []).append((version, f[3], int(f[2], 0)))


def resolve(root, elfs, name, runpath):
    """The real path (relative to root) of library name as the dynamic linker would find it."""
    for d in [*runpath, '/lib', '/usr/lib']:
        p = root/d.lstrip('/')/name
        while p.is_symlink():
            target = os.readlink(p)
            p = (root/target.lstrip('/')) if target.startswith('/') else p.parent/target
        if p.exists():
            rel = os.path.relpath(os.path.normpath(p), root)
            if rel in elfs:
                return rel
    return None


def unresolved(root, elfs):
    """Every (file, symbol@version) a file imports that no library in its NEEDED closure defines, and every
    imported data object whose size differs from its definition's (a copy relocation would truncate it)."""
    problems = set()
    for rel, elf in elfs.items():
        closure, queue = [], [rel]
        while queue:
            cur = queue.pop(0)
            for n in elfs[cur].needed:
                lib = resolve(root, elfs, n, elfs[cur].runpath)
                if lib is None:
                    problems.add((rel, f'missing library {n}'))
                elif lib not in closure:
                    closure.append(lib)
                    queue.append(lib)
        for name, version, weak, kind, size in elf.undefined:
            defs = [d for lib in closure for d in elfs[lib].defined.get(name, ())
                    if version is None or d[0] == version]
            if not defs:
                if not weak:
                    problems.add((rel, f'{name}@{version}' if version else name))
            elif kind == 'OBJECT' and size and defs[0][2] != size:
                problems.add((rel, f'{name}: object size {size}, defined as {defs[0][2]}'))
    return problems


def elf_files(root):
    """Every regular ELF file under root, by path relative to it."""
    found = {}
    for dirpath, _, files in os.walk(root):
        for f in files:
            p = pathlib.Path(dirpath)/f
            if p.is_symlink() or not p.is_file():
                continue
            with open(p, 'rb') as fh:
                if fh.read(4) != b'\x7fELF':
                    continue
            found[os.path.relpath(p, root)] = p
    return found


def check_abi(stock_root, replacements):
    """Problems with replacing stock_root's files by replacements ({rootfs path: new file}): a header
    or FP ABI that differs from the stock file's, a symbol some file imports that the replacements
    leave undefined, a data object whose size changed. Problems stock already had (a plugin's import
    from its host program) are not counted. An empty list means the swap keeps every stock import
    satisfied."""
    stock_root = pathlib.Path(stock_root)
    files = elf_files(stock_root)
    before = {rel: Elf(p) for rel, p in files.items()}
    after = dict(before)
    problems = []
    for rel, new in replacements.items():
        e = Elf(new)
        old = before.get(rel)
        if old is None:
            problems.append(f'{rel}: no stock ELF file to replace')
            continue
        if e.flags != old.flags:
            problems.append(f'{rel}: ELF flags {e.flags:#x}, stock {old.flags:#x}')
        if e.fp != old.fp:
            problems.append(f'{rel}: FP ABI {e.fp}, stock {old.fp}')
        after[rel] = e
    known = unresolved(stock_root, before)
    problems += sorted(f'{rel}: {what}' for rel, what in unresolved(stock_root, after) - known)
    return problems


# Where each component's files go: {rootfs path (stock's name, which every symlink and DT_NEEDED
# reaches): path in the component's staged install}. A library keeps stock's file name whatever its
# new version, as the repack keeps every stock inode's name.
INSTALL = {
    'openssl': {'usr/lib/libssl.so.1.1': 'usr/lib/libssl.so.1.1', 'usr/lib/libcrypto.so.1.1': 'usr/lib/libcrypto.so.1.1',
                **{f'usr/lib/engines-1.1/{e}.so': f'usr/lib/engines-1.1/{e}.so' for e in ('afalg', 'capi', 'padlock')}},
    'zlib': {'usr/lib/libz.so.1.2.11': 'usr/lib/libz.so.1.4'},
    'curl': {'usr/lib/libcurl.so.4.7.0': 'usr/lib/libcurl.so.4.8.0'},
    'sqlite': {'usr/lib/libsqlite3.so.0.8.6': 'usr/lib/libsqlite3.so.3.54.1'},
    'libpng': {'usr/lib/libpng12.so.0.37.0': 'usr/lib/libpng12.so.0.59.0'},
    'busybox': {'bin/busybox': 'bin/busybox'},
    'wpa_supplicant': {f'usr/sbin/{n}': f'usr/sbin/{n}' for n in ('wpa_supplicant', 'wpa_cli')} |
                      {'usr/lib/libwpa_client.so': 'usr/lib/libwpa_client.so'},
    'ffmpeg': {'usr/bin/ffmpeg': 'usr/bin/ffmpeg'} |
              {f'usr/lib/{n}': f'usr/lib/{n}' for n in ('libavcodec.so.58.54.100', 'libavdevice.so.58.8.100',
               'libavfilter.so.7.57.100', 'libavformat.so.58.29.100', 'libavresample.so.4.0.0', 'libavutil.so.56.31.100',
               'libswresample.so.3.5.100', 'libswscale.so.5.5.100')},
}


def stripped(src, dst):
    """src without symbols or debug sections, written to dst."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    run(f'{toolchain()}/{TRIPLE}-strip', '--strip-unneeded', '-R', '.comment', '-R', '.note.gnu.build-id', '-o', dst, src)
    return dst


def replacements(out):
    """{rootfs path: stripped file under out/thirdparty} for every INSTALL entry, and the version of each
    component built for them, those linked in (mbedTLS) or against (libnl) included."""
    files, done = {}, {}
    for name, entries in INSTALL.items():
        stage = build(name, done)
        for path, rel in entries.items():
            files[path] = stripped(stage/rel, out/'thirdparty'/path)
    return files, {name: SOURCES[name][0] for name in done}


def install(p, out, cat, swap_inode, squashfs):
    """The pseudo file's definitions p with stock's outdated components swapped for their rebuilds, and the
    manifest's record of them. Stops the build when check_abi finds a problem."""
    files, versions = replacements(out)
    root = out/'stockfs'
    shutil.rmtree(root, ignore_errors=True)
    run('unsquashfs', '-q', '-no-xattrs', '-d', root, squashfs)
    problems = check_abi(root, files)
    if problems:
        sys.exit('thirdparty: the rebuilt libraries would break stock programs:\n' + '\n'.join(problems))
    record = {}
    for path, f in files.items():
        p = swap_inode(p, path.encode(), f)
        record[path] = dict(original_sha256=hashlib.sha256(cat(path)).hexdigest(), sha256=sha256(f))
    shutil.rmtree(root)
    return p, dict(versions=versions, files=record)


def main(argv):
    if argv[:1] == ['build']:
        for name in argv[1:] or list(RECIPES):
            print(name, build(name))
        return 0
    if argv[:1] == ['check'] and len(argv) == 2:
        import tempfile
        with tempfile.TemporaryDirectory(prefix='q2-thirdparty-') as d:
            problems = check_abi(argv[1], replacements(pathlib.Path(d))[0])
        print('\n'.join(problems) or 'ok')
        return 1 if problems else 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
