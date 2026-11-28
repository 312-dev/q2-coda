"""tools/thirdparty.py with no toolchain, network or stock rootfs: the build commands go to a recorder, downloads to
in-memory stand-ins, readelf's output is canned, and the cache is the test's tmp_path."""
import ast
import hashlib
import io
import json
import os
import pathlib
import subprocess
import tarfile
import types

import pytest

import thirdparty

ROOT = pathlib.Path(__file__).resolve().parents[2]

TC = pathlib.Path('/tc/bin')
GCC = f'{TC}/mips-mti-linux-gnu-'


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(thirdparty, 'CACHE', tmp_path/'cache')
    return tmp_path/'cache'


@pytest.fixture
def calls(monkeypatch):
    """thirdparty.run's calls, as (argv, cwd, env, log), none of them run."""
    seen = []
    monkeypatch.setattr(thirdparty, 'run', lambda *cmd, cwd=None, env=None, log=None: seen.append((list(cmd), cwd, env, log)))
    return seen


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(thirdparty, 'toolchain', lambda: TC)
    monkeypatch.setattr(thirdparty, 'JOBS', '7')
    monkeypatch.delenv('PKG_CONFIG_PATH', raising=False)
    deps = {d: pathlib.Path('/dep')/d for d in ('mbedtls', 'zlib', 'openssl', 'libnl')}
    return thirdparty.Env(deps, 1234)


def tarball(path, members):
    """A .tar.gz of members, (name, bytes or None for a directory, mtime)."""
    with tarfile.open(path, 'w:gz') as t:
        for name, data, mtime in members:
            info = tarfile.TarInfo(name)
            info.mtime = mtime
            if data is None:
                info.type = tarfile.DIRTYPE
                t.addfile(info)
            else:
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
    return path


# run, need, sha256

def test_run_appends_its_output_to_the_log(tmp_path, monkeypatch):
    log = tmp_path/'build.log'
    log.write_text('before\n')
    seen = []

    def fake(cmd, **kw):
        kw['stdout'].write('during\n')
        seen.append((cmd, kw))
    monkeypatch.setattr(subprocess, 'run', fake)
    thirdparty.run('make', pathlib.Path('/x'), 3, cwd=tmp_path, env={'A': '1'}, log=log)
    (cmd, kw), = seen
    assert cmd == ['make', '/x', '3']
    assert set(kw) == {'cwd', 'env', 'check', 'stdout', 'stderr'}
    assert kw['cwd'] == tmp_path and kw['env'] == {'A': '1'} and kw['check'] is True and kw['stderr'] == subprocess.STDOUT
    assert pathlib.Path(kw['stdout'].name) == log and kw['stdout'].closed
    assert log.read_text() == 'before\nduring\n'


def test_run_without_a_log_inherits_the_output(monkeypatch):
    seen = []
    monkeypatch.setattr(subprocess, 'run', lambda cmd, **kw: seen.append((cmd, kw)))
    thirdparty.run('true')
    assert seen == [(['true'], dict(cwd=None, env=None, check=True, stdout=None, stderr=None))]


def test_a_failed_command_stops_the_build_with_the_logs_tail(tmp_path, monkeypatch):
    log = tmp_path/'build.log'
    log.write_bytes(b'A' * 10 + b'\xff' + b'B' * 2999)  # a compiler's stray byte is no UTF-8
    files = []

    def fake(cmd, **kw):
        files.append(kw['stdout'])
        raise subprocess.CalledProcessError(2, cmd)
    monkeypatch.setattr(subprocess, 'run', fake)
    with pytest.raises(SystemExit) as e:
        thirdparty.run('make', '-j2', log=log)
    assert e.value.code == 'thirdparty: make -j2 failed (2)\n�' + 'B' * 2999
    assert files[0].closed


