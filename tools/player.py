#!/usr/bin/env python3
"""Build q2player, the player that replaces stock's hciplayer (docs/player-plan.md), and its FFmpeg.

FFmpeg is fetched from ffmpeg.org at the pinned release, checked against the SHA-256 below and
configured with the demuxers, decoders and protocols the player uses and nothing else, as static
libraries. Two builds: `host`, for the tests in test/player, and `mips`, for the Q2, compiled with the
toolchain stock was built with (Codescape GNU Tools 2020.06-01, the `-mfp64 -mnan=2008` multilib),
which tools/thirdparty.py on branch sec/libs fetches into ~/.cache/q2-thirdparty; this reads it from
there. Results are cached under ~/.cache/q2-player (Q2_PLAYER_CACHE), keyed by the recipe.

    python3 tools/player.py ffmpeg host|mips    # build or reuse FFmpeg, print its prefix
    python3 tools/player.py build host|mips     # build player/ against it, print the binary
    python3 tools/player.py build host|mips car # the same with Car Audio's output (every image's: docs/iphone-mode.md#car-audio)
    python3 tools/player.py sizes mips          # the binary's size and what each library adds
"""
import hashlib, os, pathlib, shutil, subprocess, sys, tarfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = pathlib.Path(os.environ.get('Q2_PLAYER_CACHE') or pathlib.Path.home()/'.cache/q2-player')
THIRDPARTY = pathlib.Path(os.environ.get('Q2_THIRDPARTY_CACHE') or pathlib.Path.home()/'.cache/q2-thirdparty')
JOBS = os.environ.get('Q2_PLAYER_JOBS', '4')

# FFmpeg 7.1 is a maintained release branch that GCC 9.3 (the Q2's toolchain) builds.
FFMPEG = ('7.1.5', 'https://ffmpeg.org/releases/ffmpeg-7.1.5.tar.xz',
          'de668509caf9e35e3cd162473441fdb29538c6d96ed080292b3cf9e6fc5d558f')

# The toolchain and flags of tools/thirdparty.py (sec/libs): stock's own ABI. A NaN-encoding or FP-ABI
# mismatch would not load.
TRIPLE = 'mips-mti-linux-gnu'
ARCH = ['-EL', '-march=mips32r2', '-mabi=32', '-mhard-float', '-mfp64', '-mnan=2008', '-mabs=2008']

# What stock's hciplayer plays (docs/player.md#formats), by FFmpeg component, and nothing a listed format or source
# does not need: Tidal, DLNA and the phone's push hand over one file's URL, and Internet Radio is not the player, so
# no HLS, MPEG-TS or Matroska. MPEG audio is FFmpeg's integer decoder: 16-bit output, as stock's libmad gives, and no
# float in the path.
DEMUXERS = 'flac mp3 wav aiff mov ogg ape wv asf aac ac3 dts dsf iff tak'.split()
DECODERS = ('flac mp3 mp2 mp1 aac alac vorbis opus ape wavpack wmav1 wmav2 wmapro wmalossless '
            'ac3 dca tak dsd_lsbf dsd_msbf dsd_lsbf_planar dsd_msbf_planar dst '
            'pcm_s8 pcm_u8 pcm_s16le pcm_s16be pcm_s24le pcm_s24be pcm_s32le pcm_s32be pcm_f32le pcm_f32be '
            'pcm_f64le pcm_f64be pcm_alaw pcm_mulaw adpcm_ms adpcm_ima_wav').split()
PARSERS = 'flac mpegaudio aac ac3 dca vorbis opus tak'.split()
PROTOCOLS = 'file http tcp'.split()
BSFS = 'aac_adtstoasc'.split()


