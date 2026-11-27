#!/usr/bin/env python3
"""What os/package/*.mk takes from tools/ rather than repeating it: one definition of each.

    pins.py ffmpeg-configure PREFIX   tools/player.py's configure_args(PREFIX), quoted for a shell
    pins.py mbedtls-unset             tools/thirdparty.py's MBEDTLS_UNSET, one per word

Run from the tree tools/vampos.py exported, so the settings are those of the commit the image names.
"""
import pathlib
import shlex
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]/'tools'))
import player  # noqa: E402
import thirdparty  # noqa: E402

if sys.argv[1:2] == ['ffmpeg-configure'] and len(sys.argv) == 3:
    print(' '.join(shlex.quote(a) for a in player.configure_args(sys.argv[2])))
elif sys.argv[1:] == ['mbedtls-unset']:
    print(' '.join(thirdparty.MBEDTLS_UNSET))
else:
    sys.exit(__doc__)
