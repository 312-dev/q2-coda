#!/usr/bin/env python3
"""What os/package/*.mk takes from tools/ rather than repeating it: one definition of each.

    pins.py ffmpeg-configure PREFIX   tools/player.py's configure_args(PREFIX), quoted for a shell
    pins.py mbedtls-unset             tools/thirdparty.py's MBEDTLS_UNSET, one per word
    pins.py adbd SRC DL CC            tools/thirdparty.py's build_adbd on the unpacked source SRC, its other files
                                      taken from Buildroot's download folder DL (each checked against its pin), compiled
                                      with CC; the program in SRC/stage/usr/bin/adbd
    pins.py adb-mdns-sources          tools/build.py's ADB_MDNS_SOURCES
    pins.py release-key               the release key's public half (assets/signing/q2-coda-release.pub) as a C array
    pins.py uac1                      Car Audio's usb_f_uac1.ko: tools/uac1.py's pinned module in the third-party cache,
                                      its SHA-256 checked; the build stops when it is not there
    pins.py bluez-options             tools/thirdparty.py's BLUEZ_OPTIONS, one per word
    pins.py bluez-patch SRC           tools/bluez/vamp.patch applied to the unpacked BlueZ in SRC (the patch any base
                                      takes; shanling.patch is the stock base's alone, docs/bluez.md#bases)
    pins.py bluez-storage SRC         SRC/config.h's STORAGEDIR made BLUEZ_STORAGE, as build_bluez makes it
    pins.py bluealsa-options          tools/thirdparty.py's BLUEALSA_OPTIONS, one per word
    pins.py bluealsa-patch SRC        tools/thirdparty.py's BLUEALSA_PATCHES applied in order to bluez-alsa in SRC
    pins.py freeaptx SRC CC AR        tools/thirdparty.py's build_freeaptx on SRC with CC and AR; staged in SRC/stage
    pins.py ldac SRC CC AR            tools/thirdparty.py's build_ldac the same way

Run from the tree tools/vampos.py exported, so the settings are those of the commit the image names.
"""
import ast
import pathlib
import shlex
import sys

TOOLS = pathlib.Path(__file__).resolve().parents[2]/'tools'
sys.path.insert(0, str(TOOLS))
import player  # noqa: E402
import thirdparty  # noqa: E402

if sys.argv[1:2] == ['ffmpeg-configure'] and len(sys.argv) == 3:
    print(' '.join(shlex.quote(a) for a in player.configure_args(sys.argv[2])))
elif sys.argv[1:] == ['mbedtls-unset']:
    print(' '.join(thirdparty.MBEDTLS_UNSET))
elif sys.argv[1:2] == ['adbd'] and len(sys.argv) == 5:
    src, dl, cc = pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]), sys.argv[4]

    def fetch(url, digest, name):  # Buildroot fetched it, by its last path part, into dl
        path = dl/url.rsplit('/', 1)[-1]
        if thirdparty.sha256(path) != digest:
            sys.exit(f'pins: {path} does not have SHA-256 {digest}')
        return path
    thirdparty.fetch = fetch

    class Env:
        pass
    env = Env()
    env.cc = cc
    log = src/'q2-adbd.log'
    log.write_text('')
    thirdparty.build_adbd(src, src/'stage', env, log)
elif sys.argv[1:] == ['adb-mdns-sources']:
    tree = ast.parse((TOOLS/'build.py').read_text())
    value = next(n.value for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, 'id', None) == 'ADB_MDNS_SOURCES' for t in n.targets))
    print(' '.join(ast.literal_eval(value)))
elif sys.argv[1:] == ['uac1']:
    import hashlib
    import uac1
    ko = uac1.CACHE/uac1.PIN[0]/'usb_f_uac1.ko'
    if not ko.exists() or hashlib.sha256(ko.read_bytes()).hexdigest() != uac1.PIN[0]:
        sys.exit(f'pins.py: no {ko} with its pinned SHA-256: python3 tools/uac1.py builds it')
    print(ko)
elif sys.argv[1:] == ['release-key']:
    import signing
    key = signing.public_key(signing.PUBLIC_KEY.read_bytes())
    print('{' + ','.join(f'0x{b:02x}' for b in key) + '}')
elif sys.argv[1:] == ['bluez-options']:
    print(' '.join(thirdparty.BLUEZ_OPTIONS))
elif sys.argv[1:2] == ['bluez-patch'] and len(sys.argv) == 3:
    import subprocess
    subprocess.run(['patch', '-p1', '-d', sys.argv[2]], input=thirdparty.BLUEZ_VAMP_PATCH, text=True, check=True)
elif sys.argv[1:2] == ['bluez-storage'] and len(sys.argv) == 3:
    import re
    config = pathlib.Path(sys.argv[2])/'config.h'
    text, n = re.subn(r'^#define STORAGEDIR ".*"$', f'#define STORAGEDIR "{thirdparty.BLUEZ_STORAGE}"', config.read_text(),
                      flags=re.M)
    if n != 1:
        sys.exit('pins: config.h has no STORAGEDIR to point at the Q2\'s store')
    config.write_text(text)
elif sys.argv[1:] == ['bluealsa-options']:
    print(' '.join(thirdparty.BLUEALSA_OPTIONS))
elif sys.argv[1:2] == ['bluealsa-patch'] and len(sys.argv) == 3:
    import subprocess
    for text in thirdparty.BLUEALSA_PATCHES.values():
        subprocess.run(['patch', '-p1', '-d', sys.argv[2]], input=text, text=True, check=True)
elif sys.argv[1:2] in (['freeaptx'], ['ldac']) and len(sys.argv) == 5:
    src = pathlib.Path(sys.argv[2])

    class Env:
        pass
    env = Env()
    env.cc, env.vars = sys.argv[3], {'AR': sys.argv[4]}
    log = src/f'q2-{sys.argv[1]}.log'
    log.write_text('')
    (thirdparty.build_freeaptx if sys.argv[1] == 'freeaptx' else thirdparty.build_ldac)(src, src/'stage', env, log)
else:
    sys.exit(__doc__)