def mbedtls(target):  # pragma: no mutate block
    """The prefix of mbedTLS for target, for FFmpeg's https: a static TLS 1.2 and 1.3 client configured as
    tools/thirdparty.py configures it for the image (MBEDTLS_UNSET: no server side, no certificate dates). For mips the
    one tools/thirdparty.py built; for host built here from the same release and settings."""
    if target == 'mips':
        found = sorted((THIRDPARTY/'out').glob('mbedtls-*/usr/lib/libmbedtls.a'))
        if not found:
            sys.exit(f'player: no mbedTLS under {THIRDPARTY}/out; run tools/thirdparty.py once to build it')
        return found[-1].parent.parent
    from thirdparty import MBEDTLS_UNSET, SOURCES, fetch
    version, url, digest = SOURCES['mbedtls']
    key = hashlib.sha256(repr((version, digest, MBEDTLS_UNSET)).encode()).hexdigest()[:16]
    prefix = CACHE/'out'/f'mbedtls-{version}-host-{key}'
    if (prefix/'.done').exists():
        return prefix
    work = CACHE/'work'/'mbedtls-host'
    shutil.rmtree(work, ignore_errors=True); work.mkdir(parents=True)
    shutil.rmtree(prefix, ignore_errors=True)
    log = CACHE/'mbedtls-host.log'; log.write_text('')
    with tarfile.open(fetch(url, digest, url.rsplit('/', 1)[-1])) as t:
        t.extractall(work, filter='data')
    src = work/f'mbedtls-{version}'
    for option in MBEDTLS_UNSET:
        run('python3', 'scripts/config.py', 'unset', option, cwd=src, log=log)
    run('make', f'-j{JOBS}', '-C', 'library', 'static', cwd=src, env=dict(os.environ, CFLAGS='-O2 -fPIC'), log=log)
    shutil.copytree(src/'include', prefix/'include')
    (prefix/'lib').mkdir()
    for a in ('libmbedtls.a', 'libmbedx509.a', 'libmbedcrypto.a'):
        shutil.copy(src/'library'/a, prefix/'lib'/a)
    (prefix/'.done').write_text('')
    shutil.rmtree(work, ignore_errors=True)
    return prefix


def configure_args(prefix):
    a = ['--prefix=' + str(prefix), '--disable-everything', '--disable-programs', '--disable-doc', '--disable-autodetect',
         '--disable-avdevice', '--disable-swscale', '--disable-avfilter', '--disable-postproc', '--disable-debug',
         '--enable-static', '--disable-shared', '--enable-pic', '--disable-iconv', '--enable-network',
         # C only: the same arithmetic on the Q2 and on the machine that runs the reference decodes
         '--disable-asm', '--disable-runtime-cpudetect',
         # one section a function, so the player's --gc-sections link drops what nothing calls (CFLAGS, not codegen)
         '--extra-cflags=-ffunction-sections -fdata-sections']
    a += [f'--enable-demuxer={x}' for x in DEMUXERS] + [f'--enable-decoder={x}' for x in DECODERS]
    a += [f'--enable-parser={x}' for x in PARSERS] + [f'--enable-protocol={x}' for x in PROTOCOLS] + [f'--enable-bsf={x}' for x in BSFS]
    return a


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def run(*cmd, cwd=None, env=None, log=None):  # pragma: no mutate block
    with open(log, 'a') as out:
        r = subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT)
    if r.returncode:
        sys.exit(f'player: {" ".join(map(str, cmd))} failed ({r.returncode})\n' + pathlib.Path(log).read_text(errors='replace')[-3000:])


def source():  # pragma: no mutate block
    version, url, digest = FFMPEG
    path = CACHE/'dl'/url.rsplit('/', 1)[-1]
    if not (path.exists() and sha256(path) == digest):
        import urllib.request
        path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=600) as r, open(path, 'wb') as f:
            shutil.copyfileobj(r, f, 1 << 20)
        if sha256(path) != digest:
            path.unlink()
            sys.exit(f'player: {url} does not have SHA-256 {digest}')
    return path


def toolchain():
    bindir = THIRDPARTY/'toolchain'/TRIPLE/'2020.06-01/bin'
    if not (bindir/f'{TRIPLE}-gcc').exists():
        sys.exit(f'player: no toolchain at {bindir}; run tools/thirdparty.py (branch sec/libs) once to fetch it')
    return bindir


