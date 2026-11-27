# Third-party components

Shanling built the rootfs's open-source libraries and programs years before V1.36 shipped, and a scan of a build (`tools/scan.py --only firmware`) matched 294 known vulnerabilities in them, 28 critical. `tools/thirdparty.py` rebuilds the worst of them from upstream releases and `tools/build.py` installs the results in place of stock's, under stock's own file names, so every symlink and every program's `DT_NEEDED` reaches the new copy. Nothing prebuilt is committed: the script fetches pinned sources, checks their SHA-256, compiles them and caches the output under `~/.cache/q2-thirdparty`.

| Component      | Stock            | Rebuilt                | Files replaced                                                                                     |
| -------------- | ---------------- | ---------------------- | -------------------------------------------------------------------------------------------------- |
| OpenSSL        | 1.1.1f           | 1.1.1w                 | `usr/lib/libssl.so.1.1`, `usr/lib/libcrypto.so.1.1`, `usr/lib/engines-1.1/{afalg,capi,padlock}.so` |
| curl           | 7.73.0 (OpenSSL) | 8.23.0 (mbedTLS 3.6.8) | `usr/lib/libcurl.so.4.7.0`                                                                         |
| zlib           | 1.2.11           | 1.4                    | `usr/lib/libz.so.1.2.11`                                                                           |
| SQLite         | 3.19.3           | 3.54.1                 | `usr/lib/libsqlite3.so.0.8.6`                                                                      |
| libpng         | 1.2.37           | 1.2.59                 | `usr/lib/libpng12.so.0.37.0`                                                                       |
| BusyBox        | 1.31.1           | 1.38.0                 | `bin/busybox`                                                                                      |
| wpa_supplicant | 2.9              | 2.12                   | `usr/sbin/wpa_supplicant`, `usr/sbin/wpa_cli`, `usr/lib/libwpa_client.so`                          |
| FFmpeg         | 4.2.2            | 4.2.11                 | `usr/bin/ffmpeg`, the eight `usr/lib/lib{av,sw}*.so.*` libraries                                   |

A library keeps stock's file name whatever its version: `libz.so.1.2.11` holds zlib 1.4. The repack keeps every stock inode's name and metadata (`tools/build.py` checks it), and nothing reads the name past the soname link. The build's `manifest.json` lists each replaced file under `thirdparty`, with its stock and new SHA-256, and each component's version.

## Toolchain

Stock was compiled with Codescape GNU Tools 2020.06-01 for MIPS MTI Linux (GCC 9.3.0, glibc 2.28), as FFmpeg's banner and glibc's version string say. `tools/thirdparty.py` uses the same release, fetched from `codescape.mips.com` (2.3 GB, SHA-256 in `TOOLCHAIN`) and unpacked into the cache once. Its `mipsel-r2-hard-nan2008` multilib is the rootfs's own glibc build: the same 2.28 with the same symbol versions, and `ld-linux-mipsn8.so.1`. Every component is compiled with `ARCH` (`-EL -march=mips32r2 -mabi=32 -mhard-float -mfp64 -mnan=2008 -mabs=2008`), which gives stock's ELF header flags (`0x70001407` for a library, `0x70001405` for a program) and FP ABI (hard float, 32-bit CPU, 64-bit FPU). A NaN-encoding or FP-ABI mismatch would not load.

The payload and the helper programs (`compile_helper` in `tools/build.py`) are built with clang and lld against the stock libraries alone. Only `tools/thirdparty.py` needs the Codescape toolchain.

## The build step

`install` in `tools/thirdparty.py`, called from `tools/build.py` just before the rootfs is packed:

1. builds each component in `INSTALL` (with its dependencies) unless the cache has it, stripping each file;
2. unpacks the stock rootfs and runs `check_abi` (below), stopping the build on any problem;
3. swaps each stock inode's contents for the rebuilt file.

