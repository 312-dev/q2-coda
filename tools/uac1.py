#!/usr/bin/env python3
"""usb_f_uac1.ko, Car Audio's USB Audio 1.0 function (docs/iphone-mode.md#the-uac1-module), for the image.

The module is built from Linux 5.10.21's sources by tools/uac1_module/uac1_build.py, which needs kernel.org's tarball,
a MIPS cross gcc and minutes. A firmware build needs none of that: it takes the module from the third-party cache,
where it is kept under its SHA-256, and PIN below names the one to take and the uac1_module sources it was built from.
A changed source stops the build until the module is built and pinned again:

    python3 tools/uac1.py            # build it with uac1_build.py, keep it in the cache, print the new PIN
    python3 tools/uac1.py --check    # the pinned module is in the cache and the sources are the pinned ones

The image carries it at /module_driver/usb_f_uac1.ko, beside the vendor modules but not in the list
S11module_driver_default loads at boot; only the car gadget script (patch/q2car.sh) loads it, when Car
Audio starts, never at boot."""
import hashlib, os, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES = ROOT/'tools/uac1_module'
CACHE = pathlib.Path(os.environ.get('Q2_THIRDPARTY_CACHE') or pathlib.Path.home()/'.cache/q2-thirdparty')/'usb_f_uac1'
WORK = pathlib.Path.home()/'.cache/q2-uac1'  # uac1_build.py's default --work: its module/ holds the build

# (the module's SHA-256, its size, sources_sha256() of the tools/uac1_module it was built from)
PIN = ('968e7ac8c2b944ceca279e73981a937d53945a217ba27219c80efa4e8e091cba', 38420,
       '2a6eac3a4ab45ae5add37537754ad802b153dd3933ad6000cad74c108a75fe97')


def sources_sha256():
    """tools/uac1_module's sources: what uac1_build.py builds the module from (caches aside)."""
    h = hashlib.sha256()
    files = [p for p in SOURCES.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    for p in sorted(files):
        h.update(str(p.relative_to(SOURCES)).encode() + b'\0' + p.read_bytes() + b'\0')
    return h.hexdigest()


def cached(digest):
    """The cache's module of that SHA-256, or None."""
    p = CACHE/digest/'usb_f_uac1.ko'
    if p.is_file():
        data = p.read_bytes()
        if hashlib.sha256(data).hexdigest() == digest:
            return data
    return None


def keep(data):
    """data kept in the cache under its SHA-256; the digest."""
    digest = hashlib.sha256(data).hexdigest()
    (CACHE/digest).mkdir(parents=True, exist_ok=True)
    tmp = CACHE/digest/'usb_f_uac1.ko.new'
    tmp.write_bytes(data)
    os.replace(tmp, CACHE/digest/'usb_f_uac1.ko')
    return digest


def build(log=sys.stderr):
    """uac1_build.py's module (every check passed, or it exits), kept in the cache: (data, digest, sources), sources
    the sources_sha256 it was built from, taken before the build and checked again after it."""
    sources = sources_sha256()
    subprocess.run([sys.executable, str(SOURCES/'uac1_build.py'), '--work', str(WORK)], check=True, stdout=log,
                   stderr=log)
    if sources_sha256() != sources:
        raise SystemExit('tools/uac1_module changed while usb_f_uac1.ko was built: build it again')
    data = (WORK/'module/usb_f_uac1.ko').read_bytes()
    return data, keep(data), sources


def problem():
    """Why the pinned module cannot be used as it is, or None."""
    digest, size, sources = PIN
    if sources != sources_sha256():
        return 'tools/uac1_module changed since usb_f_uac1.ko was pinned: run python3 tools/uac1.py and set PIN'
    data = cached(digest)
    if data is None or len(data) != size:
        return f'usb_f_uac1.ko {digest[:12]} is not in {CACHE}: run python3 tools/uac1.py (docs/iphone-mode.md#the-uac1-module)'
    return None


def module():
    """The bytes to install as /module_driver/usb_f_uac1.ko: the pinned module."""
    why = problem()
    if why:
        raise SystemExit(why)
    return cached(PIN[0])


def main(argv):
    if argv[1:] == ['--check']:
        why = problem()
        print(why or f'usb_f_uac1.ko {PIN[0][:12]}: pinned, cached, sources unchanged')
        return 1 if why else 0
    if argv[1:]:
        print(__doc__, file=sys.stderr)
        return 2
    data, digest, sources = build()
    print(f'PIN = {(digest, len(data), sources)!r}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