def ffmpeg(target):  # pragma: no mutate block
    """The prefix of FFmpeg built for target ('host' or 'mips'), built on first use."""
    args = configure_args('PREFIX')
    tls = mbedtls(target)
    key = hashlib.sha256(repr((FFMPEG, args, target, ARCH if target == 'mips' else '', str(tls))).encode()).hexdigest()[:16]
    prefix = CACHE/'out'/f'ffmpeg-{FFMPEG[0]}-{target}-{key}'
    if (prefix/'.done').exists():
        return prefix
    work = CACHE/'work'/f'ffmpeg-{target}'
    shutil.rmtree(work, ignore_errors=True); work.mkdir(parents=True)
    shutil.rmtree(prefix, ignore_errors=True)
    log = CACHE/f'ffmpeg-{target}.log'; log.write_text('')
    with tarfile.open(source()) as t:
        t.extractall(work, filter='data')
    src = work/f'ffmpeg-{FFMPEG[0]}'
    conf = ['./configure', *configure_args(prefix), '--extra-cflags=-O2 -ffile-prefix-map=%s=/q2-player' % CACHE]
    if target == 'mips':
        b = toolchain()
        conf += ['--enable-cross-compile', f'--cross-prefix={b}/{TRIPLE}-', '--arch=mips', '--target-os=linux', '--cpu=mips32r2',
                 '--extra-cflags=' + ' '.join(ARCH), '--extra-ldflags=' + ' '.join(ARCH)]
    # https through mbedTLS; FFmpeg's mbedTLS support is under LGPL 3 (--enable-version3), which GPL 3 takes
    conf += ['--enable-mbedtls', '--enable-version3', '--enable-protocol=https', '--enable-protocol=tls',
             f'--extra-cflags=-I{tls}/include', f'--extra-ldflags=-L{tls}/lib']
    run(*conf, cwd=src, log=log)
    run('make', f'-j{JOBS}', cwd=src, log=log)
    run('make', 'install', cwd=src, log=log)
    (prefix/'.done').write_text('')
    shutil.rmtree(work, ignore_errors=True)
    return prefix


def build(target, alsa=None, car_usb=False):  # pragma: no mutate block
    """player/ compiled and linked for target; the binary's path. For mips, alsa is the libasound.so.2 to link
    against, by default an unpacked stock root filesystem's (Q2_STOCKFS). car_usb (every image's player): Car Audio's
    output, way 3 (docs/iphone-mode.md#car-audio), in a build directory of its own, so the two never share objects."""
    ff = ffmpeg(target)
    tls = mbedtls(target)
    out = ROOT/'player/build'/(target + ('-car' if car_usb else ''))
    cc = ['cc'] if target == 'host' else [f'{toolchain()}/{TRIPLE}-gcc', *ARCH]
    strip = 'strip' if target == 'host' else f'{toolchain()}/{TRIPLE}-strip'
    # patch/peq.h's files (the EQ state, the fade, the taps) under the directory a host test starts the player in
    extra = ['PEQ_ROOT=""' if target == 'mips' else 'PEQ_ROOT="."']
    if target == 'mips':
        # stock's own libasound.so.2, from an unpacked root filesystem (Q2_STOCKFS; tools/thirdparty.py keeps one)
        fs = pathlib.Path(os.environ.get('Q2_STOCKFS') or THIRDPARTY/'stockfs')
        alsa = pathlib.Path(alsa or fs/'usr/lib/libasound.so.2.0.0')
        if not alsa.exists():
            sys.exit(f'player: no {alsa}; set Q2_STOCKFS to an unpacked stock root filesystem')
        extra.append(f'ALSA={alsa}')
    extra.append(f'TLS=-L{tls}/lib -lmbedtls -lmbedx509 -lmbedcrypto')
    if car_usb:
        extra.append('EXTRA_CFLAGS=-DQ2_CAR_USB')
    run('make', '-C', ROOT/'player', f'OUT={out}', 'CC=' + ' '.join(map(str, cc)), f'STRIP={strip}', f'FFMPEG={ff}',
        f'TARGET={target}', f'-j{JOBS}', *extra, log=_log(target))
    return out/'q2player'


def _log(target):
    p = CACHE/f'player-{target}.log'; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('')
    return p


def main():
    car = sys.argv[3:] == ['car']
    if len(sys.argv) not in (3, 4) or sys.argv[1] not in ('ffmpeg', 'build', 'sizes') or sys.argv[2] not in ('host', 'mips') \
            or (len(sys.argv) == 4 and not car):
        sys.exit(__doc__)
    what, target = sys.argv[1:3]
    if what == 'ffmpeg':
        print(ffmpeg(target))
    elif what == 'build':
        print(build(target, car_usb=car))
    else:
        binary = build(target)
        print(f'{binary}: {binary.stat().st_size} bytes')
        for lib in sorted((ffmpeg(target)/'lib').glob('*.a')):
            print(f'  {lib.name}: {lib.stat().st_size} bytes (archive)')


if __name__ == '__main__':
    main()