A component is cached at `~/.cache/q2-thirdparty/out/NAME-VERSION-KEY`. The key hashes the source's SHA-256, the recipe function's text and that of the helpers and constants it names, the toolchain, the flags and each dependency's key, so editing a recipe rebuilds that component and those built on it, and nothing else. Builds run `make -j2` (`Q2_THIRDPARTY_JOBS` changes it). A first build takes about an hour on a laptop, most of it FFmpeg; a cached one adds seconds. `-ffile-prefix-map`, `SOURCE_DATE_EPOCH` (each release's own date) and `TZ=UTC` keep build paths and dates out of the bytes, so two builds still give the same `update.tar`. OpenSSL's `compiler:` string and FFmpeg's configuration line name the toolchain's and the cache's paths, so two machines give the same bytes only with the cache at the same path (`Q2_THIRDPARTY_CACHE`).

```sh
python3 tools/thirdparty.py build [NAME...]   # build or reuse components, print where each is
python3 tools/thirdparty.py check STOCKFS     # check_abi of the replacements against an unpacked stock rootfs
python3 test/thirdparty.py BUILD QEMU_MIPSEL  # the replaced programs and libraries, run in a chroot of BUILD's rootfs
```

## The ABI check

`check_abi` reads every ELF file in the stock rootfs with `readelf`: its flags and FP ABI, its `DT_NEEDED` and run path, and its dynamic symbols. For each replacement it requires stock's flags and FP ABI. Then, with the replacements in place, it resolves every file's undefined symbols through its `DT_NEEDED` closure, as `ld.so` does: a versioned import (`curl_easy_init@CURL_OPENSSL_4`) must find that version, and an imported data object must keep its size (a copy relocation would truncate it). Problems stock already has (a plugin that imports from its host program, `libbtctl.so`'s undeclared GLib imports) are subtracted, so what is left is what a replacement broke. Any problem stops the build with the file and symbol.

`test/thirdparty.py` then runs the results: each program's version, `busybox --list` against stock's, `ld.so --list` on demo, hciplayer (`q2player`, under stock's name), bluetoothd, bluealsa, wpa_supplicant, ntpdate, shairport and ffmpeg, a libpng and zlib program, SQLite's FTS3 and R\*Tree, and an FFmpeg decode, stack, scale and encode like the payload's art paths. For TLS, the curl tool built beside libcurl verifies GitHub, Last.fm and radio-browser against `/etc/scrobble-ca.pem`, and refuses a wrong host, a self-signed certificate and example.com, whose root that bundle does not pin (against the host's bundle it verifies).

## Components

### OpenSSL

1.1.1w is the last 1.1.1 release: same sonames, same symbol versions (`OPENSSL_1_1_0` to `OPENSSL_1_1_1`). Configured as stock was (`linux-generic32`, no assembly, `-O3`, `OPENSSL_USE_NODELETE`), with `OPENSSLDIR` `/etc/ssl` (where the rootfs keeps `openssl.cnf`) in place of stock's build-host path. It leaves out what no program in the rootfs calls and where most advisories since 1.1.1w's end of life are (`OPENSSL_OMIT`): binary-field elliptic curves, SM2, DTLS, CMS, time stamping and certificate transparency. Each takes its exports with it, so `check_abi` would stop the build if anything imported one (stock's libcurl imports SRP, which is why SRP stays). demo uses it for hashes, HMAC, DES and Base64; wpa_supplicant for EAP's TLS; ntpdate, shairport and libavformat too.

### curl and mbedTLS

curl 8.18 and later do not build against OpenSSL 1.1.1, and the last that do (8.17.0) are missing the fixes for some 60 advisories published since. libcurl 8.23.0 therefore does its TLS with mbedTLS 3.6.8 (the long-term-support branch), linked into `libcurl.so` statically: version scripts keep every mbedTLS symbol out of libcurl's exports, so it cannot clash with OpenSSL 1.1.1 in the same process. The symbols stay versioned `CURL_OPENSSL_4`, the version demo imports (`--enable-versioned-symbols=OPENSSL_`).

- **Dates.** mbedTLS is built without `MBEDTLS_HAVE_TIME_DATE`, so it checks no certificate's validity dates. The Q2's clock may never have been set, and past 2038 its 32-bit `time()` wraps ([internals: 2038](internals.md#2038)). A chain must still reach a root in the caller's `CURLOPT_CAINFO` and name the host. The payload sets no `CURLOPT_SSL_CTX_FUNCTION`: libcurl would hand a callback mbedTLS's configuration, not an OpenSSL `SSL_CTX`.
- **Features.** Protocols are `http`, `https` and `file`, the schemes demo and the payload use (stock had 18 more, among them FTP, SMB, TFTP, IMAP and telnet). No NTLM, Kerberos, SPNEGO, DNS over HTTPS, Alt-Svc, MIME posts or `.netrc`: demo imports only `curl_easy_*`, `curl_slist_*` and `curl_global_init`, so it cannot have used them. IPv6, the threaded resolver, zlib, cookies, proxies and Unix sockets stay.
- **mbedTLS's configuration** (`MBEDTLS_UNSET`): a TLS 1.2 and 1.3 client. No server side, DTLS, renegotiation, PSK key exchanges, static ECDH, CCM, Camellia, ARIA, DES or the small and Brainpool curves. TLS 1.0 and 1.1 are gone, as in every current TLS library: a server that offers nothing newer fails to connect.

### zlib

1.4 keeps the soname `libz.so.1` and the `ZLIB_1.2.*` symbol versions. Its consumers include demo, libpng, libavcodec and libtag.

### SQLite

3.54.1 with stock's options (FTS3, R\*Tree, synchronous FULL, the system allocator), without the JSON and math functions and extension loading, which 3.19.3 did not build in and nothing calls. demo uses four functions (`sqlite3_open`, `_exec`, `_close`, `_free`). A database written by 3.54.1 is still read by 3.19.3: the file format is the same, as long as nothing turns on a newer feature such as `STRICT` tables.

### libpng

1.2.59, the last 1.2 release (`libpng12.so.0`, symbol version `PNG12_0`). demo and libawtk read PNGs through it.

### BusyBox

1.38.0 from `defconfig`, less every applet stock does not have and plus those defconfig leaves off, so `busybox --list` is stock's 274 applets exactly (`BUSYBOX_APPLETS`; the recipe stops if the built table differs). Each applet has at least stock's features. Dynamically linked, as stock's.

### wpa_supplicant

2.12, with the features stock 2.9's strings show: nl80211 and wext drivers, WPA2 and WPA3 (SAE, OWE), WEP, the common EAP methods over OpenSSL, WPS, 802.11r, Hotspot 2.0, AP and mesh modes, TDLS, FILS, autoscan and bgscan (`WPA_CONFIG`). No D-Bus, P2P or DPP, which stock did not have either. demo talks to it through its control socket (its own copy of `wpa_ctrl`) and `wpa_cli`, both stable across releases. It links stock's libnl 3 (`libnl-3.so.200`, `libnl-genl-3.so.200`); libnl 3.5.0 is built only to compile and link against.

### FFmpeg

4.2.11, the last 4.2 point release, with stock's configuration (`FFMPEG_CONFIG`, buildroot's). A 4.2 point release keeps every library's version (libavcodec 58.54.100 and the rest), so their exports match. Only `/usr/bin/ffmpeg` and the FFmpeg libraries themselves use them; the payload runs `/usr/bin/ffmpeg` to decode cover art, photos, playlist mosaics, the boot logo and chapters.

### q2player's FFmpeg

`q2player`, the player (stock's hciplayer is not shipped: [player-plan](player-plan.md)), links an FFmpeg of its own, 7.1.5, statically, built by `tools/player.py` and not by this script. It is configured `--disable-everything` and then given only what [player.md: Formats](player.md#formats) and the player's sources need, in `tools/player.py`'s lists: the demuxers `flac mp3 wav aiff mov ogg ape wv asf aac ac3 dts dsf iff tak`, a decoder for each codec they carry (DSD's and DST among them, MPEG audio as the integer decoder), their parsers, the protocols `file http tcp https tls`, TLS through mbedTLS 3.6.8 (the image's build of it, with the same `MBEDTLS_UNSET` as libcurl's; for the host tests `tools/player.py` builds the same release so), the bitstream filter `aac_adtstoasc`, and `libswresample` for Bluetooth. No muxer, encoder, filter, scaler, device or hardware decoder is built, and no container nothing hands the player (Matroska, MPEG-TS, HLS: Tidal, DLNA and the phone's push send one file's URL, and Internet Radio does not use the player). It is C alone (`--disable-asm`), so the Q2 and the machine that makes the reference decodes run the same arithmetic, and each function is in its own section (`-ffunction-sections -fdata-sections`), so the player's `--gc-sections` link drops what it never calls.

### adbd

Not a replacement: `--dev` builds add `/usr/bin/adbd` ([adb.md](adb.md)), which stock never had. The `adbd` recipe builds Android 4.2.2's adbd from Debian's `android-tools` 4.2.2+git20130218 source (`SOURCES`; `ADBD_DEBIAN`, the 3ubuntu41 packaging whose patches add adbd's non-Android build), the source Buildroot's `android-tools` package uses, plus AOSP's `libmincrypt` from the same release (`android-4.2.2_r1`, fetched file by file from the AOSP mirror, `ADBD_MINCRYPT_FILES`) for the RSA check, then `tools/adbd.patch`: Buildroot's fixes for building it off Ubuntu and the Q2's changes, listed at the patch's head. Licenses: Apache-2.0 for adbd and libcutils (`debian/copyright`, kept in the stage as `copyright`), BSD-3-Clause for libmincrypt (its `NOTICE`, kept as `mincrypt.NOTICE`). It is built `-Os` with unused sections dropped and linked against the toolchain's glibc 2.28, the rootfs's own build, needing `libc.so.6` and `libpthread.so.0` alone: 64 KB stripped, about 25 KB in the squashfs. Linked statically it is 617 KB (about 300 KB in the squashfs), more than the rootfs has room for beside the boot animation. It is not in `INSTALL` and `check_abi` does not see it, as it replaces nothing; `test/build.py --build` checks its interpreter and libraries.

Newer adbd (Android 5 and later) is C++ against libbase, libcutils and BoringSSL, much more to build for the same shell, sync and authentication; 4.2.2 is the first release with RSA authentication, and its FunctionFS support is what Buildroot ships. What it lacks was added in the patch: `exec:` (adb exec-out), Microsoft OS descriptors, keeping `ep0` open, and a TCP-only mode (`--tcp PORT`) for [adb over Wi-Fi](adb.md#adb-over-wi-fi). It has no TLS, so no wireless pairing as Android 11's: the first key is trusted over USB or the card.

## Not replaced

- **BlueZ 5.66.** Shanling's bluetoothd is not upstream BlueZ: it adds a `shanling` profile (SyncLink, an RFCOMM remote for Shanling's app), and its AVCTP, AVRCP and A2DP code reads and writes `libbtctl.so`'s globals (`g_play_status`, `g_track_meta`, `g_high_priority_lcodec`, `g_synclink_mode`) and calls its `bluez_main`. Shanling has published no source for these changes, so an upstream bluetoothd would lose headset track info, codec priority and SyncLink. The build changes a few words of it and appends two functions, so the control channel's GetElementAttributes answers and the AVRCP record names cover art ([internals](internals.md#track-metadata), [cover art](internals.md#bluetooth-cover-art)). The scan's BlueZ findings (AVRCP, `CVE-2023-27349` and `CVE-2023-44431`; the PBAP ones are in obexd, which the rootfs does not have) stay. `libbluetooth.so.3` is left with it.
- **glibc 2.28.** Every program in the rootfs links it, and it is the toolchain's own build. A newer glibc would need every stock program and library to keep working against it, with nothing to test most of them on but the device. Its findings stay.
- **The Linux kernel (5.10.21).** It is Shanling's, with drivers for the X2000 SoC, its codec and its Wi-Fi and Bluetooth chips, and they publish no source. Out of reach.
- **Others the scan does not name.** syft names few components, and `PROBES` in `tools/scan.py` covers the ones above. The rootfs also has, unchanged: thttpd 2.29 (stock's Wi-Fi File Transfer server, which nothing starts: [localsend](localsend.md#what-it-replaces)), shairport (AirPlay receiver), ntpdate 4.2.8p15, D-Bus 1.12, GLib 2.74, expat, libjpeg 7, ncurses, readline, ALSA, TagLib and Shanling's own libraries.

### What reaches them

Which of the unreplaced code someone else can reach depends on what is listening, and stock's `config.ini` starts with Wi-Fi, Bluetooth, SyncLink, DLNA and AirPlay off (`WIFI`, `BLUETOOTH`, `SYNCLINK`, `DLNA`, `AIRPLAY` all 0). Nothing listens at boot. Then:

| Turned on                | Listening, and who can reach it                                                                                                                                           |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Wi-Fi                    | Clients only: wpa_supplicant 2.12 (rebuilt), busybox `udhcpc`, the kernel's network stack and the vendor Wi-Fi driver, to the access point and anyone on the same network |
| LocalSend page           | q2send on :53317 (TCP and UDP), TLS through the rootfs OpenSSL, only while its page or the share sheet is open ([localsend](localsend.md#when-it-listens))                |
| Bluetooth                | bluetoothd (BlueZ 5.66) and the kernel's L2CAP, to anyone in radio range: connectable while on, discoverable for 180 s ([building](building.md#what-the-rootfs-drops))    |
| SyncLink                 | TCP 1751, a UDP multicast responder, RFCOMM and a BLE GATT server, all in demo; kept off, with no Settings row to turn it on ([internals](internals.md#synclink))         |
| DLNA, AirPlay            | Shanling's UPnP renderer (`libdmr.so`) and `shairport`, on the network                                                                                                    |
| Spotify (card app)       | librespot's Connect discovery, once its row is pressed                                                                                                                    |

Checked against that: bluetoothd has no `input`, `hog` or `network` plugin built in (its plugins are `hostname`, `wiimote`, `autopair`, `policy`, `a2dp`, `avrcp`, `scanparam`, `deviceinfo` and `battery`), so the unauthenticated HID keystroke injection fixed in BlueZ 5.71 (`CVE-2023-45866`) and the PAN code have nothing to reach, and the rootfs has no obexd (the PBAP findings). glibc 2.28 predates the set-uid loader's `GLIBC_TUNABLES` overflow (`CVE-2023-4911`, from 2.34), which would matter to a set-uid program (Vamp adds none). Of its other findings, the ones a network peer could reach go through the resolver or iconv; the device runs no nscd and no Sun RPC.

What stays reachable and can't be fixed in place: the kernel's Bluetooth and Wi-Fi code (5.10.21 has the L2CAP bugs fixed in late 2022 releases, `CVE-2022-42895` and `CVE-2022-42896`, the second over LE, which only SyncLink's BLE server advertises for), BlueZ's AVRCP findings while a headset or phone is connected, and the local escalations a process running as another user could use (Dirty Pipe, `CVE-2022-0847`, fixed in 5.10.102). Bluetooth off when it isn't used is the one control for the first two.

Considered and not done:

- **LE off** (`ControllerMode = bredr` in `/etc/bluetooth/main.conf`): removes the LE radio path, and so `CVE-2022-42896` and bluetoothd's GATT, at the cost of Car Remote's link, which is LE (`patch/coda_rc_ble.c`, [remote-protocol](remote-protocol.md#bluetooth-le-primary)), and SyncLink's BLE transport, which is kept off. A one-line `BT_MAIN` change, not made while Car Remote needs LE; A2DP, AVRCP and the Bluetooth receiver are BR/EDR.
- **sysctls** (`kernel.kptr_restrict`, `kernel.dmesg_restrict`, `user.max_user_namespaces=0`; the kernel has all three, and no BPF or userfaultfd): they only slow a local unprivileged attacker, nearly every process is root anyway, and Dirty Pipe needs no namespace or kernel address. Not worth a boot-script change on their own.
- **bluetoothd's other plugins** (`--noplugin=wiimote,scanparam,deviceinfo,battery`): each reads a connected device's records; dropping them needs a wrapper in front of demo's `bluetoothd &` that keeps the process name `killall` matches, for little less code reachable.

Effort, if taken on:

| Work                                                                                                                                                                                                                                                              | Estimate                                                |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| BlueZ: one AVRCP finding fixed by binary patch in Shanling's bluetoothd, with a fuzz case                                                                                                                                                                         | 2 to 4 days each                                        |
| BlueZ: Shanling's changes forward-ported to a current release                                                                                                                                                                                                     | blocked on Shanling's source                            |
| glibc: a newer one beside 2.28 for Vamp's own programs only (`q2player`, the helpers)                                                                                                                                                                           | 1 to 2 weeks; leaves every stock program on 2.28        |
| glibc: replaced for the whole rootfs                                                                                                                                                                                                                              | 4 to 6 weeks plus a device test of every stock program  |
| Kernel: a 5.10 LTS with the vendor's X2000, codec, Wi-Fi and Bluetooth drivers                                                                                                                                                                                    | blocked on Shanling's GPL source; weeks once it arrives |

## Artwork

Artwork that ships in the firmware, drawn from someone else's design. Fonts are in `assets/fonts`, each with its
license (`docs/ipod.md`).

| Component                                    | License | Files                                                          | Used for                                                                                                       |
| -------------------------------------------- | ------- | -------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Phosphor Icons, `@phosphor-icons/core` 2.1.1 | MIT     | `assets/icons/phosphor/*.svg`, `assets/icons/phosphor/LICENSE` | Theme: OLED's glyphs, drawn into bitmaps at build time by `tools/art.py` ([oled-design](oled-design.md#icons)) |
| Libre Baskerville (2.005, bold and regular, in Cabin Solitaire) | OFL-1.1 | `assets/games/solitaire.bin`, `patch/game_sudoku_art.inc`, `assets/games/LibreBaskerville-OFL.txt` | Cabin Solitaire: the cards' indexes and the figures and words of its slip and boards ([games-engine](games-engine.md#cabin-solitaire)); the Evening Puzzle: its printed clues, tallies and level names. Glyph masks of only the sizes and letters each draws |
| Playfair Display (1.203, weight 800, in Cabin Solitaire) | OFL-1.1 | `assets/games/solitaire.bin`, `patch/game_sudoku_art.inc`, `assets/games/PlayfairDisplay-OFL.txt` | Cabin Solitaire's two board titles and the Evening Puzzle's masthead, title and SOLVED stamp, as glyph masks. Its Reserved Font Name is not used: no font is shipped, only the masks |
| Patrick Hand                                 | OFL-1.1 | `patch/game_sudoku_art.inc`                                     | The Evening Puzzle's handwritten digits and notes |
| Special Elite                                | Apache-2.0 | `patch/game_sudoku_art.inc`                                  | The Evening Puzzle's small typewritten capitals |
| Bungee 2.000 (The Bungee Project Authors)    | OFL-1.1 | `assets/popstall/art.bin` (its glyphs only), `assets/popstall/Bungee-OFL.txt` | Pop Stall's type: the glyphs it draws at 9, 11, 14, 20 and 30 px, pictures made from the font by the game's design ([games-engine](games-engine.md#pop-stall)); the notice ships beside them in `usr/share/q2games/popstall/` |
| Bungee 2.000 and Righteous 1.000 (fonts)    | OFL-1.1 | `assets/games/callerten.bin` (glyph masks), `assets/games/callerten-fonts.txt` | Caller Ten: the figures, the radio's line and the pad's headings, drawn into masks by the design's `export.py` ([games-engine](games-engine.md#caller-ten)) |
| Baloo 2 (Ek Type), Bold                      | OFL-1.1 | `assets/games/embergarden.bin` (its letters drawn as masks; the font file is not in the repository or the firmware) | Ember Garden's lettering, drawn by the design's `sky-defense/src/export.py` (`text_parts`, run by `export_retune.py`) ([games-engine](games-engine.md#ember-garden)) |

The Evening Puzzle's masks are packed by `tools/game_art.py` from the font files (`FONTS` names each and its SHA-256), which stay out of the repository ([games-engine](games-engine.md#evening-puzzle)); Cabin Solitaire's are in its art file, written by the design's `src/export.py`.

## What the scan still finds

`tools/scan.py --only firmware` on a stock-library build and on one with these replacements (November 2026):

| Component      | Stock: matches (critical, high) | Rebuilt: matches (critical, high) |
| -------------- | ------------------------------- | --------------------------------- |
| OpenSSL        | 54 (4, 26)                      | 31 (3, 15)                        |
| curl           | 20 (5, 6)                       | 0                                 |
| zlib           | 2 (1, 1)                        | 0                                 |
| SQLite         | 34 (4, 15)                      | 1 (0, 1)                          |
| libpng         | 29 (2, 13)                      | 14 (1, 7)                         |
| BusyBox        | 16 (1, 10)                      | 3 (0, 3)                          |
| wpa_supplicant | 7 (2, 2)                        | 1 (0, 1)                          |
| FFmpeg         | 66 (2, 36)                      | 55 (1, 29)                        |
| BlueZ          | 10 (0, 5)                       | 10 (0, 5), not replaced           |
| glibc          | 55 (7, 22)                      | 55 (7, 22), not replaced          |
| thttpd         | 1 (0, 0)                        | 1 (0, 0), not replaced            |
| All            | 294 (28 critical, 136 high)     | 171 (12 critical, 83 high)        |
| Subsonic app's Go standard library | not in stock | 0, built with Go 1.27.4 (77 with Go 1.22.2) |

A release build of the branch that retired stock's player (`tools/scan.py --only firmware`, 2026-11-18, grype's database of that day) names no FFmpeg 3.0 and no OpenSSL 1.0.1: syft finds FFmpeg 4.2.11 (`libavcodec.so.58`, the payload's `ffmpeg` command) and no other FFmpeg, the probes find OpenSSL 1.1.1w, and `usr/bin/hciplayer` is `q2player`, whose FFmpeg 7.1.5 and mbedTLS 3.6.7 are static and name no version string, so the scan cannot see them. It matched 348 vulnerabilities (17 critical, 147 high). The table above predates the change and counts what the database held then: the same components now match more (FFmpeg 4.2.11 154, from 55), and the Subsonic card app's Go 1.22.2 standard library (78: 5 critical, 36 high) is listed too, which that table does not carry.

What is left of the rebuilt components is in releases nothing newer replaces with the same ABI:

- **OpenSSL 1.1.1w** is the last public 1.1.1 release (end of life since September 2023). The findings are advisories published since, fixed only in 3.x and in OpenSSL's paid 1.1.1 support. The scan goes by the version string, so it still lists the ones in code this build leaves out (CMS, DTLS, time stamping, SM2 signing, binary curves: 12 of the 31). Of the rest, those reachable from the network are in ASN.1 and certificate parsing (`CVE-2026-34180`, `CVE-2026-7383`, `CVE-2026-35189`), through FFmpeg's TLS and EAP-TLS; the others need PKCS#12 or PKCS#7 files, `SSL_free_buffers` or NPN, none of which the rootfs's programs use on untrusted input. libcurl does not use it; what does is demo's hashing, HMAC and Base64, EAP's TLS in wpa_supplicant, ntpdate, shairport, and FFmpeg's `https` (Internet Radio streams through `ffmpeg`). Moving those to OpenSSL 3 needs a new soname (`libssl.so.3`) and rebuilt or patched consumers, demo among them.
- **FFmpeg 4.2.11** is the last 4.2 release; the 4.2 branch is unmaintained, and most findings are 2026 advisories fixed only in maintained branches. A maintained FFmpeg has new sonames (`libavcodec.so.61` and up) and a changed command line, so it means adding its libraries beside or in place of 4.2's, rechecking every command line the payload runs (`patch/video.c`, `coverflow.c`, `playlist_art.c`, `chapters.c`, `system.c`, `tidal.c`, Internet Radio) and making room in the rootfs.
- **libpng 1.2.59** is the last 1.2 release; libpng 1.6 has another ABI (`libpng16.so.16`) that demo does not link.
- **BusyBox 1.38.0, SQLite 3.54.1** are the newest releases; their findings have no release with a fix yet. **wpa_supplicant**'s one (`CVE-2024-5290`) is in a distribution's D-Bus policy, which this build does not have.
- **mbedTLS 3.6.8** is not visible to the scan (libcurl links it statically and keeps no version string, and so does `q2player`). It is the newest of the 3.6 long-term-support branch (2026-11-16, checked 2026-11-25), whose support ends 2027-03-28; 4.1 LTS is the branch after it. 3.6.8 carries 21 advisories, almost all in code `MBEDTLS_UNSET` leaves out (the server side, session tickets and cache, renegotiation, ECJPAKE, PSK, PKCS#5 and #12, NIST key wrap, DTLS, and the hardware AES and p256-m paths, which MIPS does not build). What reaches this client: `CVE-2026-115490` (a TLS 1.2 ServerHello with `legacy_version` 0x0304 completed a handshake that no secret fed into), `CVE-2026-93843` (a TLS 1.3 setup failure leaked memory), the CertificateRequest parsing fixes (a truncated message, an odd-length signature list), the digest fix for 4 GiB or more in one call, and the Spectre v1 hardening of ECDSA, ECC and the cipher module, which needs a local attacker. [firmware](firmware.md) lists every component's version against the newest public.

## Bumping a version

1. Change the component's entry in `SOURCES` (version, URL, SHA-256). Check the release's signature first (the keys are below) and take the SHA-256 from the verified file.
2. If the library's real file name changed (`libcurl.so.4.8.0`), change its `INSTALL` entry's right-hand side. The left-hand side is stock's name and stays.
3. `python3 tools/thirdparty.py build NAME`, then a build and `test/thirdparty.py` on it. `check_abi` stops the build if the new release drops a symbol something imports.
4. Update the table at the top and this page's notes on the component, and add a line to [changelog.md](changelog.md).

Signing keys the pinned releases verify against:

| Source         | Signed by                                         | Fingerprint                                         |
| -------------- | ------------------------------------------------- | --------------------------------------------------- |
| OpenSSL        | OpenSSL OMC                                       | `EFC0 A467 D613 CB83 C7ED 6D30 D894 E2CE 8B3D 79F5` |
| curl           | Daniel Stenberg                                   | `27ED EAF2 2F3A BCEB 50DB 9A12 5CC9 08FD B71E 12C2` |
| zlib           | Mark Adler                                        | `5ED4 6A67 21D3 6558 7791 E2AA 783F CD8E 58BC AFBA` |
| wpa_supplicant | Jouni Malinen                                     | `EC4A A0A9 91A5 F246 4582 D52D 2B6E F432 EFC8 95FA` |
| FFmpeg         | FFmpeg release signing key                        | `FCF9 86EA 15E6 E293 A564 4F10 B432 2F04 D676 58D8` |
| libpng         | Glenn Randers-Pehrson                             | `8048 643B A2C8 40F4 F92A 195F F549 84BF A16C 640F` |
| libnl          | Thomas Haller                                     | key ID `29C2 366E 4DFC 5728`                        |
| BusyBox        | the SHA-256 file on busybox.net                   |                                                     |
| SQLite         | the SHA3-256 on sqlite.org's download page        |                                                     |
| mbedTLS        | the SHA-256 in the GitHub release notes           |                                                     |
| Toolchain      | the SHA-256 on codescape.mips.com's download page |                                                     |