def test_a_failed_command_without_a_log(monkeypatch):
    def fake(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(subprocess, 'run', fake)
    with pytest.raises(SystemExit) as e:
        thirdparty.run('false', pathlib.Path('/a'))
    assert e.value.code == 'thirdparty: false /a failed (1)\n'


def test_need():
    assert thirdparty.need(0, 'unused') == 0 and thirdparty.need('x', 'unused') == 'x'
    with pytest.raises(SystemExit) as e:
        thirdparty.need(None, 'no table')
    assert e.value.code == 'thirdparty: no table'


def test_sha256_of_a_file_over_one_block(tmp_path):
    data = bytes(range(256)) * 4100 + b'end'  # past 1 MB, so read in two blocks
    (tmp_path/'f').write_bytes(data)
    assert thirdparty.sha256(tmp_path/'f') == hashlib.sha256(data).hexdigest()


# fetch, toolchain, unpack, epoch

class Download(io.BytesIO):
    """A response that checks, as it is read, that the bytes go to name.part."""
    def __init__(self, data, path):
        super().__init__(data)
        self.path = path

    def read(self, *args):
        assert self.path.with_name(self.path.name + '.part').exists()
        return super().read(*args)


@pytest.fixture
def server(monkeypatch, cache):
    """urlopen's stand-in: the bytes each URL serves, and the URLs asked for."""
    files, asked = {}, []

    def urlopen(url, timeout):
        assert timeout == 600
        asked.append(url)
        return Download(files[url], cache/'dl'/'pkg.tar.gz')
    monkeypatch.setattr(thirdparty.urllib.request, 'urlopen', urlopen)
    return files, asked


def test_fetch_downloads_once_and_checks_the_hash(server, cache, capsys):
    files, asked = server
    files['https://x.invalid/pkg.tar.gz'] = b'source'
    digest = hashlib.sha256(b'source').hexdigest()
    path = thirdparty.fetch('https://x.invalid/pkg.tar.gz', digest, 'pkg.tar.gz')
    assert path == cache/'dl/pkg.tar.gz' and path.read_bytes() == b'source'
    assert sorted(p.name for p in path.parent.iterdir()) == ['pkg.tar.gz']
    assert capsys.readouterr().err == 'thirdparty: fetching https://x.invalid/pkg.tar.gz\n'
    assert thirdparty.fetch('https://x.invalid/pkg.tar.gz', digest, 'pkg.tar.gz') == path
    assert asked == ['https://x.invalid/pkg.tar.gz'] and capsys.readouterr().err == ''


def test_fetch_replaces_a_cached_file_with_the_wrong_hash(server, cache):
    files, asked = server
    files['https://x.invalid/pkg.tar.gz'] = b'source'
    (cache/'dl').mkdir(parents=True)
    (cache/'dl/pkg.tar.gz').write_bytes(b'truncated')
    path = thirdparty.fetch('https://x.invalid/pkg.tar.gz', hashlib.sha256(b'source').hexdigest(), 'pkg.tar.gz')
    assert path.read_bytes() == b'source' and asked == ['https://x.invalid/pkg.tar.gz']


def test_fetch_refuses_a_download_with_another_hash(server, cache):
    files, _ = server
    files['https://x.invalid/pkg.tar.gz'] = b'tampered'
    digest = hashlib.sha256(b'source').hexdigest()
    with pytest.raises(SystemExit) as e:
        thirdparty.fetch('https://x.invalid/pkg.tar.gz', digest, 'pkg.tar.gz')
    assert e.value.code == (f'thirdparty: https://x.invalid/pkg.tar.gz has SHA-256 {hashlib.sha256(b"tampered").hexdigest()}, '
                            f'expected {digest}')
    assert list((cache/'dl').iterdir()) == []


def test_the_toolchain_is_unpacked_once(cache, calls, monkeypatch):
    fetched = []
    monkeypatch.setattr(thirdparty, 'fetch', lambda *a: fetched.append(a) or pathlib.Path('/dl/tc.tar.gz'))
    (cache/'toolchain').mkdir(parents=True)
    (cache/'toolchain/half-unpacked').write_text('')
    bindir = cache/'toolchain/mips-mti-linux-gnu/2020.06-01/bin'
    assert thirdparty.toolchain() == bindir
    assert fetched == [(*thirdparty.TOOLCHAIN, 'mti-linux-2020.06-01.tar.gz')]
    assert calls == [(['tar', '-C', cache/'toolchain', '-xzf', pathlib.Path('/dl/tc.tar.gz')], None, None, None)]
    assert sorted(p.name for p in (cache/'toolchain').iterdir()) == ['.sha256']
    assert (cache/'toolchain/.sha256').read_text() == thirdparty.TOOLCHAIN[1]
    assert thirdparty.toolchain() == bindir and len(fetched) == 1 and len(calls) == 1


def test_a_toolchain_of_another_release_is_replaced(cache, calls, monkeypatch):
    (cache/'toolchain').mkdir(parents=True)
    (cache/'toolchain/.sha256').write_text('0' * 64)
    monkeypatch.setattr(thirdparty, 'fetch', lambda *a: pathlib.Path('/dl/tc.tar.gz'))
    thirdparty.toolchain()
    assert len(calls) == 1 and (cache/'toolchain/.sha256').read_text() == thirdparty.TOOLCHAIN[1]


def test_the_first_toolchain_goes_into_an_empty_cache(cache, calls, monkeypatch):
    monkeypatch.setattr(thirdparty, 'fetch', lambda *a: pathlib.Path('/dl/tc.tar.gz'))
    assert not cache.exists()
    thirdparty.toolchain()
    assert (cache/'toolchain/.sha256').read_text() == thirdparty.TOOLCHAIN[1]


@pytest.fixture
def source(tmp_path, monkeypatch):
    """A source entry 'pkg' whose archive is the tarball the test writes to the returned path."""
    archive = tmp_path/'pkg-1.0.tar.gz'
    monkeypatch.setitem(thirdparty.SOURCES, 'pkg', ('1.0', 'https://x.invalid/dl/pkg-1.0.tar.gz', 'c' * 64))
    fetched = []

    def fetch(url, digest, name):
        fetched.append((url, digest, name))
        return archive
    monkeypatch.setattr(thirdparty, 'fetch', fetch)
    return archive, fetched


def test_unpack_gives_a_fresh_tree(source, tmp_path):
    archive, fetched = source
    tarball(archive, [('pkg-1.0', None, 1), ('pkg-1.0/configure', b'#!/bin/sh\n', 1), ('pkg-1.0/lib/a.c', b'int a;\n', 1)])
    work = tmp_path/'work'
    (work/'src/old').mkdir(parents=True)
    top = thirdparty.unpack('pkg', work)
    assert top == work/'src/pkg-1.0'
    assert sorted(p.name for p in (work/'src').iterdir()) == ['pkg-1.0']
    assert (top/'lib/a.c').read_bytes() == b'int a;\n'
    assert fetched == [('https://x.invalid/dl/pkg-1.0.tar.gz', 'c' * 64, 'pkg-1.0.tar.gz')]


def test_unpack_wants_one_top_directory(source, tmp_path):
    archive, _ = source
    tarball(archive, [('pkg-1.0/configure', b'', 1), ('stray.txt', b'', 1)])
    with pytest.raises(SystemExit) as e:
        thirdparty.unpack('pkg', tmp_path/'work')
    assert e.value.code == 'thirdparty: pkg-1.0.tar.gz has no single top directory'


def test_unpack_refuses_a_member_outside_the_tree(source, tmp_path):
    archive, _ = source
    tarball(archive, [('pkg-1.0/../../escaped', b'x', 1)])
    with pytest.raises(tarfile.FilterError):
        thirdparty.unpack('pkg', tmp_path/'work')
    assert not (tmp_path/'work/escaped').exists()


def test_epoch_is_the_newest_member(source):
    archive, fetched = source
    tarball(archive, [('pkg-1.0', None, 100), ('pkg-1.0/a', b'', 300), ('pkg-1.0/b', b'', 200)])
    assert thirdparty.epoch('pkg') == 300
    assert fetched == [('https://x.invalid/dl/pkg-1.0.tar.gz', 'c' * 64, 'pkg-1.0.tar.gz')]


# Env and the recipes

def test_env_with_dependencies(env, monkeypatch):
    flags = ' '.join(thirdparty.ARCH + thirdparty.REPRODUCIBLE)
    assert env.bin == TC and env.cc == f'{GCC}gcc {flags}' and env.host == 'mipsel-linux-gnu'
    v = env.vars
    assert (v['CC'], v['CPP']) == (env.cc, env.cc + ' -E')
    assert [v[k] for k in ('AR', 'RANLIB', 'STRIP', 'NM', 'LD')] == [GCC + t for t in ('ar', 'ranlib', 'strip', 'nm', 'ld')]
    assert v['CFLAGS'] == '-O2 -pipe' and v['SOURCE_DATE_EPOCH'] == '1234' and v['TZ'] == 'UTC'
    assert v['CPPFLAGS'] == '-I/dep/mbedtls/usr/include -I/dep/zlib/usr/include -I/dep/openssl/usr/include -I/dep/libnl/usr/include'
    assert v['LDFLAGS'] == ' '.join(f'-L/dep/{d}/usr/lib -Wl,-rpath-link,/dep/{d}/usr/lib' for d in ('mbedtls', 'zlib', 'openssl', 'libnl'))
    assert v['PKG_CONFIG_LIBDIR'] == ':'.join(f'/dep/{d}/usr/lib/pkgconfig' for d in ('mbedtls', 'zlib', 'openssl', 'libnl'))
    assert v['PKG_CONFIG_SYSROOT_DIR'] == ''


def test_env_without_dependencies_hides_the_hosts_pkg_config(monkeypatch):
    monkeypatch.setattr(thirdparty, 'toolchain', lambda: TC)
    monkeypatch.setenv('PKG_CONFIG_PATH', '/usr/lib/pkgconfig')
    monkeypatch.setenv('Q2_SOMETHING', 'kept')
    e = thirdparty.Env({}, 0)
    assert e.deps == {} and e.vars['CPPFLAGS'] == '' and e.vars['LDFLAGS'] == ''
    assert e.vars['PKG_CONFIG_LIBDIR'] == '/nonexistent' and 'PKG_CONFIG_PATH' not in e.vars
    assert e.vars['Q2_SOMETHING'] == 'kept' and e.vars['SOURCE_DATE_EPOCH'] == '0'


def test_openssl(env, calls):
    src, stage, log = pathlib.Path('/src'), pathlib.Path('/stage'), pathlib.Path('/log')
    thirdparty.build_openssl(src, stage, env, log)
    conf, make, install = calls
    assert conf[0] == ['./Configure', 'linux-generic32', 'shared', '--prefix=/usr', '--openssldir=/etc/ssl', '--libdir=lib',
                            '-DOPENSSL_USE_NODELETE', 'no-tests', 'no-ec2m', 'no-sm2', 'no-dtls', 'no-cms', 'no-ts', 'no-ct',
                            *thirdparty.ARCH, *thirdparty.REPRODUCIBLE, '-O3']
    assert conf[1:] == (src, dict(env.vars, CC=f'{GCC}gcc', CFLAGS='', CPPFLAGS='', LDFLAGS=''), log)
    assert make == (['make', '-j7'], src, env.vars, log)
    assert install == (['make', 'install_sw', 'DESTDIR=/stage'], src, env.vars, log)


def test_zlib(env, calls):
    src, log = pathlib.Path('/src'), pathlib.Path('/log')
    thirdparty.build_zlib(src, pathlib.Path('/stage'), env, log)
    assert calls == [(['./configure', '--prefix=/usr', '--shared'], src, dict(env.vars, CHOST='mipsel-linux-gnu'), log),
                     (['make', '-j7', 'libz.so.1.4'], src, env.vars, log),
                     (['make', 'install', 'DESTDIR=/stage'], src, env.vars, log)]


def configure(env, *options):
    return ['./configure', f'--host={env.host}', '--prefix=/usr', '--disable-static', '--enable-shared', *options]


def test_libpng(env, calls):
    src, log = pathlib.Path('/src'), pathlib.Path('/log')
    thirdparty.build_libpng(src, pathlib.Path('/stage'), env, log)
    assert calls == [(configure(env), src, env.vars, log), (['make', '-j7'], src, env.vars, log),
                     (['make', 'install', 'DESTDIR=/stage'], src, env.vars, log)]


def test_autotools_make_targets(env, calls):
    thirdparty.autotools(pathlib.Path('/src'), pathlib.Path('/stage'), env, None, '--x', make=('a', 'b'))
    assert [c[0] for c in calls] == [configure(env, '--x'), ['make', '-j7', 'a', 'b'], ['make', 'install', 'DESTDIR=/stage']]


def test_sqlite(env, calls):
    src, log = pathlib.Path('/src'), pathlib.Path('/log')
    thirdparty.build_sqlite(src, pathlib.Path('/stage'), env, log)
    assert calls == [(configure(env, '--fts3', '--rtree', '--disable-json', '--disable-math', '--disable-load-extension',
                                '--disable-readline', '--disable-static-shell'), src, env.vars, log),
                     (['make', '-j7'], src, env.vars, log), (['make', 'install', 'DESTDIR=/stage'], src, env.vars, log)]


def test_curl(env, calls):
    src, log = pathlib.Path('/src'), pathlib.Path('/log')
    ldflags = env.vars['LDFLAGS']
    thirdparty.build_curl(src, pathlib.Path('/stage'), env, log)
    disabled = ('dict', 'ftp', 'gopher', 'imap', 'pop3', 'rtsp', 'smb', 'smtp', 'telnet', 'tftp', 'mqtt', 'ipfs', 'websockets',
                'ldap', 'ldaps', 'ntlm', 'kerberos-auth', 'negotiate-auth', 'aws', 'docs', 'doh', 'alt-svc', 'mime', 'form-api', 'netrc')
    assert calls[0][0] == configure(env, '--with-mbedtls=/dep/mbedtls/usr', '--with-zlib=/dep/zlib/usr',
                                    '--enable-versioned-symbols=OPENSSL_', '--enable-threaded-resolver', '--enable-ipv6',
                                    *(f'--disable-{p}' for p in disabled),
                                    '--without-libpsl', '--without-brotli', '--without-zstd', '--without-nghttp2', '--without-libidn2',
                                    '--without-libssh2', '--without-librtmp', '--without-ca-bundle', '--without-ca-path',
                                    '--without-ca-fallback')
    # gc-sections drops the mbedTLS code curl never calls
    assert env.vars['CFLAGS'] == '-O2 -pipe -ffunction-sections -fdata-sections'
    assert env.vars['LDFLAGS'] == ldflags + ' -Wl,--gc-sections'
    assert [c[0] for c in calls[1:]] == [['make', '-j7'], ['make', 'install', 'DESTDIR=/stage']]
    assert [c[1:] for c in calls] == [(src, env.vars, log)] * 3


def test_mbedtls(env, calls, tmp_path):
    src, stage, log = tmp_path/'src', tmp_path/'stage', tmp_path/'log'
    (src/'include/mbedtls').mkdir(parents=True)
    (src/'include/mbedtls/ssl.h').write_text('ssl')
    (src/'library').mkdir()
    for a in ('libmbedtls.a', 'libmbedx509.a', 'libmbedcrypto.a', 'libother.a'):
        (src/'library'/a).write_text(a)
    thirdparty.build_mbedtls(src, stage, env, log)
    assert calls[:-1] == [(['python3', 'scripts/config.py', 'unset', o], src, None, log) for o in thirdparty.MBEDTLS_UNSET]
    assert 'MBEDTLS_HAVE_TIME_DATE' in thirdparty.MBEDTLS_UNSET
    assert calls[-1] == (['make', '-j7', '-C', 'library', 'static'], src,
                         dict(env.vars, CFLAGS='-O2 -fPIC -ffunction-sections -fdata-sections'), log)
    assert (stage/'usr/include/mbedtls/ssl.h').read_text() == 'ssl'
    assert sorted(p.name for p in (stage/'usr/lib').iterdir()) == ['libmbedcrypto.a', 'libmbedtls.a', 'libmbedx509.a']
    assert (stage/'usr/lib/libmbedx509.a').read_text() == 'libmbedx509.a'


def test_mbedtls_into_a_stage_that_has_its_directories(env, calls, tmp_path):
    src, stage = tmp_path/'src', tmp_path/'stage'
    (src/'include').mkdir(parents=True)
    (src/'library').mkdir()
    for a in ('libmbedtls.a', 'libmbedx509.a', 'libmbedcrypto.a'):
        (src/'library'/a).write_text(a)
    (stage/'usr/include').mkdir(parents=True)
    (stage/'usr/lib').mkdir()
    thirdparty.build_mbedtls(src, stage, env, None)
    assert (stage/'usr/lib/libmbedtls.a').exists()


def test_libnl(env, calls, tmp_path):
    src, stage, log = tmp_path/'src', tmp_path/'stage', tmp_path/'log'
    (src/'include/netlink').mkdir(parents=True)
    (src/'include/netlink/netlink.h').write_text('h')
    (src/'lib/.libs').mkdir(parents=True)
    for so in ('libnl-3.so', 'libnl-genl-3.so'):
        (src/'lib/.libs'/(so + '.200.26.0')).write_text(so + ' 26')
        (src/'lib/.libs'/(so + '.200.9.0')).write_text(so + ' 9')
    thirdparty.build_libnl(src, stage, env, log)
    assert calls == [(['./configure', '--host=mipsel-linux-gnu', '--prefix=/usr', '--disable-static', '--disable-cli', '--disable-debug'],
                      src, dict(env.vars, FLEX='flex', YACC='bison -y'), log),
                     (['make', '-j7', 'lib/libnl-3.la', 'lib/libnl-genl-3.la'], src, env.vars, log)]
    assert (stage/'usr/include/libnl3/netlink/netlink.h').read_text() == 'h'
    lib = stage/'usr/lib'
    assert sorted(p.name for p in lib.iterdir()) == ['libnl-3.so', 'libnl-3.so.200', 'libnl-genl-3.so', 'libnl-genl-3.so.200']
    for so in ('libnl-3.so', 'libnl-genl-3.so'):
        assert (lib/(so + '.200')).read_text() == so + ' 26'  # the first in sorted order
        assert (lib/so).readlink() == pathlib.Path(so + '.200')


def test_wpa_supplicant(env, calls, tmp_path):
    src, stage, log = tmp_path/'src', tmp_path/'stage', tmp_path/'log'
    d = src/'wpa_supplicant'
    d.mkdir(parents=True)
    for f in ('wpa_supplicant', 'wpa_cli', 'libwpa_client.so'):
        (d/f).write_text(f)
    thirdparty.build_wpa_supplicant(src, stage, env, log)
    assert (d/'.config').read_text() == (thirdparty.WPA_CONFIG + 'CFLAGS += -I/dep/openssl/usr/include -I/dep/libnl/usr/include/libnl3\n'
                                         'LIBS += -L/dep/openssl/usr/lib -L/dep/libnl/usr/lib\n')
    (cmd, cwd, make, logged), = calls
    assert cmd == ['make', '-j7', 'wpa_supplicant', 'wpa_cli', 'libwpa_client.so', f'CC={env.cc}', 'V=1']
    assert (cwd, logged) == (d, log)
    expected = {k: v for k, v in env.vars.items() if k not in ('CFLAGS', 'CPPFLAGS', 'LDFLAGS')}
    assert make == dict(expected, PKG_CONFIG='false', CFLAGS='-O2 -pipe -MMD')
    for f in ('usr/sbin/wpa_supplicant', 'usr/sbin/wpa_cli', 'usr/lib/libwpa_client.so'):
        assert (stage/f).read_text() == pathlib.Path(f).name


def test_ffmpeg(env, calls):
    src, log = pathlib.Path('/src'), pathlib.Path('/log')
    thirdparty.build_ffmpeg(src, pathlib.Path('/stage'), env, log)
    conf, make, install = calls
    assert conf[0] == ['./configure', '--enable-cross-compile', f'--cross-prefix={GCC}', '--arch=mipsel', '--target-os=linux',
                             '--host-cc=gcc', f'--cc={env.cc}', '--pkg-config=pkg-config', *thirdparty.FFMPEG_CONFIG,
                             '--extra-cflags=-I/dep/openssl/usr/include -I/dep/zlib/usr/include',
                             '--extra-ldflags=-L/dep/openssl/usr/lib -L/dep/zlib/usr/lib -Wl,-rpath-link,/dep/openssl/usr/lib:/dep/zlib/usr/lib']
    assert conf[1:] == (src, dict(env.vars, PKG_CONFIG_LIBDIR='/dep/openssl/usr/lib/pkgconfig', PKG_CONFIG_SYSROOT_DIR='/dep/openssl'), log)
    assert make == (['make', '-j7'], src, env.vars, log)
    assert install == (['make', 'install', 'DESTDIR=/stage'], src, env.vars, log)


# A busybox tree in miniature: modprobe-small builds insmod and depmod as well, so with stock's set (ar, insmod,
# ls) INSMOD is on and MODPROBE_SMALL off; defconfig leaves AR off.
APPLETS = {
    'coreutils/ls.c': '//applet:IF_LS(APPLET_NOEXEC(ls, ls, BB_DIR_BIN, BB_SUID_DROP, ls))\n',
    'archival/ar.c': '//applet: IF_AR(APPLET(ar, BB_DIR_USR_BIN, BB_SUID_DROP))\n/* (c) J\xf6rg, in Latin-1 */\n',
    'modutils/insmod.c': '//applet:IF_INSMOD(IF_NOT_MODPROBE_SMALL(APPLET_NOEXEC(insmod, insmod, BB_DIR_SBIN, BB_SUID_DROP, insmod)))\n',
    'modutils/modprobe-small.c': '//applet:IF_MODPROBE_SMALL(APPLET_NOEXEC(modprobe, modprobe, BB_DIR_SBIN, BB_SUID_DROP, modprobe))\n'
                                 '//applet:IF_DEPMOD(IF_MODPROBE_SMALL(APPLET_NOEXEC(depmod, modprobe, BB_DIR_SBIN, BB_SUID_DROP, modprobe)))\n'
                                 '//applet:IF_INSMOD(IF_MODPROBE_SMALL(APPLET_NOEXEC(insmod, modprobe, BB_DIR_SBIN, BB_SUID_DROP, modprobe)))\n',
}
DEFCONFIG = '''CONFIG_EXTRA_CFLAGS=""
CONFIG_EXTRA_LDFLAGS=""
CONFIG_CROSS_COMPILER_PREFIX=""
CONFIG_LS=y
CONFIG_FEATURE_LS_COLOR=y
# CONFIG_AR is not set
CONFIG_INSMOD=y
CONFIG_MODPROBE_SMALL=y
CONFIG_DEPMOD=y
# CONFIG_NOT_MODPROBE_SMALL is not set
'''


def busybox_tree(tmp_path, monkeypatch, calls, table):
    """src with APPLETS, and a run that answers defconfig with DEFCONFIG and the build with table's applets."""
    src = tmp_path/'src'
    for name, text in APPLETS.items():
        (src/name).parent.mkdir(parents=True, exist_ok=True)
        (src/name).write_text(text, encoding='latin-1')
    monkeypatch.setattr(thirdparty, 'BUSYBOX_APPLETS', ['ar', 'insmod', 'ls'])
    at_oldconfig = []

    def run(*cmd, cwd=None, env=None, log=None):
        calls.append((list(cmd), cwd, env, log))
        if cmd == ('make', 'defconfig'):
            (cwd/'.config').write_text(DEFCONFIG)
        elif cmd[0] == 'sh':
            at_oldconfig.append((cwd/'.config').read_text())
        elif cmd[-1] == 'busybox':
            (cwd/'include').mkdir()
            if table is not None:
                (cwd/'include/applet_tables.h').write_text(
                    'const char applet_names[] ALIGN1 = ""\n' + ''.join(f'"{n}" "\\0"\n' for n in table) + ';\n\nint x;\n')
            else:
                (cwd/'include/applet_tables.h').write_text('/* nothing */\n')
            (cwd/'busybox').write_text('elf')
    monkeypatch.setattr(thirdparty, 'run', run)
    return src, at_oldconfig


def test_busybox_builds_stocks_applets(env, tmp_path, monkeypatch):
    calls: list = []
    src, at_oldconfig = busybox_tree(tmp_path, monkeypatch, calls, ['ar', 'busybox', 'insmod', 'ls'])
    stage, log = tmp_path/'stage', tmp_path/'log'
    thirdparty.build_busybox(src, stage, env, log)
    flags = ' '.join(thirdparty.ARCH + thirdparty.REPRODUCIBLE)
    config = f'''CONFIG_EXTRA_CFLAGS="{flags}"
CONFIG_EXTRA_LDFLAGS="{flags}"
CONFIG_CROSS_COMPILER_PREFIX="{GCC}"
CONFIG_LS=y
CONFIG_FEATURE_LS_COLOR=y
CONFIG_AR=y
CONFIG_INSMOD=y
# CONFIG_MODPROBE_SMALL is not set
# CONFIG_DEPMOD is not set
# CONFIG_NOT_MODPROBE_SMALL is not set
'''
    assert at_oldconfig == [config]
    plain = {k: v for k, v in env.vars.items() if k not in ('CC', 'CPP', 'AR', 'RANLIB', 'STRIP', 'NM', 'LD', 'CFLAGS', 'CPPFLAGS', 'LDFLAGS')}
    assert calls == [(['make', 'defconfig'], src, env.vars, log), (['sh', '-c', 'yes "" | make oldconfig'], src, env.vars, log),
                     (['make', '-j7', 'busybox'], src, plain, log)]
    assert 'SOURCE_DATE_EPOCH' in plain and 'CC' not in plain
    assert (stage/'bin/busybox').read_text() == 'elf' and (stage/'busybox.config').read_text() == config


def test_busybox_stops_when_the_applets_differ(env, tmp_path, monkeypatch):
    src, _ = busybox_tree(tmp_path, monkeypatch, [], ['ls', 'modprobe', 'ar'])
    with pytest.raises(SystemExit) as e:
        thirdparty.build_busybox(src, tmp_path/'stage', env, None)
    assert e.value.code == "thirdparty: busybox applets differ from stock: missing ['busybox', 'insmod'], extra ['modprobe']"
    assert not (tmp_path/'stage').exists()


def test_busybox_stops_without_an_applet_table(env, tmp_path, monkeypatch):
    src, _ = busybox_tree(tmp_path, monkeypatch, [], None)
    with pytest.raises(SystemExit) as e:
        thirdparty.build_busybox(src, tmp_path/'stage', env, None)
    assert e.value.code == 'thirdparty: busybox: no applet table'


def test_adbd_tree_applies_debians_patches_mincrypt_and_the_q2_patch(tmp_path, calls, monkeypatch):
    src = tmp_path/'src'
    deb = tmp_path/'debian.tar.gz'
    tarball(deb, [('debian', None, 0), ('debian/patches', None, 0), ('debian/patches/series', b'a.patch\nb.patch\n', 0)])
    fetched = []
    def fetch(url, digest, name):
        fetched.append((url, digest, name))
        if url == thirdparty.ADBD_DEBIAN[0]:
            return deb
        f = tmp_path/name
        f.write_text(name)
        return f
    monkeypatch.setattr(thirdparty, 'fetch', fetch)
    (src/'core').mkdir(parents=True)
    log = tmp_path/'log'
    thirdparty.adbd_tree(src, log)
    assert fetched[0] == (*thirdparty.ADBD_DEBIAN, 'android-tools_4.2.2+git20130218-3ubuntu41.debian.tar.gz')
    assert fetched[1:] == [(thirdparty.ADBD_MINCRYPT + n, d, f'mincrypt-4.2.2_r1-{n}') for n, d in thirdparty.ADBD_MINCRYPT_FILES.items()]
    assert sorted(p.name for p in (src/'core/libmincrypt').iterdir()) == sorted(thirdparty.ADBD_MINCRYPT_FILES)
    assert (src/'q2-adbd.patch').read_text() == thirdparty.ADBD_PATCH
    assert calls == [(['patch', '-p1', '-i', src/'debian/patches/a.patch'], src, None, log),
                     (['patch', '-p1', '-i', src/'debian/patches/b.patch'], src, None, log),
                     (['patch', '-p1', '-i', 'q2-adbd.patch'], src, None, log)]


def test_adbd_is_one_program_against_glibc(tmp_path, env, calls, monkeypatch):
    src, stage, log = tmp_path/'src', tmp_path/'stage', tmp_path/'log'
    def tree(s, l):
        (s/'core/libmincrypt').mkdir(parents=True)
        (s/'core/libmincrypt/NOTICE').write_text('bsd')
        (s/'debian').mkdir()
        (s/'debian/copyright').write_text('apache')
    monkeypatch.setattr(thirdparty, 'adbd_tree', tree)
    thirdparty.build_adbd(src, stage, env, log)
    n = sum(len(v) for v in thirdparty.ADBD_SOURCES.values())
    compiles, link = calls[:n], calls[n]
    assert len(calls) == n + 1
    assert compiles[0][0] == [*env.cc.split(), '-Os', '-ffunction-sections', '-fdata-sections', '-Wall', '-Wno-unused-parameter',
                              *thirdparty.ADBD_DEFINES, f'-I{src}/core/adbd', f'-I{src}/core/include', '-c',
                              src/'core/adbd/adb.c', '-o', src/'adbd-adb.o']
    assert compiles[-1][0][-3:] == [src/'core/libmincrypt/sha.c', '-o', src/'libmincrypt-sha.o']
    assert link[0][:len(env.cc.split()) + 3] == [*env.cc.split(), '-Wl,--gc-sections', '-o', stage/'usr/bin/adbd']
    assert link[0][-1] == '-lpthread' and len(link[0]) == len(env.cc.split()) + 3 + n + 1
    assert (stage/'mincrypt.NOTICE').read_text() == 'bsd' and (stage/'copyright').read_text() == 'apache'


# bluetoothd and the headers and stubs it is built against (docs/bluez.md#build)

def test_a_stub_library_carries_the_soname_and_stocks_symbols(env, calls, tmp_path, monkeypatch):
    monkeypatch.setattr(thirdparty, 'STUBS', 'libx.so.3\tfn\tFUNC\t8\nlibx.so.3\tobj\tOBJECT\t24\nliby.so\tother\tFUNC\t4\n')
    thirdparty.stub_library(env, 'libx.so.3', tmp_path/'lib')
    assert (tmp_path/'lib/libx.so.3.stub.c').read_text() == 'void fn(void) {}\nchar obj[24];\n'
    assert calls == [([*env.cc.split(), '-shared', '-fPIC', '-Wl,-soname,libx.so.3', '-o', tmp_path/'lib/libx.so.3',
                       tmp_path/'lib/libx.so.3.stub.c'], None, None, None)]
    assert os.readlink(tmp_path/'lib/libx.so') == 'libx.so.3'
    thirdparty.stub_library(env, 'liby.so', tmp_path/'lib')  # its own name already: no link
    assert (tmp_path/'lib/liby.so.stub.c').read_text() == 'void other(void) {}\n' and not (tmp_path/'lib/liby.so').exists()


def test_the_stub_table_names_glib_dbus_and_libbtctl_only():
    rows = [r.split('\t') for r in thirdparty.STUBS.splitlines()]
    assert {r[0] for r in rows} == {'libglib-2.0.so.0', 'libdbus-1.so.3', 'libbtctl.so'} and all(len(r) == 4 for r in rows)
    assert {r[2] for r in rows} == {'FUNC', 'OBJECT'} and all(r[3].isdigit() for r in rows)
    names = {r[1] for r in rows}
    assert {'g_main_loop_run', 'g_key_file_load_from_file', 'dbus_connection_send_with_reply'} <= names


def test_glib_is_headers_a_stub_and_a_pkg_config_file(env, calls, tmp_path):
    src, stage = tmp_path/'src', tmp_path/'stage'
    for d, names in (('glib', ('glib.h', 'glib-object.h', 'gmain.h', 'gmain.c')), ('glib/deprecated', ('gthread.h',)),
                     ('gobject', ('gobject.h',))):
        (src/d).mkdir(parents=True, exist_ok=True)
        for n in names:
            (src/d/n).write_text(n)
    thirdparty.build_glib(src, stage, env, tmp_path/'log')
    inc = stage/'usr/include/glib-2.0'
    assert sorted(str(f.relative_to(inc)) for f in inc.rglob('*.[ch]')) == [
        'glib-object.h', 'glib.h', 'glib/deprecated/gthread.h', 'glib/gmain.h', 'gobject/gobject.h']
    assert (stage/'usr/lib/glib-2.0/include/glibconfig.h').read_text() == thirdparty.GLIBCONFIG
    assert '#define GLIB_MINOR_VERSION 74' in thirdparty.GLIBCONFIG and 'G_BYTE_ORDER G_LITTLE_ENDIAN' in thirdparty.GLIBCONFIG
    assert calls[0][0][-4:-2] == ['-Wl,-soname,libglib-2.0.so.0', '-o'] and len(calls) == 1
    assert (stage/'usr/lib/pkgconfig/glib-2.0.pc').read_text() == (
        f"prefix={stage}/usr\nName: GLib\nDescription: stock's GLib\nVersion: 2.74.4\nLibs: -L${{prefix}}/lib -lglib-2.0\n"
        'Cflags: -I${prefix}/include/glib-2.0 -I${prefix}/lib/glib-2.0/include\n')
    assert thirdparty.SOURCES['glib'][0] == '2.74.4'


def test_dbus_is_headers_a_stub_and_a_pkg_config_file(env, calls, tmp_path):
    src, stage = tmp_path/'src', tmp_path/'stage'
    (src/'dbus').mkdir(parents=True)
    (src/'dbus/dbus.h').write_text('h')
    (src/'dbus/dbus-internal.c').write_text('c')
    (src/'dbus/dbus-arch-deps.h.in').write_text(
        '@DBUS_HAVE_INT64@\n@DBUS_INT64_TYPE@ @DBUS_INT64_CONSTANT@ @DBUS_UINT64_CONSTANT@ @DBUS_INT32_TYPE@ '
        '@DBUS_INT16_TYPE@\n@DBUS_MAJOR_VERSION@.@DBUS_MINOR_VERSION@.@DBUS_MICRO_VERSION@ @DBUS_VERSION@\n')
    thirdparty.build_dbus(src, stage, env, tmp_path/'log')
    assert [f.name for f in (stage/'usr/include/dbus-1.0/dbus').iterdir()] == ['dbus.h']
    assert (stage/'usr/lib/dbus-1.0/include/dbus/dbus-arch-deps.h').read_text() == (
        '#define DBUS_HAVE_INT64 1\nlong long (val##LL) (val##ULL) int short\n1.12.28 1.12.28\n')
    assert calls[0][0][-4:-2] == ['-Wl,-soname,libdbus-1.so.3', '-o'] and len(calls) == 1
    assert (stage/'usr/lib/pkgconfig/dbus-1.pc').read_text() == (
        f"prefix={stage}/usr\nName: dbus\nDescription: stock's D-Bus\nVersion: 1.12.28\nLibs: -L${{prefix}}/lib -ldbus-1\n"
        'Cflags: -I${prefix}/include/dbus-1.0 -I${prefix}/lib/dbus-1.0/include\n')
    assert thirdparty.SOURCES['dbus'][0] == '1.12.28'


def bluez_tree(tmp_path, config):
    src = tmp_path/'src'
    (src/'src').mkdir(parents=True)
    (src/'config.h').write_text(config)
    for name, text in (('src/bluetoothd', 'elf'), ('src/main.conf', 'conf'), ('COPYING', 'gpl')):
        (src/name).write_text(text)
    return src


def test_bluez_is_upstreams_daemon_on_the_q2s_store(env, calls, tmp_path, monkeypatch):
    # a base other than stock's: Vamp's patch alone, then configure and make (docs/bluez.md#bases)
    monkeypatch.setattr(thirdparty, 'BLUEZ_BASE', 'buildroot')
    src = bluez_tree(tmp_path, '#define A 1\n#define STORAGEDIR "/var/lib/bluetooth"\n#define B 2\n')
    stage, log = tmp_path/'stage', tmp_path/'log'
    thirdparty.build_bluez(src, stage, env, log)
    vars = dict(env.vars, CFLAGS='-O2 -pipe -fPIC', LDFLAGS=env.vars['LDFLAGS'] + ' -pie -Wl,-z,now')
    assert calls == [(['patch', '-p1', '-i', 'vamp.patch'], src, None, log),
                     (['./configure', '--host=mipsel-linux-gnu', '--prefix=/usr', *thirdparty.BLUEZ_OPTIONS], src, vars, log),
                     (['make', '-j7'], src, vars, log)]
    assert (src/'vamp.patch').read_text() == thirdparty.BLUEZ_VAMP_PATCH and not (src/'shanling.patch').exists()
    assert (src/'config.h').read_text() == '#define A 1\n#define STORAGEDIR "/mnt/data/bluetooth"\n#define B 2\n'
    assert [(stage/f).read_text() for f in ('usr/bin/bluetoothd', 'etc/bluetooth/main.conf', 'COPYING')] == ['elf', 'conf', 'gpl']
    # stock's plugin set and no more: what a headset or a phone could reach stays what it was
    off = {o[len('--disable-'):] for o in thirdparty.BLUEZ_OPTIONS if o.startswith('--disable-')}
    assert {'hid', 'hog', 'network', 'obex', 'mesh', 'bap', 'midi', 'client', 'tools'} <= off and 'hfp' not in off
    assert not {'a2dp', 'avrcp'} & off and thirdparty.BLUEZ_STORAGE == '/mnt/data/bluetooth'
    assert thirdparty.SOURCES['bluez'][0] == '5.88' and thirdparty.BLUETOOTHD == 'usr/bin/bluetoothd'
    assert thirdparty.RECIPES['bluez'] == (thirdparty.build_bluez, ['glib', 'dbus']) and 'bluez' not in thirdparty.INSTALL


def test_bluez_for_the_stock_base_takes_shanlings_hooks_and_libbtctl(env, calls, tmp_path):
    """Vamp's patch, then Shanling's (tools/bluez/shanling.patch), autoreconf for the sources and libraries it adds
    to Makefile.am, a stub of each library stock's bluetoothd names, and the link told where they are."""
    assert thirdparty.BLUEZ_BASE == 'stock'
    src = bluez_tree(tmp_path, '#define STORAGEDIR "/var/lib/bluetooth"\n')
    stage, log = tmp_path/'stage', tmp_path/'log'
    thirdparty.build_bluez(src, stage, env, log)
    stubs = src/'q2-stubs'
    assert [c[0][:4] for c in calls[:3]] == [['patch', '-p1', '-i', 'vamp.patch'], ['patch', '-p1', '-i', 'shanling.patch'],
                                              ['autoreconf', '-fi']]
    assert (src/'shanling.patch').read_text() == thirdparty.BLUEZ_SHANLING_PATCH
    linked = [c[0][c[0].index('-o') + 1] for c in calls if '-shared' in c[0]]
    assert linked == [stubs/lib for lib in thirdparty.BLUEZ_SHANLING_STUBS]
    configure = [c for c in calls if c[0][0] == './configure'][0]
    assert configure[2]['LDFLAGS'].endswith(f' -pie -Wl,-z,now -L{stubs}')
    # stock's bluetoothd's NEEDED, less what every build links (glib, dbus, libc's)
    assert set(thirdparty.BLUEZ_SHANLING_STUBS) == {'libbtctl.so', 'libncurses.so.6', 'libgio-2.0.so.0', 'libgmodule-2.0.so.0',
                                                    'libgobject-2.0.so.0', 'libpcre2-8.so.0', 'libffi.so.8', 'libgthread-2.0.so.0'}
    stubbed = {row.split('\t')[1] for row in thirdparty.STUBS.splitlines() if row.startswith('libbtctl.so\t')}
    assert {'bluez_main', 'bluez_play_info_signal', 'g_play_status', 'g_track_meta', 'g_notify_mutex', 'g_avrcp_event_fun',
            'g_high_priority_lcodec', 'g_synclink_mode'} <= stubbed  # what shanling.patch calls and reads
    assert 'coda-rc' not in thirdparty.BLUEZ_SHANLING_PATCH.split('diff --git', 1)[1]  # browsing stays out


def test_bluez_stops_when_configure_names_no_store(env, calls, tmp_path):
    src = bluez_tree(tmp_path, '#define A 1\n')
    with pytest.raises(SystemExit, match='no STORAGEDIR'):
        thirdparty.build_bluez(src, tmp_path/'stage', env, tmp_path/'log')
    assert calls[-1][0][0] == './configure'  # configured, never compiled


def test_the_adbd_patch_is_the_files_text():
    assert thirdparty.ADBD_PATCH == (ROOT/'tools/adbd.patch').read_text() and 'diff --git a/core/adbd/adb_auth_client.c' in thirdparty.ADBD_PATCH


def test_every_recipe_has_a_source_and_its_dependencies_a_recipe():
    assert set(thirdparty.RECIPES) == set(thirdparty.SOURCES)
    assert all(d in thirdparty.RECIPES for _, needs in thirdparty.RECIPES.values() for d in needs)
    assert set(thirdparty.INSTALL) <= set(thirdparty.RECIPES)


# key: mutmut wraps each tools/ function in a trampoline whose code names none of the original's helpers, so what
# key follows is tested on functions planted here, which it leaves alone. Keys are compared with each other, as a
# mutant's module has other source text and so other digests.

FAKE = '''
def fake_recipe(src, stage, env, log):
    fake_helper_a(FAKE_STR, FAKE_LIST)
    fake_helper_b(FAKE_TUPLE, FAKE_DICT, FAKE_INT)
    run(JOBS, SOURCES, TOOLCHAIN, ARCH, CACHE, toolchain)


def fake_helper_a(*args):
    return args


def fake_helper_b(*args):
    fake_recipe(None, None, None, None)
    fake_helper_c()


def fake_helper_c():
    pass
'''


@pytest.fixture
def fake(monkeypatch):
    """A recipe 'fake' in thirdparty's namespace and CODE: fake_recipe, its helpers (b names the recipe back, and c
    after it, so a seen function must not end the walk) and constants of each kind."""
    ns: dict = {'__name__': thirdparty.__name__}
    exec(FAKE, ns)
    for node in ast.parse(FAKE).body:
        assert isinstance(node, ast.FunctionDef)
        monkeypatch.setattr(thirdparty, node.name, ns[node.name], raising=False)
        monkeypatch.setitem(thirdparty.CODE, node.name, ast.get_source_segment(FAKE, node))
    for name, value in (('FAKE_STR', 's'), ('FAKE_LIST', ['l']), ('FAKE_TUPLE', ('t',)), ('FAKE_DICT', {'d': 1}), ('FAKE_INT', 1)):
        monkeypatch.setattr(thirdparty, name, value, raising=False)
    monkeypatch.setitem(thirdparty.RECIPES, 'fake', (ns['fake_recipe'], []))
    monkeypatch.setitem(thirdparty.SOURCES, 'fake', ('1.0', 'https://x.invalid/fake-1.0.tar.gz', 'f' * 64))
    return lambda deps={}: thirdparty.key('fake', deps)


def test_a_key_is_sixteen_hex_digits_and_stable(fake):
    k = fake()
    assert len(k) == 16 and int(k, 16) >= 0 and fake() == k
    assert fake({'a': 'x', 'b': 'y'}) == fake({'b': 'y', 'a': 'x'})


def test_the_key_follows_the_recipe_and_what_it_names(fake, monkeypatch):
    base = fake()
    for name in ('fake_recipe', 'fake_helper_a', 'fake_helper_b', 'fake_helper_c', 'Env'):
        with monkeypatch.context() as m:
            m.setitem(thirdparty.CODE, name, f'{thirdparty.CODE[name]}\n# edited')
            assert fake() != base, name
    for name, value in (('FAKE_STR', 's2'), ('FAKE_LIST', ['l2']), ('FAKE_TUPLE', ('t2',)), ('FAKE_DICT', {'d': 2}),
                        ('REPRODUCIBLE', ['-ffile-prefix-map=/elsewhere=/q2-thirdparty']), ('ARCH', ['-EB']),
                        ('TOOLCHAIN', (thirdparty.TOOLCHAIN[0], '0' * 64))):
        with monkeypatch.context() as m:
            m.setattr(thirdparty, name, value)
            assert fake() != base, name
    with monkeypatch.context() as m:
        m.setitem(thirdparty.SOURCES, 'fake', ('1.0', 'https://x.invalid/fake-1.0.tar.gz', 'e' * 64))
        assert fake() != base
    assert fake({'zlib': 'zlib-1.4-a'}) != base and fake({'zlib': 'zlib-1.4-a'}) != fake({'zlib': 'zlib-1.4-b'})


def test_the_key_leaves_out_the_runner_the_settings_and_other_sources(fake, monkeypatch):
    base = fake()
    for name in ('run', 'toolchain'):
        with monkeypatch.context() as m:
            m.setitem(thirdparty.CODE, name, f'{thirdparty.CODE[name]}\n# edited')
            assert fake() == base, name
    for name, value in (('FAKE_INT', 2), ('JOBS', '64'), ('CACHE', pathlib.Path('/elsewhere')),
                        ('TOOLCHAIN', ('https://mirror.invalid/tc.tar.gz', thirdparty.TOOLCHAIN[1]))):
        with monkeypatch.context() as m:
            m.setattr(thirdparty, name, value)
            assert fake() == base, name
    with monkeypatch.context() as m:
        m.setitem(thirdparty.SOURCES, 'openssl', ('9', 'https://x.invalid/o.tar.gz', '0' * 64))
        m.setitem(thirdparty.SOURCES, 'fake', ('2.0', 'https://x.invalid/fake-2.0.tar.gz', 'f' * 64))
        assert fake() == base


def test_a_real_recipes_key_follows_its_text_and_dependencies(monkeypatch):
    deps = {'mbedtls': 'mbedtls-3.6.7-a', 'zlib': 'zlib-1.4-a'}
    base, zlib = thirdparty.key('curl', deps), thirdparty.key('zlib', {})
    assert base != zlib and thirdparty.key('curl', dict(deps, zlib='zlib-1.4-b')) != base
    monkeypatch.setitem(thirdparty.CODE, 'build_curl', f"{thirdparty.CODE['build_curl']}\n# edited")
    assert thirdparty.key('curl', deps) != base and thirdparty.key('zlib', {}) == zlib


# build

BUILD_FAKE = '''
def fake_build(src, stage, env, log):
    calls.append((src, stage, env, log, stage.exists()))
    (stage/'usr').mkdir(parents=True)
    if fail:
        raise RuntimeError('compiler crashed')
'''


@pytest.fixture
def builds(cache, monkeypatch):
    """Recipes 'a' and 'b' (which needs a), one planted function recording each call; unpack and epoch stand-ins."""
    ns: dict = {'__name__': thirdparty.__name__, 'calls': [], 'fail': False}
    exec(BUILD_FAKE, ns)
    node, = ast.parse(BUILD_FAKE).body
    monkeypatch.setattr(thirdparty, 'fake_build', ns['fake_build'], raising=False)
    monkeypatch.setitem(thirdparty.CODE, 'fake_build', ast.get_source_segment(BUILD_FAKE, node))
    monkeypatch.setattr(thirdparty, 'RECIPES', {'a': (ns['fake_build'], []), 'b': (ns['fake_build'], ['a'])})
    monkeypatch.setattr(thirdparty, 'SOURCES', {'a': ('1.0', 'https://x.invalid/a.tgz', 'a' * 64),
                                                'b': ('2.0', 'https://x.invalid/b.tgz', 'b' * 64)})
    monkeypatch.setattr(thirdparty, 'toolchain', lambda: TC)
    unpacked = []

    def unpack(name, work):
        assert work == cache/'work'/name and work.is_dir() and list(work.iterdir()) == []
        unpacked.append(name)
        (work/'src/top').mkdir(parents=True)
        return work/'src/top'
    monkeypatch.setattr(thirdparty, 'unpack', unpack)
    monkeypatch.setattr(thirdparty, 'epoch', lambda name: {'a': 100, 'b': 200}[name])
    return ns, unpacked


def test_build_builds_dependencies_first_and_caches(builds, cache, capsys):
    ns, unpacked = builds
    done: dict = {}
    b = thirdparty.build('b', done)
    a = cache/'out'/f'a-1.0-{thirdparty.key("a", {})}'
    assert b == cache/'out'/f'b-2.0-{thirdparty.key("b", {"a": a.name})}' and done == {'a': a, 'b': b}
    assert unpacked == ['a', 'b']
    (src_a, stage_a, env_a, log_a, _), (src_b, stage_b, env_b, log_b, _) = ns['calls']
    assert (src_a, stage_a, log_a) == (cache/'work/a/src/top', a, cache/'work/a/build.log')
    assert (src_b, stage_b, log_b) == (cache/'work/b/src/top', b, cache/'work/b/build.log')
    assert env_a.deps == {} and env_b.deps == {'a': a}
    assert env_a.vars['SOURCE_DATE_EPOCH'] == '100' and env_b.vars['SOURCE_DATE_EPOCH'] == '200'
    assert json.loads((b/'.done').read_text()) == {'version': '2.0', 'sha256': 'b' * 64}
    assert not (cache/'work/a').exists() and not (cache/'work/b').exists()
    assert capsys.readouterr().err == (f'thirdparty: building a 1.0 (log {cache}/work/a/build.log)\n'
                                       f'thirdparty: building b 2.0 (log {cache}/work/b/build.log)\n')
    assert thirdparty.build('b') == b and len(ns['calls']) == 2 and capsys.readouterr().err == ''


def test_build_reuses_what_this_run_built(builds):
    ns, _ = builds
    assert thirdparty.build('a', {'a': pathlib.Path('/given')}) == pathlib.Path('/given')
    assert ns['calls'] == []


def test_a_failed_build_is_not_cached(builds, cache):
    ns, unpacked = builds
    ns['fail'] = True
    with pytest.raises(RuntimeError):
        thirdparty.build('a')
    (cache/'work/a/leftover').write_text('')
    ns['fail'] = False
    stage = thirdparty.build('a')
    assert (stage/'.done').exists() and unpacked == ['a', 'a']
    assert [c[4] for c in ns['calls']] == [False, False]  # the half-built stage went first


def test_a_stage_without_its_done_mark_is_rebuilt(builds, cache):
    ns, _ = builds
    stage = cache/'out'/f'a-1.0-{thirdparty.key("a", {})}'
    (stage/'usr').mkdir(parents=True)
    assert thirdparty.build('a') == stage and len(ns['calls']) == 1 and ns['calls'][0][4] is False


# Elf, against readelf output in the format binutils 2.42 writes for a MIPS library

HEADER = '''ELF Header:
  Magic:   7f 45 4c 46 01 01 01 00 03 00 00 00 00 00 00 00
  Class:                             ELF32
  Machine:                           MIPS R3000
  Flags:                             0x70001407, noreorder, pic, cpic, nan2008, o32, mips32r2

Dynamic section at offset 0x168 contains 6 entries:
  Tag        Type                         Name/Value
 0x00000001 (NEEDED)                     Shared library: [libc.so.6]
 0x00000001 (NEEDED)                     Shared library: [libz.so.1]
 0x0000000e (SONAME)                     Library soname: [libfoo.so.1]
 0x0000000f (RPATH)                      Library rpath: [/opt/a:/opt/b]
 0x0000001d (RUNPATH)                    Library runpath: [/usr/lib/x::/usr/lib/y]
 0x00000000 (NULL)                       0x0
Attribute Section: gnu
File Attributes
  Tag_GNU_MIPS_ABI_FP: Hard float (32-bit CPU, Any FPU)

MIPS ABI Flags Version: 0

ISA: MIPS32r2
FP ABI: Hard float (32-bit CPU, 64-bit FPU)
'''

SYMBOLS = '''
Symbol table '.dynsym' contains 12 entries:
   Num:    Value  Size Type    Bind   Vis      Ndx Name
     0: 00000000     0 NOTYPE  LOCAL  DEFAULT  UND
     1: 00001974     0 SECTION GLOBAL DEFAULT   11 .init
     2: 00000000     0 FILE    GLOBAL DEFAULT  ABS crt1.c
     3: 00002090    24 FUNC    LOCAL  DEFAULT   12 hidden
     4: 000123e0     0 FUNC    GLOBAL DEFAULT  UND close@GLIBC_2.0 (19)
     5: 00000000     0 NOTYPE  WEAK   DEFAULT  UND __gmon_start__
     6: 00010000 0x1000 OBJECT  GLOBAL DEFAULT  UND table@V1 (3)
     7: 00002660    24 FUNC    GLOBAL DEFAULT   12 crc32@@ZLIB_1.2.12
     8: 00002670    16 FUNC    GLOBAL DEFAULT   12 crc32@ZLIB_1.2.0
     9: 0000df5c    56 FUNC    GLOBAL DEFAULT   12 compress
    10: 00000000     0 OBJECT  GLOBAL DEFAULT  ABS ZLIB_1.2.0
    11: 00030000 0x2000 OBJECT  WEAK   DEFAULT   20 tunable
'''


def readelf(monkeypatch, header, symbols=''):
    def run(cmd, capture_output, text):
        assert capture_output is True and text is True and cmd[0] == 'readelf' and cmd[2:] == ['-W', '/lib/libfoo.so.1']
        return types.SimpleNamespace(stdout={'-hdA': header, '--dyn-syms': symbols}[cmd[1]])
    monkeypatch.setattr(subprocess, 'run', run)


def test_elf_reads_what_the_linker_sees(monkeypatch):
    readelf(monkeypatch, HEADER, SYMBOLS)
    e = thirdparty.Elf('/lib/libfoo.so.1')
    assert e.path == '/lib/libfoo.so.1' and e.flags == 0x70001407 and e.fp == 'Hard float (32-bit CPU, 64-bit FPU)'
    assert e.needed == ['libc.so.6', 'libz.so.1'] and e.runpath == ['/opt/a', '/opt/b', '/usr/lib/x', '/usr/lib/y']
    assert e.undefined == [('close', 'GLIBC_2.0', False, 'FUNC', 0), ('__gmon_start__', None, True, 'NOTYPE', 0),
                           ('table', 'V1', False, 'OBJECT', 0x1000)]
    assert e.defined == {'crc32': [('ZLIB_1.2.12', 'FUNC', 24), ('ZLIB_1.2.0', 'FUNC', 16)], 'compress': [(None, 'FUNC', 56)],
                         'ZLIB_1.2.0': [(None, 'OBJECT', 0)], 'tunable': [(None, 'OBJECT', 0x2000)]}


def test_elf_fp_abi_from_the_attribute_or_nowhere(monkeypatch):
    plain = HEADER.split('Attribute Section')[0]
    readelf(monkeypatch, plain + '  Tag_GNU_MIPS_ABI_FP: Hard float (32-bit CPU, Any FPU) \n\nPrimary GOT:\n')
    assert thirdparty.Elf('/lib/libfoo.so.1').fp == 'Hard float (32-bit CPU, Any FPU)'
    readelf(monkeypatch, plain.replace(' (RUNPATH)                    Library runpath: [/usr/lib/x::/usr/lib/y]',
                                       ' (RUNPATH)                    Library runpath: []').replace('[/opt/a:/opt/b]', '[]'))
    e = thirdparty.Elf('/lib/libfoo.so.1')
    assert e.fp is None and e.runpath == [] and e.defined == {} and e.undefined == []


def test_elf_refuses_what_is_not_elf(monkeypatch):
    readelf(monkeypatch, 'readelf: Error: Not an ELF file\n')
    with pytest.raises(SystemExit) as e:
        thirdparty.Elf('/lib/libfoo.so.1')
    assert e.value.code == 'thirdparty: /lib/libfoo.so.1: not an ELF file'


def test_elf_refuses_a_symbol_line_it_cannot_read(monkeypatch):
    line = '     1: 00000000     0 FUNC    GLOBAL DEFAULT  UND @GLIBC_2.0'
    readelf(monkeypatch, HEADER, line + '\n')
    with pytest.raises(SystemExit) as e:
        thirdparty.Elf('/lib/libfoo.so.1')
    assert e.value.code == f'thirdparty: /lib/libfoo.so.1: {line}'


# resolve, unresolved, elf_files, check_abi

def lib(needed=(), runpath=(), defined=None, undefined=()):
    return types.SimpleNamespace(needed=list(needed), runpath=list(runpath), defined=defined or {}, undefined=list(undefined))


def touch(root, rel, data=b'\x7fELF\x01\x01\x01'):
    (root/rel).parent.mkdir(parents=True, exist_ok=True)
    (root/rel).write_bytes(data)


def test_resolve_follows_the_run_path_and_symlinks(tmp_path):
    root = tmp_path
    touch(root, 'lib/libc.so.6')
    touch(root, 'usr/lib/libz.so.1.2.11')
    (root/'usr/lib/libz.so.1').symlink_to('libz.so.1.2.11')
    touch(root, 'opt/app/lib/libz.so.1.4')
    (root/'opt/app/lib/libz.so.1').symlink_to('/usr/lib/../../opt/app/lib/libz.so.1.4')
    touch(root, 'opt/plain/libc.so.6', b'not an ELF file')
    (root/'lib/libm.so.6').symlink_to('../usr/lib/libm-2.28.so')
    touch(root, 'usr/lib/libm-2.28.so')
    elfs = dict.fromkeys(['lib/libc.so.6', 'usr/lib/libz.so.1.2.11', 'opt/app/lib/libz.so.1.4', 'usr/lib/libm-2.28.so'])
    assert thirdparty.resolve(root, elfs, 'libz.so.1', []) == 'usr/lib/libz.so.1.2.11'
    assert thirdparty.resolve(root, elfs, 'libz.so.1', ['/opt/app/lib']) == 'opt/app/lib/libz.so.1.4'
    assert thirdparty.resolve(root, elfs, 'libc.so.6', ['/opt/plain']) == 'lib/libc.so.6'  # a file the reader skipped
    assert thirdparty.resolve(root, elfs, 'libm.so.6', []) == 'usr/lib/libm-2.28.so'
    assert thirdparty.resolve(root, elfs, 'libnone.so', ['/opt/app/lib']) is None


def test_unresolved_searches_the_needed_closure_breadth_first(tmp_path):
    for rel in ('bin/prog', 'lib/libA.so', 'lib/libB.so', 'lib/libC.so', 'lib/libD.so'):
        touch(tmp_path, rel)
    # ld.so's order is A, B, C, D: C's `size` is the one prog binds to, and D's would truncate it.
    elfs = {'bin/prog': lib(['libA.so', 'libB.so'], undefined=[('size', None, False, 'OBJECT', 4), ('deep', 'V1', False, 'FUNC', 0)]),
            'lib/libA.so': lib(['libC.so']), 'lib/libB.so': lib(['libD.so', 'libA.so']),
            'lib/libC.so': lib(['libA.so'], defined={'size': [(None, 'OBJECT', 4)]}),
            'lib/libD.so': lib(defined={'size': [(None, 'OBJECT', 8)], 'deep': [('V1', 'FUNC', 12)]})}
    assert thirdparty.unresolved(tmp_path, elfs) == set()


def test_unresolved_reports_each_kind_of_problem(tmp_path):
    for rel in ('bin/prog', 'lib/libc.so.6'):
        touch(tmp_path, rel)
    libc = lib(defined={'puts': [('GLIBC_2.0', 'FUNC', 40)], 'environ': [('GLIBC_2.0', 'OBJECT', 4)],
                        'stdout': [('GLIBC_2.0', 'OBJECT', 4)], 'errno': [(None, 'OBJECT', 4)], 'tls': [('GLIBC_2.0', 'TLS', 8)]})
    imports = [('puts', 'GLIBC_2.0', False, 'FUNC', 99),        # a function's size does not matter
               ('puts', 'GLIBC_2.1', False, 'FUNC', 0),         # no such version
               ('gone', None, False, 'FUNC', 0), ('maybe', 'GLIBC_2.0', True, 'FUNC', 0),
               ('environ', 'GLIBC_2.0', False, 'OBJECT', 8),    # copy-relocated, and bigger than stock's
               ('stdout', None, False, 'OBJECT', 0),            # size unknown
               ('errno', None, False, 'OBJECT', 4), ('tls', 'GLIBC_2.0', False, 'TLS', 4)]
    elfs = {'bin/prog': lib(['libc.so.6', 'libmissing.so'], undefined=imports), 'lib/libc.so.6': libc}
    assert thirdparty.unresolved(tmp_path, elfs) == {('bin/prog', 'missing library libmissing.so'), ('bin/prog', 'puts@GLIBC_2.1'),
                                                     ('bin/prog', 'gone'), ('bin/prog', 'environ: object size 8, defined as 4')}


def test_unresolved_follows_each_librarys_own_run_path(tmp_path):
    for rel in ('bin/prog', 'opt/lib/libA.so', 'opt/private/libB.so'):
        touch(tmp_path, rel)
    elfs = {'bin/prog': lib(['libA.so'], ['/opt/lib'], undefined=[('b', None, False, 'FUNC', 0)]),
            'opt/lib/libA.so': lib(['libB.so'], ['/opt/private']), 'opt/private/libB.so': lib(defined={'b': [(None, 'FUNC', 4)]})}
    assert thirdparty.unresolved(tmp_path, elfs) == set()


def test_elf_files_are_the_regular_files_with_elfs_magic(tmp_path):
    touch(tmp_path, 'bin/busybox')
    touch(tmp_path, 'usr/lib/deep/libx.so')
    touch(tmp_path, 'etc/passwd', b'root:x:0:0')
    touch(tmp_path, 'etc/short', b'\x7f')
    (tmp_path/'bin/sh').symlink_to('busybox')
    (tmp_path/'dev').mkdir()
    # Whatever order the directory lists them in, a skipped file is followed by an ELF one somewhere.
    mixed = [f'lib/{kind}{i}' for i in range(6) for kind in ('text', 'elf')]
    for rel in mixed:
        touch(tmp_path, rel, b'plain text' if 'text' in rel else b'\x7fELF\x01')
    elfs = ['bin/busybox', 'usr/lib/deep/libx.so', *(rel for rel in mixed if 'elf' in rel)]
    assert thirdparty.elf_files(tmp_path) == {rel: tmp_path/rel for rel in elfs}


def stock_tree(root):
    """A stock rootfs in miniature: a program, the library it imports from, and a plugin with a host-program import."""
    for rel in ('bin/prog', 'usr/lib/libz.so.1.2.11', 'usr/lib/plugin.so'):
        touch(root, rel)
    (root/'usr/lib/libz.so.1').symlink_to('libz.so.1.2.11')


def fake_elfs(monkeypatch, specs):
    monkeypatch.setattr(thirdparty, 'Elf', lambda p: specs[str(p)])


FP = 'Hard float (32-bit CPU, 64-bit FPU)'


def mips(needed=(), defined=None, undefined=(), flags=0x70001407, fp=FP):
    return types.SimpleNamespace(flags=flags, fp=fp, **vars(lib(needed, (), defined, undefined)))


def test_check_abi_accepts_a_compatible_replacement(tmp_path, monkeypatch):
    root = tmp_path/'stockfs'
    stock_tree(root)
    zlib = {'inflate': [('ZLIB_1.2.0', 'FUNC', 100)], 'zlibVersion': [(None, 'FUNC', 8)]}
    fake_elfs(monkeypatch, {f'{root}/bin/prog': mips(['libz.so.1'], undefined=[('inflate', 'ZLIB_1.2.0', False, 'FUNC', 0)]),
                            f'{root}/usr/lib/libz.so.1.2.11': mips(defined=zlib),
                            f'{root}/usr/lib/plugin.so': mips(undefined=[('host_call', None, False, 'FUNC', 0)]),
                            '/new/libz.so': mips(defined=dict(zlib, deflate=[('ZLIB_1.2.0', 'FUNC', 1)]))})
    assert thirdparty.check_abi(str(root), {'usr/lib/libz.so.1.2.11': '/new/libz.so'}) == []


def test_check_abi_reports_what_a_replacement_breaks(tmp_path, monkeypatch):
    root = tmp_path/'stockfs'
    stock_tree(root)
    imports = [('inflate', 'ZLIB_1.2.0', False, 'FUNC', 0), ('crc32', 'ZLIB_1.2.0', False, 'FUNC', 0), ('z_errmsg', None, False, 'OBJECT', 40)]
    zlib = {'inflate': [('ZLIB_1.2.0', 'FUNC', 100)], 'crc32': [('ZLIB_1.2.0', 'FUNC', 9)], 'z_errmsg': [(None, 'OBJECT', 40)]}
    fake_elfs(monkeypatch, {f'{root}/bin/prog': mips(['libz.so.1'], undefined=imports),
                            f'{root}/usr/lib/libz.so.1.2.11': mips(defined=zlib),
                            f'{root}/usr/lib/plugin.so': mips(undefined=[('host_call', None, False, 'FUNC', 0)]),
                            '/new/libz.so': mips(['libnew.so'], {'inflate': [('ZLIB_1.2.9', 'FUNC', 100)], 'z_errmsg': [(None, 'OBJECT', 44)]},
                                                 flags=0x70001405, fp='Hard float (double precision)'),
                            '/new/extra.so': mips()})
    # the file with no stock counterpart first: the check goes on to the next
    assert thirdparty.check_abi(root, {'usr/lib/extra.so': '/new/extra.so', 'usr/lib/libz.so.1.2.11': '/new/libz.so'}) == [
        'usr/lib/extra.so: no stock ELF file to replace',
        'usr/lib/libz.so.1.2.11: ELF flags 0x70001405, stock 0x70001407',
        'usr/lib/libz.so.1.2.11: FP ABI Hard float (double precision), stock Hard float (32-bit CPU, 64-bit FPU)',
        'bin/prog: crc32@ZLIB_1.2.0', 'bin/prog: inflate@ZLIB_1.2.0',
        'bin/prog: missing library libnew.so',  # through its closure, as well as the library's own
        'bin/prog: z_errmsg: object size 40, defined as 44', 'usr/lib/libz.so.1.2.11: missing library libnew.so']


# stripped, replacements, install, main

def test_stripped(tmp_path, calls, monkeypatch):
    monkeypatch.setattr(thirdparty, 'toolchain', lambda: TC)
    dst = tmp_path/'out/thirdparty/usr/lib/libz.so.1.2.11'
    assert thirdparty.stripped(pathlib.Path('/stage/usr/lib/libz.so.1.4'), dst) == dst
    assert dst.parent.is_dir()
    assert thirdparty.stripped(pathlib.Path('/stage/usr/lib/libz.so.1.4'), dst) == dst  # into the directory made before
    assert calls == [([f'{GCC}strip', '--strip-unneeded', '-R', '.comment', '-R', '.note.gnu.build-id', '-o', dst,
                       pathlib.Path('/stage/usr/lib/libz.so.1.4')], None, None, None)] * 2


def test_replacements_strips_every_install_entry(tmp_path, monkeypatch):
    def build(name, done):
        for d in {'curl': ['mbedtls', 'zlib'], 'wpa_supplicant': ['openssl', 'libnl']}.get(name, []):
            done.setdefault(d, pathlib.Path('/out')/d)
        done[name] = pathlib.Path('/out')/name
        return done[name]
    monkeypatch.setattr(thirdparty, 'build', build)
    stripped = []
    monkeypatch.setattr(thirdparty, 'stripped', lambda src, dst: stripped.append((src, dst)) or dst)
    out = tmp_path/'build'
    files, versions = thirdparty.replacements(out)
    expected = {path: (pathlib.Path('/out')/name/rel, out/'thirdparty'/path)
                for name, entries in thirdparty.INSTALL.items() for path, rel in entries.items()}
    assert files == {path: dst for path, (_, dst) in expected.items()} and sorted(stripped) == sorted(expected.values())
    assert files['usr/lib/libz.so.1.2.11'] == out/'thirdparty/usr/lib/libz.so.1.2.11'
    # mbedtls and libnl among them; adbd is no replacement (a --dev build's program, docs/adb.md), nor are
    # bluez and the glib and dbus headers it is built against (an --own-bluez build's, docs/bluez.md), nor the
    # own bluealsa and what it is built from (an --own-bluealsa build's, docs/bluealsa.md#build), nor squashfuse_ll
    # and the static libraries it links (a --dev build's, docs/boot.md#vampos-card-boot)
    beside = {'adbd', 'bluez', 'glib', 'dbus', 'bluealsa', 'stocksdk', 'sbc', 'freeaptx', 'ldac',
              'squashfuse', 'libfuse', 'lzo'}
    assert versions == {name: v for name, (v, _, _) in thirdparty.SOURCES.items() if name not in beside}


@pytest.fixture
def installing(tmp_path, monkeypatch, calls):
    out = tmp_path/'build'
    a, b = tmp_path/'a.so', tmp_path/'b'
    a.write_bytes(b'new a')
    b.write_bytes(b'new b')
    files = {'usr/lib/a.so': a, 'bin/b': b}

    def replacements(o):
        assert o == out
        return files, {'a': '1.0'}
    monkeypatch.setattr(thirdparty, 'replacements', replacements)

    def run(*cmd, cwd=None, env=None, log=None):
        calls.append((list(cmd), cwd, env, log))
        assert not (out/'stockfs').exists()
        (out/'stockfs/usr').mkdir(parents=True)
    monkeypatch.setattr(thirdparty, 'run', run)
    checked = []
    return out, files, checked


def test_install_swaps_each_inode_and_records_it(installing, calls, monkeypatch):
    out, files, checked = installing
    (out/'stockfs/stale').mkdir(parents=True)  # a failed build's
    monkeypatch.setattr(thirdparty, 'check_abi', lambda root, f: checked.append((root, f)) or [])
    swaps = []

    def swap_inode(p, path, f):
        swaps.append((p, path, f))
        return p + path + b';'
    p, record = thirdparty.install(b'pseudo;', out, lambda path: b'stock ' + path.encode(), swap_inode, pathlib.Path('/rootfs.squashfs'))
    assert calls == [(['unsquashfs', '-q', '-no-xattrs', '-d', out/'stockfs', pathlib.Path('/rootfs.squashfs')], None, None, None)]
    assert checked == [(out/'stockfs', files)]
    assert swaps == [(b'pseudo;', b'usr/lib/a.so', files['usr/lib/a.so']), (b'pseudo;usr/lib/a.so;', b'bin/b', files['bin/b'])]
    assert p == b'pseudo;usr/lib/a.so;bin/b;'
    assert record == {'versions': {'a': '1.0'}, 'files': {
        'usr/lib/a.so': {'original_sha256': hashlib.sha256(b'stock usr/lib/a.so').hexdigest(), 'sha256': hashlib.sha256(b'new a').hexdigest()},
        'bin/b': {'original_sha256': hashlib.sha256(b'stock bin/b').hexdigest(), 'sha256': hashlib.sha256(b'new b').hexdigest()}}}
    assert not (out/'stockfs').exists()


def test_install_stops_on_an_abi_problem(installing, monkeypatch):
    out, _, _ = installing
    monkeypatch.setattr(thirdparty, 'check_abi', lambda root, f: ['bin/prog: gone', 'bin/prog: lost'])
    with pytest.raises(SystemExit) as e:
        thirdparty.install(b'', out, lambda path: b'', lambda *a: pytest.fail('swapped'), '/rootfs.squashfs')
    assert e.value.code == 'thirdparty: the rebuilt libraries would break stock programs:\nbin/prog: gone\nbin/prog: lost'


def test_main_builds_the_named_components_or_all(monkeypatch, capsys):
    built = []
    monkeypatch.setattr(thirdparty, 'build', lambda name: built.append(name) or pathlib.Path('/out')/name)
    assert thirdparty.main(['build', 'zlib', 'curl']) == 0
    assert built == ['zlib', 'curl'] and capsys.readouterr().out == 'zlib /out/zlib\ncurl /out/curl\n'
    built.clear()
    assert thirdparty.main(['build']) == 0 and built == list(thirdparty.RECIPES)


@pytest.mark.parametrize('problems,printed,status', [([], 'ok\n', 0), (['bin/a: x', 'bin/b: y'], 'bin/a: x\nbin/b: y\n', 1)])
def test_main_checks_against_a_stock_rootfs(monkeypatch, capsys, problems, printed, status):
    dirs = []

    def replacements(out):
        assert out.is_dir() and out.name.startswith('q2-thirdparty-')
        dirs.append(out)
        return {'bin/a': out/'a'}, {}
    monkeypatch.setattr(thirdparty, 'replacements', replacements)
    checked = []
    monkeypatch.setattr(thirdparty, 'check_abi', lambda root, files: checked.append((root, files)) or problems)
    assert thirdparty.main(['check', '/stockfs']) == status
    assert checked == [('/stockfs', {'bin/a': dirs[0]/'a'})] and not dirs[0].exists()
    assert capsys.readouterr().out == printed


@pytest.mark.parametrize('argv', [[], ['check'], ['check', 'a', 'b'], ['help'], ['stockfs', 'check']])
def test_main_usage(argv, monkeypatch, capsys):
    monkeypatch.setattr(thirdparty, 'build', lambda name: pytest.fail(name))
    monkeypatch.setattr(thirdparty, 'replacements', lambda out: pytest.fail(out))
    assert thirdparty.main(argv) == 2
    assert capsys.readouterr() == ('', thirdparty.__doc__ + '\n')


# the own bluealsa and what it is built from (docs/bluealsa.md#build)
def test_pkgconfig_names_the_stage(tmp_path):
    thirdparty.pkgconfig(tmp_path, 'alsa', '1.1.6', '-L/x -lasound', '-I${prefix}/include', 'glib-2.0')
    thirdparty.pkgconfig(tmp_path, 'sbc', '2.1', '-lsbc', '')  # into the directory made before
    assert (tmp_path/'usr/lib/pkgconfig/alsa.pc').read_text() == (
        f'prefix={tmp_path}/usr\nName: alsa\nDescription: alsa for the Q2\nVersion: 1.1.6\nRequires: glib-2.0\n'
        'Libs: -L/x -lasound\nCflags: -I${prefix}/include\n')
    assert 'Requires: \nLibs: -lsbc\n' in (tmp_path/'usr/lib/pkgconfig/sbc.pc').read_text()


def test_copy_headers_takes_the_pattern_alone(tmp_path):
    src = tmp_path/'src'
    src.mkdir()
    for n in ('a.h', 'gunixfd.h', 'a.c'):
        (src/n).write_text(n)
    thirdparty.copy_headers(src, tmp_path/'all/deep')
    thirdparty.copy_headers(src, tmp_path/'unix', 'gunix*.h')
    assert sorted(p.name for p in (tmp_path/'all/deep').iterdir()) == ['a.h', 'gunixfd.h']
    assert [p.name for p in (tmp_path/'unix').iterdir()] == ['gunixfd.h']


def test_meson_files_reads_a_list_and_what_is_added_to_it():
    text = "gio_headers = files(\n  'a.h',\n  'b.h',\n)\nother = files('x.h')\ngio_headers += files('c.h')\n"
    assert thirdparty.meson_files(text, 'gio_headers') == ['a.h', 'b.h', 'c.h']
    assert thirdparty.meson_files(text, 'other') == ['x.h'] and thirdparty.meson_files(text, 'none') == []


@pytest.fixture
def sdk(tmp_path, monkeypatch):
    """A stand-in GLib tree, the other libraries' archives and a stock rootfs, as build_stocksdk reads them."""
    src = tmp_path/'work/src/glib-2.74.4'
    for d in ('glib/deprecated', 'gobject', 'gio/gdbus-2.0/codegen', 'gmodule'):
        (src/d).mkdir(parents=True)
    for h in ('glib/glib.h', 'glib/glib-object.h', 'glib/glib-unix.h', 'glib/gunicode.h', 'glib/deprecated/gold.h',
              'gobject/gobject.h', 'gio/gio.h', 'gio/gunixfdlist.h', 'gmodule/gmodule.h'):
        (src/h).write_text(h)
    (src/'gobject/glib-mkenums.in').write_text('#!/usr/bin/env @PYTHON@\n# @VERSION@\n')

    def mkenums(argv, check, stdout):
        # GLib's glib-mkenums, not run here: what it was asked for, so the test sees which headers each enum header is from
        tool = pathlib.Path(argv[1])
        assert check and stdout == subprocess.PIPE and tool.read_text() == '#!/usr/bin/env python3\n# 2.74.4\n'
        return types.SimpleNamespace(stdout=(' '.join([tool.name, argv[2], *(pathlib.Path(a).name for a in argv[3:])]) + '\n').encode())
    monkeypatch.setattr(thirdparty.subprocess, 'run', mkenums)
    for t in ('gobject/glib-enumtypes.h.template', 'gio/gioenumtypes.h.template'):
        (src/t).write_text('')
    listed = [f'g{i}.h' for i in range(151)]
    (src/'gio/meson.build').write_text('gio_headers = files(\n' + ''.join(f"  '{h}',\n" for h in listed) + ')\n'
                                       "gdbus_headers = files('gdbus.h')\napplication_headers = files()\nsettings_headers = files('gsettings.h')\n")
    for n in ('codegen_main.py', 'config.py.in', 'meson.build'):
        (src/'gio/gdbus-2.0/codegen'/n).write_text(n)
    (src/'gio/gdbus-2.0/codegen/utils.py').write_text(CODEGEN_UTILS)
    members = {'dbus': [('dbus/dbus.h', b'dbus'), ('dbus/dbus-arch-deps.h.in', b'@DBUS_INT64_TYPE@ @DBUS_VERSION@ @DBUS_HAVE_INT64@')],
               'alsa': [('include/asoundlib.h', b'asound'), ('include/sys.h', b'sys'), ('include/sound/asound.h', b'kernel')],
               'bluez': [('lib/bluetooth.h', b'bt'), ('lib/hci.c', b'c')],
               'fdk-aac': [('libSYS/include/FDK_audio.h', b'sys'), ('libAACenc/include/aacenc_lib.h', b'enc'),
                           ('libAACdec/include/aacdecoder_lib.h', b'dec')]}
    archives = {}
    for name, (url, digest, top) in thirdparty.BLUEALSA_HEADERS.items():
        archives[url] = tarball(tmp_path/f'{name}.tar.gz', [(f'{top}/{n}', data, 0) for n, data in members[name]])
    monkeypatch.setattr(thirdparty, 'fetch', lambda url, digest, name: archives[url])
    stock = tmp_path/'stockfs'
    (stock/'usr/lib').mkdir(parents=True)
    (stock/'usr/lib/libglib.so').write_bytes(b'glib')
    monkeypatch.setattr(thirdparty, 'STOCKFS', stock)
    monkeypatch.setattr(thirdparty, 'BLUEALSA_STOCK_LIBS', {'usr/lib/libglib.so': hashlib.sha256(b'glib').hexdigest()})
    return types.SimpleNamespace(src=src, stock=stock, stage=tmp_path/'stage', listed=listed)


# the part of GLib 2.74's gio/gdbus-2.0/codegen/utils.py build_stocksdk rewrites (distutils is gone from Python 3.12)
CODEGEN_UTILS = '''import distutils.version


def version_cmp_key(key):
    v = key[0] or "0"
    return (distutils.version.LooseVersion(v), key[1])
'''


def test_stocksdk_is_headers_tools_and_links_to_the_stock_libraries(sdk, env, tmp_path):
    thirdparty.build_stocksdk(sdk.src, sdk.stage, env, tmp_path/'log')
    usr = sdk.stage/'usr'
    inc = usr/'include/glib-2.0'
    for top in ('glib.h', 'glib-object.h', 'glib-unix.h', 'gmodule.h', 'glib/gunicode.h', 'glib/deprecated/gold.h',
                'gobject/gobject.h', 'gio/gio.h', 'gio/gunixfdlist.h'):
        assert (inc/top).is_file(), top
    assert not (inc/'glib/glib.h').exists()  # moved up, as GLib installs it
    assert [p.name for p in (usr/'include/gio-unix-2.0/gio').iterdir()] == ['gunixfdlist.h']
    assert (usr/'lib/glib-2.0/include/glibconfig.h').read_text() == thirdparty.GLIBCONFIG
    assert (inc/'gobject/glib-enumtypes.h').read_text() == 'glib-mkenums --template glib-enumtypes.h.template gunicode.h\n'
    assert (inc/'gio/gioenumtypes.h').read_text().split() == ['glib-mkenums', '--template', 'gioenumtypes.h.template', *sdk.listed,
                                                              'gdbus.h', 'gsettings.h']
    codegen = usr/'share/glib-2.0/codegen'
    assert sorted(p.name for p in codegen.iterdir()) == ['codegen_main.py', 'config.py', 'utils.py']  # no .in, no meson.build
    assert 'VERSION = "2.74.4"' in (codegen/'config.py').read_text()
    utils = (codegen/'utils.py').read_text()
    assert 'distutils' not in utils
    space = {}
    exec(utils, space)  # numbers compare as numbers, as LooseVersion's: 2.9 before 2.10, "" as 0, before both
    keys = [('2.10', 'a'), ('', 'b'), ('2.9', 'c'), ('2.9.1', 'd')]
    assert [k[1] for k in sorted(keys, key=space['version_cmp_key'])] == ['b', 'c', 'd', 'a']
    tool = usr/'bin/gdbus-codegen'
    assert repr(str(codegen.parent)) in tool.read_text() and tool.stat().st_mode & 0o111
    assert (usr/'include/dbus-1.0/dbus/dbus.h').read_text() == 'dbus'
    assert (usr/'lib/dbus-1.0/include/dbus/dbus-arch-deps.h').read_text() == 'long long 1.12.28 #define DBUS_HAVE_INT64 1'
    assert (usr/'include/alsa/asoundlib.h').read_text() == 'asound' and (usr/'include/alsa/sound/asound.h').read_text() == 'kernel'
    assert (usr/'include/sys/asoundlib.h').read_text() == 'sys'
    assert [p.name for p in (usr/'include/bluetooth').iterdir()] == ['bluetooth.h']
    assert sorted(p.name for p in (usr/'include/fdk-aac').iterdir()) == ['FDK_audio.h', 'aacdecoder_lib.h', 'aacenc_lib.h']
    pcs = {p.stem: p.read_text() for p in (usr/'lib/pkgconfig').iterdir()}
    assert sorted(pcs) == ['alsa', 'bluez', 'dbus-1', 'fdk-aac', 'gio-2.0', 'gio-unix-2.0', 'glib-2.0', 'gobject-2.0']
    for name, lib in (('alsa', 'asound'), ('bluez', 'bluetooth'), ('dbus-1', 'dbus-1'), ('fdk-aac', 'fdk-aac'),
                      ('gio-2.0', 'gio-2.0'), ('glib-2.0', 'glib-2.0'), ('gobject-2.0', 'gobject-2.0')):
        assert f'Libs: -L{sdk.stock}/usr/lib -l{lib}\n' in pcs[name], name
    assert 'Requires: gio-2.0\nLibs: \n' in pcs['gio-unix-2.0'] and 'Requires: gobject-2.0\n' in pcs['gio-2.0']
    assert (sdk.stage/'ldflags').read_text() == f'-Wl,-rpath-link,{sdk.stock}/usr/lib:{sdk.stock}/lib\n'


def test_stocksdk_refuses_a_missing_or_another_stock_library(sdk, env, tmp_path):
    (sdk.stock/'usr/lib/libglib.so').write_bytes(b'another build')
    with pytest.raises(SystemExit, match="is not V1.36's"):
        thirdparty.build_stocksdk(sdk.src, sdk.stage, env, tmp_path/'log')
    (sdk.stock/'usr/lib/libglib.so').unlink()
    with pytest.raises(SystemExit, match='no stock rootfs to link against'):
        thirdparty.build_stocksdk(sdk.src, sdk.stage, env, tmp_path/'log')
    assert not sdk.stage.exists()


def test_stocksdk_refuses_a_glib_whose_header_list_it_cannot_read(sdk, env, tmp_path):
    (sdk.src/'gio/meson.build').write_text("gio_headers = files('a.h')\n")
    with pytest.raises(SystemExit, match='does not list its headers'):
        thirdparty.build_stocksdk(sdk.src, sdk.stage, env, tmp_path/'log')


def test_sbc_is_a_static_library(env, calls, tmp_path):
    src, stage, log = tmp_path/'src', tmp_path/'stage', tmp_path/'log'
    thirdparty.build_sbc(src, stage, env, log)
    assert calls == [(['./configure', '--host=mipsel-linux-gnu', '--prefix=/usr', '--enable-static', '--disable-shared',
                       '--with-pic', '--disable-tools', '--disable-tester'], src, env.vars, log),
                     (['make', '-j7'], src, env.vars, log), (['make', 'install', f'DESTDIR={stage}'], src, env.vars, log)]
    assert 'Libs: -L${prefix}/lib -lsbc\n' in (stage/'usr/lib/pkgconfig/sbc.pc').read_text()


def test_freeaptx_is_one_object_in_an_archive(env, calls, tmp_path):
    src, stage, log = tmp_path/'src', tmp_path/'stage', tmp_path/'log'
    src.mkdir()
    for n in ('freeaptx.h', 'COPYING'):
        (src/n).write_text(n)
    thirdparty.build_freeaptx(src, stage, env, log)
    assert calls == [([*env.cc.split(), '-O3', '-fPIC', '-I.', '-c', 'freeaptx.c', '-o', 'freeaptx.o'], src, None, log),
                     ([f'{GCC}ar', 'rcs', stage/'usr/lib/libfreeaptx.a', 'freeaptx.o'], src, None, log)]
    assert (stage/'usr/include/freeaptx.h').read_text() == 'freeaptx.h' and (stage/'COPYING.freeaptx').read_text() == 'COPYING'
    assert 'Libs: -L${prefix}/lib -lfreeaptx\n' in (stage/'usr/lib/pkgconfig/libfreeaptx.pc').read_text()


def test_ldac_is_the_encoder_and_its_rate_control(env, calls, tmp_path):
    src, stage, log = tmp_path/'src', tmp_path/'stage', tmp_path/'log'
    lib = src/'libldac'
    for h in ('inc/ldacBT.h', 'abr/inc/ldacBT_abr.h', 'inc/other.h', 'LICENSE', 'NOTICE'):
        (lib/h).parent.mkdir(parents=True, exist_ok=True)
        (lib/h).write_text(h)
    thirdparty.build_ldac(src, stage, env, log)
    cc = [*env.cc.split(), '-O2', '-fPIC', '-Iinc', '-Isrc', '-Iabr/inc', '-c']
    assert calls == [([*cc, 'src/ldaclib.c', '-o', 'ldaclib.o'], lib, None, log), ([*cc, 'src/ldacBT.c', '-o', 'ldacBT.o'], lib, None, log),
                     ([f'{GCC}ar', 'rcs', stage/'usr/lib/libldacBT_enc.a', 'ldaclib.o', 'ldacBT.o'], lib, None, log),
                     ([*cc, 'abr/src/ldacBT_abr.c', '-o', 'ldacBT_abr.o'], lib, None, log),
                     ([f'{GCC}ar', 'rcs', stage/'usr/lib/libldacBT_abr.a', 'ldacBT_abr.o'], lib, None, log)]
    assert sorted(p.name for p in (stage/'usr/include/ldac').iterdir()) == ['ldacBT.h', 'ldacBT_abr.h']
    assert (stage/'LICENSE.ldac').read_text() == 'LICENSE' and (stage/'NOTICE.ldac').read_text() == 'NOTICE'
    pc = stage/'usr/lib/pkgconfig'
    assert 'Libs: -L${prefix}/lib -lldacBT_enc -lm\n' in (pc/'ldacBT-enc.pc').read_text()
    assert 'Requires: ldacBT-enc\nLibs: -L${prefix}/lib -lldacBT_abr\n' in (pc/'ldacBT-abr.pc').read_text()


@pytest.mark.parametrize('ldflags', ['-Wl,-rpath-link,/stock/usr/lib\n', None])
def test_bluealsa_is_patched_and_built_for_its_base(ldflags, calls, tmp_path, monkeypatch):
    monkeypatch.setattr(thirdparty, 'toolchain', lambda: TC)
    monkeypatch.setattr(thirdparty, 'JOBS', '7')
    monkeypatch.setenv('PATH', '/bin')
    base = tmp_path/'base'
    base.mkdir()
    if ldflags:
        (base/'ldflags').write_text(ldflags)
    env = thirdparty.Env({thirdparty.BLUEALSA_BASE: base, 'sbc': pathlib.Path('/dep/sbc')}, 1234)
    src, stage, log = tmp_path/'src', tmp_path/'stage', tmp_path/'log'
    src.mkdir()
    stage.mkdir()
    (src/'LICENSE').write_text('MIT')
    thirdparty.build_bluealsa(src, stage, env, log)
    v = dict(env.vars, PATH=f'{base}/usr/bin:/bin', LDFLAGS=f'{env.vars["LDFLAGS"]} {(ldflags or "").strip()}')
    patches = list(thirdparty.BLUEALSA_PATCHES)
    assert patches == ['alsa-1.1.6-ioplug-avail.patch', 'shanling.patch']  # in BLUEALSA_PATCHES's order
    assert calls == [*((['patch', '-p1', '-i', n], src, None, log) for n in patches),
                     (['autoreconf', '--install', '--force'], src, v, log),
                     (['./configure', '--host=mipsel-linux-gnu', '--prefix=/usr', *thirdparty.BLUEALSA_OPTIONS], src, v, log),
                     (['make', '-j7'], src, v, log), (['make', 'install', f'DESTDIR={stage}'], src, v, log)]
    for n in patches:
        assert (src/n).read_text() == (ROOT/'tools/bluealsa'/n).read_text() and '\n--- a/src/' in (src/n).read_text()
    assert (stage/'LICENSE').read_text() == 'MIT'
    assert {'--enable-aac', '--enable-aptx', '--enable-aptx-hd', '--with-libfreeaptx', '--enable-ldac'} <= set(thirdparty.BLUEALSA_OPTIONS)
    assert thirdparty.RECIPES['bluealsa'] == (thirdparty.build_bluealsa, [thirdparty.BLUEALSA_BASE, 'sbc', 'freeaptx', 'ldac'])
