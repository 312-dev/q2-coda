#!/usr/bin/env python3
"""q2-iroh, Car Remote's iroh sidecar (docs/remote.md#car-remote-across-networks), for the image.

The binary is Rust (remote/iroh-sidecar), cross-built for the Q2 by remote/iroh-sidecar/q2/build.sh with Rust
nightly. A firmware build needs no Rust: it takes the binary from the third-party cache, where it is kept under
its SHA-256, and PIN below names the one to take and the crate it was built from. A changed crate stops the
build until it is built and pinned again:

    python3 tools/iroh.py            # build it from the crate, keep it in the cache, print the new PIN
    python3 tools/iroh.py --check    # the pinned binary is in the cache and the crate is the pinned one

`tools/build.py --build-iroh` builds it the same way inside a firmware build and uses what it made, pinned or
not (it says when the pin is stale). Building needs what q2/build.sh says (nightly with rust-src, clang, lld,
llvm-ar, llvm-strip, the stock rootfs and the Codescape headers tools/thirdparty.py keeps); CARGO_HOME,
RUSTUP_HOME and CARGO_TARGET_DIR are passed through."""
import hashlib, os, pathlib, shutil, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CRATE = ROOT/'remote/iroh-sidecar'
CACHE = pathlib.Path(os.environ.get('Q2_THIRDPARTY_CACHE') or pathlib.Path.home()/'.cache/q2-thirdparty')/'q2-iroh'

# (the stripped binary's SHA-256, its size, crate_sha256() of the crate it was built from)
PIN = ('7c80a9448c58a96700df92c075d8e3a5c01a1a61d1ced795b75b1b14d9fc5dac', 5383796,
       'a7065644727430a4f1baf1362b2ddcfaf05fc6b9b8e297fcb12f9ce9f363235e')


def crate_sha256():
    """The crate's sources: what q2/build.sh builds from (its target directory and lock-free files aside)."""
    h = hashlib.sha256()
    files = [CRATE/'Cargo.toml', CRATE/'Cargo.lock', *CRATE.glob('src/**/*.rs'), *(p for p in CRATE.glob('q2/*') if p.is_file())]
    for p in sorted(files):
        h.update(str(p.relative_to(CRATE)).encode() + b'\0' + p.read_bytes() + b'\0')
    return h.hexdigest()


def cached(digest):
    """The cache's binary of that SHA-256, or None."""
    p = CACHE/digest/'q2-iroh'
    if p.is_file():
        data = p.read_bytes()
        if hashlib.sha256(data).hexdigest() == digest:
            return data
    return None


def build(log=sys.stderr):
    """q2/build.sh's binary, kept in the cache under its SHA-256: (data, digest)."""
    out = pathlib.Path(os.environ.get('CARGO_TARGET_DIR') or CRATE/'target')/'q2'
    path = subprocess.run(['sh', str(CRATE/'q2/build.sh'), str(out)], check=True, stdout=subprocess.PIPE,
                          stderr=log, text=True).stdout.strip().splitlines()[-1]
    data = pathlib.Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    (CACHE/digest).mkdir(parents=True, exist_ok=True)
    tmp = CACHE/digest/'q2-iroh.new'
    tmp.write_bytes(data)
    tmp.chmod(0o755)
    os.replace(tmp, CACHE/digest/'q2-iroh')
    shutil.rmtree(out/'sysroot', ignore_errors=True)  # a copy of the stock /lib, made again by each build
    return data, digest


def problem():
    """Why the pinned binary cannot be used as it is, or None."""
    digest, size, crate = PIN
    if crate != crate_sha256():
        return 'remote/iroh-sidecar changed since q2-iroh was pinned: run python3 tools/iroh.py and set PIN'
    data = cached(digest)
    if data is None or len(data) != size:
        return f'q2-iroh {digest[:12]} is not in {CACHE}: run python3 tools/iroh.py (docs/remote.md#car-remote-across-networks)'
    return None


def binary(fresh=False):
    """The bytes to install as /usr/bin/q2-iroh: the pinned binary, or with fresh one built now."""
    if fresh:
        data, digest = build()
        if (digest, len(data), crate_sha256()) != PIN:
            print(f'q2-iroh: built {digest[:12]}, not the pinned one; PIN = {(digest, len(data), crate_sha256())!r}',
                  file=sys.stderr)
        return data
    why = problem()
    if why:
        raise SystemExit(why)
    return cached(PIN[0])


def main(argv):
    if argv[1:] == ['--check']:
        why = problem()
        print(why or f'q2-iroh {PIN[0][:12]}: pinned, cached, crate unchanged')
        return 1 if why else 0
    if argv[1:]:
        print(__doc__, file=sys.stderr)
        return 2
    data, digest = build()
    print(f'PIN = {(digest, len(data), crate_sha256())!r}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
