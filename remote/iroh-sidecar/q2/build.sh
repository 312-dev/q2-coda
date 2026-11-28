#!/bin/sh
# q2-iroh for the Q2 (MIPS32r2, FP64, NaN-2008), linked against the Q2's own glibc 2.28, the way
# librespot is (docs/librespot.md). tools/iroh.py runs it and pins the result for the firmware build
# (docs/remote.md#car-remote-across-networks).
#   remote/iroh-sidecar/q2/build.sh [OUT]
# STOCK_LIB names a stock V1.36 rootfs's /lib (default: Q2_STOCKFS's, else the unpacked rootfs
# tools/thirdparty.py keeps, as tools/player.py links against) and
# GLIBC_INCLUDE glibc 2.28's NaN-2008 headers (default: the Codescape toolchain thirdparty.py
# fetches). Needs Rust's nightly-2026-11-18 (TOOLCHAIN, the one tools/iroh.py's PIN was built with: another
# nightly makes another binary, or none) with rust-src, clang, lld, llvm-ar and llvm-strip.
# mipsel-q2-linux-gnu.json, q2-cc, q2-link, start.c and nonshared.c are DiamondBond/q2-librespot's
# contrib/shanlingq2 files (MIT); q2-link also links a shared object without start.o.
set -eu
TOOLCHAIN=nightly-2026-11-18
HERE=$(cd "$(dirname "$0")" && pwd)
CRATE=$(cd "$HERE/.." && pwd)
OUT=${1:-${CARGO_TARGET_DIR:-$CRATE/target}/q2}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
CACHE=${Q2_THIRDPARTY_CACHE:-$HOME/.cache/q2-thirdparty}
STOCK_LIB=${STOCK_LIB:-${Q2_STOCKFS:-$CACHE/stockfs}/lib}
GLIBC_INCLUDE=${GLIBC_INCLUDE:-$CACHE/toolchain/mips-mti-linux-gnu/2020.06-01/sysroot/mipsel-r2-hard-nan2008/usr/include}
[ -e "$STOCK_LIB/libc.so.6" ] || { echo "build.sh: no libc.so.6 in $STOCK_LIB (STOCK_LIB)" >&2; exit 1; }
[ -e "$GLIBC_INCLUDE/gnu/stubs-o32_hard_2008.h" ] || { echo "build.sh: no NaN-2008 glibc headers in $GLIBC_INCLUDE" >&2; exit 1; }

# A copy of the Q2's /lib with start.o and libq2nonshared.a standing in for crt1.o and
# libc_nonshared.a, which the rootfs lacks (see q2-librespot's build.sh).
L=$OUT/sysroot/lib
mkdir -p "$L"
cp -a "$STOCK_LIB"/*.so* "$L/"
CFLAGS="--target=mipsel-linux-gnu -march=mips32r2 -mfp64 -mnan=2008 -mabs=2008 -mabicalls -O2"
clang $CFLAGS -fno-pic -c "$HERE/start.c" -o "$OUT/start.o"
clang $CFLAGS -fPIC -c "$HERE/nonshared.c" -o "$OUT/nonshared.o"
rm -f "$L/libq2nonshared.a"
llvm-ar rcs "$L/libq2nonshared.a" "$OUT/nonshared.o"
for l in pthread.so.0 m.so.6 dl.so.2 rt.so.1 util.so.1 gcc_s.so.1; do ln -sf "lib$l" "$L/lib${l%.*}"; done
echo 'GROUP ( libc.so.6 libq2nonshared.a AS_NEEDED ( ld-linux-mipsn8.so.1 ) )' > "$L/libc.so"

export Q2_SYSROOT="$OUT/sysroot" Q2_START="$OUT/start.o" Q2_INCLUDE="$GLIBC_INCLUDE" MIPSEL_INCLUDE="$GLIBC_INCLUDE"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$CRATE/target}"
# iroh with iroh-restun.patch (its header says why): the crate is built from a copy, so the patch's
# path source never rewrites the crate's own Cargo.lock; iroh's source is the one Cargo.lock's
# checksum fetched.
BUILD="$OUT/crate"
rm -rf "$BUILD" "$OUT/iroh"
mkdir -p "$BUILD"
cp -a "$CRATE/Cargo.toml" "$CRATE/Cargo.lock" "$CRATE/src" "$BUILD/"
cd "$BUILD"
cargo +"$TOOLCHAIN" fetch --locked
IROH_SRC=$(ls -d "${CARGO_HOME:-$HOME/.cargo}"/registry/src/*/iroh-1.5.0 | head -1)
cp -a "$IROH_SRC" "$OUT/iroh"
patch -s -p1 -d "$OUT/iroh" < "$HERE/iroh-restun.patch"
CC_mipsel_q2_linux_gnu="$HERE/q2-cc" \
AR_mipsel_q2_linux_gnu=llvm-ar \
CARGO_TARGET_MIPSEL_Q2_LINUX_GNU_LINKER="$HERE/q2-link" \
cargo +"$TOOLCHAIN" build --release -Zbuild-std=std,panic_abort -Zjson-target-spec \
    --config "patch.crates-io.iroh.path=\"$OUT/iroh\"" \
    --target "$HERE/mipsel-q2-linux-gnu.json"
grep -q 'from_secs(300)' "$OUT/iroh/src/socket.rs" || { echo "q2-iroh: iroh-restun.patch not applied" >&2; exit 1; }

BIN=$CARGO_TARGET_DIR/mipsel-q2-linux-gnu/release/q2-iroh
# std's clock is only 64-bit (past January 2038) if its weak __clock_gettime64 bound to nonshared.c's.
llvm-nm "$BIN" | grep -q ' T __clock_gettime64$' ||
    { echo "q2-iroh: std's __clock_gettime64 is unresolved; see nonshared.c" >&2; exit 1; }
llvm-strip -o "$OUT/q2-iroh" "$BIN"
echo "$OUT/q2-iroh"
