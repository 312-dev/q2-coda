#!/usr/bin/env python3
"""Build q2ui, the menu UI that is a program of Vamp's own (docs/ui-rewrite.md), from ui/.

Two builds, as tools/player.py's: `host`, to run and test on this machine against a file standing in for the
panel, and `mips`, for the Q2 and the emulator, compiled with the toolchain stock was built with (tools/thirdparty.py
keeps it in ~/.cache/q2-thirdparty) and linked against the root filesystem's own SQLite. Text is rasterised by
stb_truetype, one public-domain header fetched from its repository at the pinned commit and checked against the
SHA-256 below; it is not in this repository.

    python3 tools/ui.py build host|mips    # build ui/, print the binary
    python3 tools/ui.py size mips          # the stripped binary's size
    python3 tools/ui.py test host          # build ui/test_engine.c with the toolkit, print the binary
"""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import player  # noqa: E402  (the toolchain's place and flags: one definition)
import thirdparty  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
# stb_truetype 1.26, the rasteriser AWTK itself uses, so the glyphs are the ones the menus have today
STB = ('1.26', 'https://raw.githubusercontent.com/nothings/stb/f0569113c93ad095470c54bf34a17b36646bbbb5/stb_truetype.h',
       'ecd30b05e0dd4fea3a13c26810dd9e1992dc379049482c393d5a19e6b5090aab')
# stb_image 2.30, from the same commit: PNG (the theme's glyphs) and JPEG (covers)
STB_IMAGE = ('2.30', 'https://raw.githubusercontent.com/nothings/stb/f0569113c93ad095470c54bf34a17b36646bbbb5/stb_image.h',
             '594c2fe35d49488b4382dbfaec8f98366defca819d916ac95becf3e75f4200b3')
HOST_SQLITE = pathlib.Path('/usr/lib/x86_64-linux-gnu/libsqlite3.so.0')


def stb():
    """The folder holding stb_truetype.h, fetched once."""
    version, url, digest = STB
    path = thirdparty.fetch(url, digest, f'stb_truetype-{version}/stb_truetype.h')
    return path.parent


def stb_image():
    """The folder holding stb_image.h, fetched once."""
    version, url, digest = STB_IMAGE
    path = thirdparty.fetch(url, digest, f'stb_image-{version}/stb_image.h')
    return path.parent


def sqlite_include():
    """SQLite's header, from the SQLite tools/thirdparty.py builds for the image: the declarations are the same for
    the root filesystem's own library and this machine's, which only the stable C API is used of."""
    found = sorted((player.THIRDPARTY/'out').glob('sqlite-*/usr/include/sqlite3.h'))
    if not found:
        sys.exit(f'ui: no SQLite under {player.THIRDPARTY}/out; run tools/thirdparty.py once to build it')
    return found[-1].parent


def build(target, goal='q2ui'):  # pragma: no mutate block
    """ui/ compiled and linked for target; the binary's path. goal q2ui-test is the toolkit's own checks."""
    out = ROOT/'ui/build'/target
    if target == 'host':
        cc, strip, lib = ['cc'], 'strip', pathlib.Path(os.environ.get('Q2_HOST_SQLITE') or HOST_SQLITE)
    else:
        fs = pathlib.Path(os.environ.get('Q2_STOCKFS') or player.THIRDPARTY/'stockfs')
        cc = [f'{player.toolchain()}/{player.TRIPLE}-gcc', *player.ARCH]
        strip, lib = f'{player.toolchain()}/{player.TRIPLE}-strip', fs/'usr/lib/libsqlite3.so.0.8.6'
    if not lib.exists():
        sys.exit(f'ui: no {lib}')
    log = player.CACHE/f'ui-{target}.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('')
    player.run('make', '-C', ROOT/'ui', f'OUT={out}', 'CC=' + ' '.join(map(str, cc)), f'STRIP={strip}', f'STB={stb()}', f'STBI={stb_image()}',
               f'SQLITE_INC={sqlite_include()}', f'SQLITE_LIB={lib}', '-j4', out/goal, log=log)
    return out/goal


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ('build', 'size', 'test') or sys.argv[2] not in ('host', 'mips'):
        sys.exit(__doc__)
    binary = build(sys.argv[2], 'q2ui-test' if sys.argv[1] == 'test' else 'q2ui')
    print(f'{binary}: {binary.stat().st_size} bytes' if sys.argv[1] == 'size' else binary)


if __name__ == '__main__':
    main()
