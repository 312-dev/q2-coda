# Vamp OS: the base image

The first piece of [Vamp OS](vamp-os.md) built: a root filesystem made from source by Buildroot, with no file of Shanling's in it and no `demo`, that boots on Shanling's kernel and modules straight into Vamp's own programs. It brings [M5](vamp-os.md#m5-the-vamp-os-base-and-the-installer)'s base forward, beside the UI work; the installer, the services and the rest of M5 and M6 are not here yet ([What is missing](#what-is-missing)). **Nothing of it has run on a Q2.** Every claim below says whether it was measured (in a build or under the emulator, with its source) or is inferred.

## Build

```sh
tools/mac/q2env python3 tools/vampos.py build --out ~/q2-os                        # ~/q2-os/vampos.squashfs, manifest.json
tools/mac/q2env python3 test/os_image.py ~/q2-os                                    # the image's contents
tools/mac/q2env python3 test/os_image.py ~/q2-os --zip ~/q2-stock/"Q2 Firmware V1.36.zip"   # composed, then booted
```

On GRAYTHINKPAD the same commands run without `tools/mac/q2env`. `build` needs Linux, `cpio` and `rsync` beside [building](building.md#requirements)'s list, and a case-sensitive file system: Buildroot's tree and its build directory live in `/home/gray/.cache/q2-os` (`CACHE`), which on a Mac is the `q2-os` Docker volume `q2env` mounts there. The path is fixed on both machines because the toolchain's paths end up in its output. Downloads go to `~/.cache/q2-os/dl`.

| Piece | What |
| --- | --- |
| `os/` | The `BR2_EXTERNAL` tree: `configs/vamp_q2_defconfig`, the packages (`package/`), the root filesystem's own files (`board/q2/overlay/`), the post-build step, the vendor manifest (`vendor/V1.36.tsv`), one patch to Buildroot (`patches/buildroot/`) |
| `tools/vampos.py` | `build`, `compose`, `vendor-manifest`, `qemu`, `card` (its docstring). Not `tools/os.py`: a module named `os` in `tools/` is imported in place of Python's own by a script there run with `-S` |
| `test/os_image.py` | The checks below |
| `patch/q2vampos.sh`, `test/q2vampos.py` | The [card boot](#card-boot) from a `--dev` build of today's Vamp, and its test |

**Pinned.** Buildroot 2026.02.3, the long-term line, by SHA-256 (the one in buildroot.org's signed `.sign`). Every package Buildroot fetches is checked against its `.hash` file; ours carry the hashes `tools/player.py` (FFmpeg 7.1.5), `tools/thirdparty.py` (mbedTLS 3.6.8) and `tools/ui.py` (stb_truetype, stb_image) pin, and `test/os_image.py` fails when the two differ. Vamp's own sources are not read from the working tree: `build` exports `os/`, `tools/`, `player/`, `patch/` and `assets/fonts` at `HEAD`, and `ui/` and `patch/` at `Q2UI_COMMIT` (a commit of `feat/render-engine`, read-only here), and `manifest.json` names both commits. `--worktree` builds the working tree instead and marks the commit `-dirty`. The configure lines of FFmpeg and the mbedTLS settings come from `tools/player.py` and `tools/thirdparty.py` themselves (`os/support/pins.py`), so they have one definition.

## The toolchain

Buildroot's own: GCC 14.3, glibc 2.42, binutils 2.44, kernel headers 5.10 (the Q2 runs 5.10.21, so glibc assumes no system call it lacks), and 64-bit `time_t` across the image (`BR2_TIME_BITS_64`). The ABI is stock's, fixed by the hardware and every card app: MIPS32R2 little-endian, o32, hard float with a 64-bit FPU, NaN 2008 and abs 2008, `ld-linux-mipsn8.so.1` ([third-party: Toolchain](third-party.md#toolchain)).

Two departures from [the roadmap's toolchain plan](vamp-os.md#toolchain-and-libc), with reasons:

- **Buildroot's toolchain from the start, not Codescape first.** The roadmap's first step keeps stock's Codescape 2020.06 so the image can run `demo` and its libraries during the transition. This image runs no `demo`, so nothing needs glibc 2.28 itself, and Codescape is an x86-64 program the Mac's native image does not run. glibc keeps its old symbol versions, so a card app built against 2.28 should still load (inferred from glibc's compatibility rule; not tried).
- **One patch to Buildroot.** Buildroot ties MIPS32R2 to legacy NaNs and offers NaN 2008 only on R5 and R6. `0001-arch-mips-offer-nan-2008-on-generic-mips32r2.patch` lets the generic R2 variant choose it; `tools/vampos.py` applies it to the unpacked tree. GCC is then configured `--with-arch=mips32r2 --with-nan=2008 --with-fp-32=64`, and `-mabs=2008` is `BR2_TARGET_OPTIMIZATION`.

## What boots

BusyBox `init` (`/sbin/init`, and `/linuxrc` for a kernel line that names it) reads [`/etc/inittab`](../os/board/q2/overlay/etc/inittab): `/etc/init.d/rcS` once, then `runsvdir` over `/etc/sv`, restarted if it ends. No login on the serial port and root locked, as today's Vamp has it. [`rcS`](../os/board/q2/overlay/etc/init.d/rcS) in order:

1. `/proc`, `/sys`, a tmpfs `/dev` with `null` and `console`, `devpts`, `/dev/shm`, `/tmp`, `/run`, as stock's inittab makes them (stock's kernel may not have devtmpfs: stock mounts a tmpfs there, so this image does too).
2. `mdev -s`, then `mdev -d`, its daemon, so the devices the modules make get their nodes. [`/etc/mdev.conf`](../os/board/q2/overlay/etc/mdev.conf) loads no module (no `modprobe` in the image) and mounts the card on `/mnt/mmc` when it comes.
3. `/mnt/data` (left as it is when it is already mounted, as on a [card boot](#card-boot)): the UBI volume on the partition `/proc/mtd` names `userdata`, attached as `ubi1` as stock's `mount_ubifs.sh` does, mounted `-o sync`. **Never erased or formatted:** stock's script runs `flash_erase` on the partition when the attach or the mount fails; this image has no tool that could (`test/os_image.py` checks), and on any failure `/mnt/data` is a tmpfs for that boot and the boot log says why.
4. The modules (not run when any of them is loaded already, as on a card boot: the keyboard module takes its keys through a parameter file written once per load): Shanling's `driver_default_init_script.sh`, unchanged, from `/module_driver` (a link to `/vendor/module_driver`), as stock's `S11module_driver_default` runs it. It is 24 `insmod` lines with their parameters, run by this image's BusyBox. Then each `.ko` there is looked for in `/proc/modules` and the ones missing are logged.
5. After an install ([q2pkgd](pkgd.md#the-rescue-copy)), the card's rescue copy put back in `recovery-update/` (`q2pkgd restore`) as soon as the card is mounted, before anything else that can fail: Shanling's recovery removes the folder once it has flashed.
6. A wait of up to 5 s for `/dev/fb0`, and the input devices with their names.

Then init starts `runsvdir`, which starts the services below ([`/etc/sv`](../os/board/q2/overlay/etc/sv)) and starts each again when it ends; a `finish` script waits 10 s after a failure, so a service that cannot run yet does not spin. Each step writes a line to `/run/vamp/boot.log`, and once `/mnt/data` is mounted also to `/mnt/data/vamp/boot.log` (the last boot's kept as `boot.log.1`), with `dmesg.log`, `modules.log` (the module script's output) and `kernel.txt` (`/proc/cmdline`, `/proc/modules`) beside it. Nothing of Shanling's userspace runs: no program of theirs is in the image, and the vendor scripts are shell lines run by BusyBox.

| Service | Runs | What it waits for |
| --- | --- | --- |
| `adbd` | adb over USB ([adb](#adb)): `q2adb-gadget setup`, then `adbd`, and `q2adb-gadget bind` once it has written its descriptors | configfs and FunctionFS in the kernel, the USB controller |
| `adb-wifi` | `q2adb-wifi`, which runs `adbd --tcp 5555` and `q2adb-mdns` only while a computer is trusted | a key in `/mnt/data/adb_keys` or the card's `adb_keys` |
| `q2netd` | `/usr/bin/q2netd` ([q2netd](netd.md)): Wi-Fi on the saved networks, DHCP, Smart Wi-Fi, Q2NET/1 | `wlan0` (the radio's module), `/mnt/data/wpa_supplicant.conf` |
| `q2libd` | `/usr/bin/q2libd` ([q2libd](libd.md)): the card's songs into `/mnt/data/vamp/library.db` and the signed-in apps' catalogues into `/mnt/mmc/.vamp/catalogue.db`, `q2ui` restarted in place after each | the card's mount |
| `q2playd` | `/usr/bin/q2playd` ([q2playd](playd.md)): the server on `/tmp/sock_play`, the queue, what plays; `q2ui`'s command queue and now file, and Q2PLAY/1 | `/mnt/data/vamp/library.db` for a card song's file, the card's `.vamp/catalogue.db` for a server song's |
| `q2player` | `/usr/bin/q2player`, built as every Vamp image's (Car Audio's output on) against this image's alsa-lib, FFmpeg 7.1.5 and mbedTLS | `q2playd` on `/tmp/sock_play` (it waits for it up to 5 s, then `finish` waits 10 s before the next try) |
| `q2sysd` | `/usr/bin/q2sysd` ([q2sysd](sysd.md)): the power key (a press: the screen off or on; held 3 s: power off), the backlight, the battery (`/run/vamp/battery`), Q2SYS/1 | the input devices with `KEY_POWER`, `/sys/class/backlight`, the CW2015 |
| `q2ui` | `/usr/bin/q2ui --db /mnt/data/vamp/library.db --catalogue /mnt/mmc/.vamp/catalogue.db --fonts /usr/share/vamp/fonts --icons /usr/share/vamp/icons` | `q2libd`'s library, which is there (empty at first) before anything else |

## adb

The image is reachable for debugging on its own, with no `demo` and no today's Vamp, over USB and over Wi-Fi, with today's `--dev` key rules ([adb](adb.md)): the same adbd (Android 4.2.2's with `tools/adbd.patch`, built by `tools/thirdparty.py`'s own `build_adbd` against this image's glibc, `os/package/q2-adbd`), the same gadget script, Wi-Fi supervisor and mDNS responder (`patch/adb_gadget.sh`, `patch/adb_wifi.sh`, `patch/adb_mdns.c`, `os/package/q2adb`). [`vamp`](adb.md#vamp) and Google's `adb` both speak to it.

- **Keys**: a computer gets in only with its key in `/mnt/data/adb_keys` (a computer trusted with Always Allow under today's Vamp is trusted here too: the file is the same) or in the card's `adb_keys`. Nothing in the image answers adbd's prompt socket, so an unknown key is refused at once over USB too; there is no Allow card until `q2ui` has one. A first key on a Q2 that never trusted one comes on the card ([adb: Trusting the first key](adb.md#adb-over-wi-fi)).
- **No open port without a trusted key**: `q2adb-wifi` starts the Wi-Fi adbd and the `_adb._tcp` responder only while one of those files holds a line and `config.ini` has no `ADBWIFI=0`, and stops both within 10 s of that ending.
- **Wi-Fi itself** is `q2netd`'s ([q2netd](netd.md)): the saved networks, DHCP, and Smart Wi-Fi, which a computer let in over Wi-Fi holds on for 30 minutes.
- **Beside another adbd** (today's Vamp's, outside a card boot's chroot) each side yields to it, decided at run time by what is actually serving (`/usr/libexec/vamp/outer`): `adbd` when a process named `adbd` runs without `--tcp`, `adb-wifi` when something listens on TCP 5555, each looked at again every 30 s. Two Wi-Fi adbds cannot share port 5555, and with no outer one the image's own must run, or a card boot would leave nobody serving adb. In today's `--dev` S90play both of its adbds start before `q2vampos` hands over (its USB adbd and `q2adb-wifi` are lines ahead of the boot hook, read from a built image's `S90play`), so on a card boot they are normally there; the run-time rule covers the case where they are not.

**Tested without a Q2.** `test/os_image.py`'s boot: no port 5555 for 15 s with no key; a key written to `/mnt/data/adb_keys` opens it and lets `vamp` in, which reads the image's own `/etc/vamp-release` over `exec:`; another key is refused; the port closes once the file is gone; on a card boot neither adb service starts. `--perturb adb-open` (the Wi-Fi adbd with no key check) and `adb-off` fail it, and the card-boot check fails on the image built before the services yielded. `test/adb.py` run on this image (a folder holding `vampos.squashfs` as `rootfs.squashfs`, `--qemu` the pinned one) passes its 93 checks with this image's adbd and glibc: the FunctionFS descriptors, key refusal, the card's keys, `shell:`, `exec:`, push and pull, the refused services, adbd `--tcp` driven by `vamp`, `q2adb-wifi`, and `q2adb-mdns` found by `vamp`'s browse. **Needs a Q2**: whether the USB controller is in device mode with nothing of stock's having set it (today `demo` runs stock's USB code), the gadget on the real UDC, `wlan0` associating, and DHCP.

## Hot reload

A standing UI requirement (the owner, 2026-11-27: a `q2ui` that exits is restarted in place, never left dark). Under Vamp OS's supervisor:

- `kill -HUP` makes `q2ui` start itself again in place (`ui/main.c` `restart`, on `feat/render-engine`): the same process, its options and binary read anew, its pages kept. `runsv` sees nothing.
- Any other end is started again by `runsv`: at once after 0 (Return on the first page), 3 (a page of `demo`'s asked for, which Vamp OS has none of) and 4 (the restart code), 2 s after a crash or a signal (`/etc/sv/q2ui/finish`), and `q2ui` comes back on the pages it had (`/tmp/q2ui.state`). So pushing a new binary over adb and ending the old one is a hot reload. `q2ui`'s own three-deaths-a-minute rescue page is not built yet.
- `q2playd` and `q2player` do not depend on `q2ui`: the queue, the song and the player's connection stay through a restart.

**Tested without a Q2.** `test/os_image.py`'s boot plays a 60 s song over Q2PLAY/1 to alsa-lib's null card (a test file, `/etc/asound.conf`, in the boot's copy only) and pauses it, sends `q2ui`'s process SIGTERM, and checks that `runsv` starts `q2ui` again within 5 s and that `q2playd` still has the song paused with the player connected; resumed, the song plays out. With a `finish` that waits 10 s after any end (`--perturb ui-slow`) the restart check fails. The null card takes audio as fast as it comes, so the song is paused for there to be one across the restart. Not tested here: `kill -HUP`'s in-place restart (under QEMU `/proc/self/exe` is QEMU, so it is a Q2 test, `feat/render-engine`'s).

## The vendor set

The files of Shanling's the image needs at run time: the 24 modules, their 25 scripts, the radio's 4 firmware and table files and the MCU's firmware, 54 files. [`os/vendor/V1.36.tsv`](../os/vendor/V1.36.tsv) names each by its stock path, SHA-256 and mode (made by `tools/vampos.py vendor-manifest ZIP`); the hashes are facts about the files, and the files are never committed or shipped ([study hygiene](vamp-os.md#study-hygiene)). `vampos.squashfs` has no `/vendor`; `tools/vampos.py compose` copies the image and appends one with `mksquashfs` (one thread, owners root, the base image's time on every file), from either source:

- `--zip ZIP`: the user's own stock firmware download, checked against the manifest's ZIP hash; for tests and a first install from stock with a computer.
- `--rootfs DIR`: an unpacked root filesystem, which on a Q2 is its own `/`: the installer's step 2 ([How an image is made on the device](vamp-os.md#how-an-image-is-made-on-the-device)). A file whose hash is not the manifest's stops the compose with its name.

`/vendor/module_driver`, `/vendor/firmware` and `/vendor/mcu` hold them; `/module_driver` and `/firmware` are links there, the paths the scripts, the Wi-Fi driver and `rtk_hciattach` name. `rtk_hciattach` and `cmd_mcu` are not in the set yet: nothing here starts Bluetooth or the MCU.

## The emulator, and what it cannot stand in for

`test/os_image.py BUILD --zip ZIP` composes the image and boots it under QEMU's user-mode MIPS emulator, as [the emulator](building.md#emulator-pool) runs stock: BusyBox `init` as PID 1 of a `bwrap` sandbox (`--as-pid-1`), every MIPS program started through the host's `binfmt_misc` entry for mipsel, which finds a static `qemu-mipsel` bound at its `-binfmt-P` path. That is `tools/vampos.py qemu`'s: `qemu-user-static` 8.2.2 for arm64 from Ubuntu 24.04 (Launchpad's copy of `qemu-user-static_8.2.2+ds-0ubuntu1.20_arm64.deb`, by SHA-256), unpacked once under `~/.cache/q2-os/qemu`; on an x86-64 machine pass `--qemu ~/q2-emu/qemu-mipsel-static`.

| Stands in | Does not stand in |
| --- | --- |
| Every program of the image, its dynamic linker and libraries, the ABI | The kernel: there is none. Shanling's kernel, its configuration, its system calls and their errors are the VM's (Linux 6.8 here) seen through QEMU |
| `init`, `rcS`, `runsvdir`, `runsv`, the services' scripts, in the image's order | The modules: `insmod`, `mount`, `mknod`, `mdev`, `ubiattach` and `dmesg` are stand-ins that record their arguments. No module is loaded, so whether each loads into Shanling's kernel under this init is a Q2 question |
| The framebuffer as a file of two 320x375 pages, the keys and the wheel as FIFOs (no ioctl answers: `q2ui` takes the file's size) | `/proc/mtd`, UBI, the card, the panel's timing, vsync, the DAC, ALSA (no sound card: `q2player` never opens one here) |
| The network: adb over Wi-Fi on this machine's loopback | Power, suspend, the watchdog, the PMIC's keys |

The boot passes when the record shows, in this order: `/proc`, the tmpfs `/dev`, `mdev -s` and `mdev -d`, the data step (here: no `userdata` in `/proc/mtd`, so a tmpfs), the 24 `insmod` calls in the order and with the parameters Shanling's scripts give (read from the vendor files themselves), `modules: done`, `fb0: present` and `rcS: done`, all before either service starts; then `q2ui` draws into the framebuffer, `q2player` connects to the image's `q2playd`, and a song asked for over [Q2PLAY/1](playd.md#q2play1) reaches `q2player` and its end comes back ("did not play": no sound card), and adb over Wi-Fi keeps its key rules ([adb](#adb)), and `q2ui` comes back after being ended while `q2playd` keeps its song ([Hot reload](#hot-reload)).

## Card boot

How Vamp OS first runs on a Q2 without writing its root filesystem to flash (the owner's go-ahead, 2026-11-27): a `--dev` build of today's Vamp hands the Q2 over to an image on the card, as it hands it to Rockbox ([boot: Rockbox](boot.md#rockbox)). Release builds do not have it.

| What | Where |
| --- | --- |
| The image | `/mnt/mmc/.vamp/os/vampos.squashfs`, a composed image, and `vampos.squashfs.sha256` beside it (`sha256sum`'s line). `tools/vampos.py card --image composed.squashfs --card CARD` writes both and the switch |
| The switch | `.vamp/os/boot-once` (one boot: removed before anything else is done) or `boot-always` (every boot); `boot-once` wins when both are there |
| The hand-off | `/usr/bin/q2vampos` (`patch/q2vampos.sh`), run by `S90play` before the boot choice and demo (`tools/build.py` `VAMPOS_HOOK`) |
| The log | `/mnt/data/vamp/handoff.log`, a line per decision and outcome; Vamp OS's own `boot.log` beside it |

`q2vampos` waits up to 3 s for the card, as the Rockbox choice does (so a `--dev` boot with no card in starts 3 s later than before), then, in order: no switch is the usual boot and writes nothing; `boot-once` is removed (and a `boot-once` that cannot be removed is not used); Play/Pause held at power-on (`q2boot`, Rockbox's key) is the usual boot; the image and its `.sha256` must both be there and agree; the image is loop-mounted read-only at `/tmp/vamp-os-root` and must hold an `rcS` and `runsvdir`; `/mnt/data` and `/mnt/mmc` are bind-mounted into it; stock's splash and its `mdev` daemon are stopped; then the image's `rcS` and `runsvdir` run in a chroot of it. Every failure before `runsvdir` undoes what it did and is the usual boot, with its reason in the log; a Vamp OS that runs and then ends (`runsvdir` exits) reboots the Q2. So **a hang, a crash or a bad image, then a power cycle, is today's Vamp again**, unless `boot-always` is set, when Play/Pause held at power-on is the way out.

It is a chroot and not `switch_root`: `switch_root` must run as PID 1 and replaces an initramfs, and here PID 1 is stock's BusyBox `init` on a squashfs root. So stock's `init` stays PID 1, and what stock's boot started before `S90play` keeps running beside Vamp OS: `dbus-daemon`, `q2guard` (the overheat guardian, which is wanted), and on a `--dev` build `q2remote` and `adbd`, so **adb works while Vamp OS runs** (`adb shell cat /mnt/data/vamp/boot.log`, `ls /tmp/vamp-os-root`). The kernel and the modules are the ones stock's boot loaded; Vamp OS's `rcS` sees them loaded and `/mnt/data` mounted and leaves both ([What boots](#what-boots)). Nothing is written to flash but `/mnt/data/vamp/` (the two logs) and what Vamp OS's programs write in `/mnt/data` (`q2ui`'s `q2ui-stats.json`).

**Tested without a Q2:** `test/q2vampos.py` runs `q2vampos` under the host's `sh` (dash: the Q2 runs BusyBox `ash`) with stand-ins for `mount`, `umount`, `chroot`, `killall`, `q2boot` and `S10mdev`: no switch writes nothing, `boot-once` is gone before the key is read and before any mount, `boot-always` stays, Play/Pause, a missing image or hash file, a wrong or short hash, a failed loop mount, an image that is not Vamp OS, a failed `rcS` and an unremovable `boot-once` each end in the usual boot with their log line, and a hand-off runs the mounts, the stops and the two chroots in that order. Four broken copies of the script (boot-once removed after the mount, no hash compare, a wrong exit status, a log line on every boot) each fail it (`Q2VAMPOS_SCRIPT`). `test/build.py` checks the hook in `S90play` (only exit 2 reboots; 1, 0, 3 and a missing `q2vampos` go on to demo; the old `[ $? = 1 ] ||` form fails it) and, with `--build`, that a `--dev` image ships `q2vampos` and a release has neither it nor the hook. `test/os_image.py --card-boot` runs the image as `q2vampos` does, its rcS and `runsvdir` beside stand-ins for what today's Vamp has running (its `wpa_supplicant`, `udhcpc` and Wi-Fi adbd on 5555), with `/mnt/data` mounted and every vendor module in `/proc/modules`: no `insmod`, no attach, the services after `rcS`, the image's adb off beside the outer adbd, and Wi-Fi left exactly as it was ([q2netd: On a card boot](netd.md#on-a-card-boot)); with `--no-outer-adbd` (today's Vamp running no adbd) the image's adbd starts on USB and Wi-Fi and a trusted key gets in over Wi-Fi, which `--perturb adb-marker` (the old rule: yield on any card boot) fails. **End to end**, `test/card_e2e.py BUILD --zip ZIP`: the image composed and laid out on a stand-in card by `tools/vampos.py card`, the real `q2vampos.sh` under the host's `sh`, its loop mount an unsquashfs of the card's image into the mount point and each chroot a `bwrap` of that root with its binds and QEMU: the hand-off's log, `boot-once` gone, the image's rcS taking the card-boot path (data kept, modules not loaded again), `q2ui` drawing, and a song from the card's own library (`q2libd`'s scan) played over Q2PLAY/1; then the next boot, `boot-once` spent, is the usual one, and a wrong `.sha256` is refused with nothing mounted (a copy of the script without the hash compare fails that, `Q2VAMPOS_SCRIPT`). **Not tested:** the loop device (whether the kernel has one is a Q2 question), a real chroot of the image over a running stock boot, and stopping stock's `mdev`.

## Size and time

Measured on the MacBook Pro (M-series, `tools/mac/q2env`'s native arm64 image, the Colima VM's 9 CPUs and 11 GB shared with three other workers), 2026-11-27:

| | |
| --- | --- |
| `vampos.squashfs` | 6,569,984 bytes (6.3 MiB), 9% of mtd2's 72 MiB (`ROOTFS_ROOM`) |
| Composed, with `/vendor` | 8,650,752 bytes (8.3 MiB) |
| Largest files | `q2player` 3.2 MB (FFmpeg and mbedTLS inside), `libc.so.6` 2.0 MB, `sqlite3` (the shell) 1.9 MB, `libsqlite3` 1.2 MB, `libasound` 1.2 MB, `busybox` 1.1 MB, the two Inter faces 0.8 MB, `q2ui` 0.2 MB |
| A full build from an empty build directory, downloads cached | 929 s and 909 s (two runs, `make clean` before each, `BR2_JLEVEL=4`, niced), most of it the toolchain (binutils, GCC twice, glibc) |
| A build after a commit, toolchain and FFmpeg built | 8 s to a few minutes, by what changed |
| The build directory, `/home/gray/.cache/q2-os/out` | 6.7 GB; downloads in `~/.cache/q2-os/dl` 0.95 GB |
| Under the emulator: `rcS`'s first line to both services started | about 2.5 s (QEMU user mode on the VM; a Q2 is not this) |

**Reproducible, as far as shown:** after `dirclean` of every target package (q2player, its FFmpeg and mbedTLS, q2ui and its headers, the fonts, BusyBox, SQLite, alsa-lib) and a rebuild at the same commit, `vampos.squashfs` was the same bytes (SHA-256 `4d8527be...0570935` both times, commit `f2786f2f`), and composing it twice gave the same image (`54b3121b...7daf76`). Not shown: that a second toolchain build gives the same bytes (the two full builds were of different commits), or that another machine does. The image's bytes follow the commit as well as the sources (`/etc/vamp-release` names it).

## What is missing

In the order it is needed:

1. **A first run on a Q2**, from the card ([below](#first-boot-on-a-q2)): whether the kernel has loop devices, whether `q2ui` and `q2player` run on its kernel, then a boot from flash: whether the modules load under this init, whether the kernel has what it assumes (a tmpfs `/dev` and mdev, UBI, LZO squashfs), whether the watchdog runs.
2. **The rescue page**, and a first-key Allow card in `q2ui`: until then a Q2 that trusts no computer is reached only by putting a key in the card's `adb_keys` ([adb](#adb)).
3. The rest of `q2playd` and `q2libd` ([q2playd](playd.md#what-is-missing), [q2libd](libd.md#what-is-missing)): the rest of server songs, duplicates, playlists.
4. **The rest of q2sysd** ([Needs a Q2](sysd.md#needs-a-q2) first): Standby, the screen's idle timeout, the jack and the DAC's power and volume, Charge Limit, heat, USB modes.
5. **The rest of the installer** ([q2pkgd](pkgd.md)): its first slice checks, composes and stages a signed release (the image's own `mksquashfs` composes the build machine's bytes under QEMU); the download, the automatic hand-off to the recovery and packages are not built.
6. q2ui's icons: it looks for `oled_*_w.png` in `--icons`, stock's art, which the image does not have; the pages draw without them.
7. Bluetooth, Wi-Fi, USB modes, the MCU's firmware load, the splash.

## Needs a device

Everything here has been run only under the emulator and on this machine. What only a Q2 can prove, in one list (each item's detail is in the doc named):

| Area | What only a Q2 proves | Doc |
| --- | --- | --- |
| Boot | The kernel's command line (`init=`), devtmpfs or not, LZO squashfs, UBI and UBIFS as this init assumes; every module loading under it; the watchdog's state with nothing opening it | [What boots](#what-boots), [First boot](#first-boot-on-a-q2) |
| Card boot | Loop devices in the kernel; the chroot over a running stock boot; stopping stock's `mdev` | [Card boot](#card-boot) |
| Recovery | Shanling's recovery restoring this unit, and what its `recovery-update/` folder must hold | [First boot](#first-boot-on-a-q2) |
| adb | The USB controller in device mode with nothing of stock's having set it; the gadget on the real UDC | [adb](#adb) |
| Screen | `bl_power` turning the panel's light off; the brightness scale; the double tap waking it; q2ui's 24-bit panel mode | [q2sysd](sysd.md#needs-a-q2) |
| Power | Which devices carry the power button and whether a press arrives twice; `poweroff` reaching the PMIC; the PMIC's own long-press time | [q2sysd](sysd.md#needs-a-q2) |
| Battery | The CW2015's and the charger's files under this init | [q2sysd](sysd.md#needs-a-q2) |
| Charging | `PE22` driven through the kernel's GPIO class, and the charger stopping | [q2sysd](sysd.md#needs-a-q2) |
| USB storage | The mass-storage gadget on the real controller, the card seen by a computer and mounted again | [q2sysd](sysd.md#needs-a-q2) |
| Standby | `mem` suspend with Vamp OS's services, its wake sources, its current | [q2sysd](sysd.md#needs-a-q2) |
| Sound | The DAC powered and in PCM mode with no `demo`; the driver's 0-100 volume law in decibels | [q2playd](playd.md#the-volume) |
| Wi-Fi | Association, DHCP and DNS; the radio's power when `wlan0` is down | [q2netd](netd.md#needs-a-q2) |
| Library | A real card's first scan time and memory under FFmpeg | [q2libd](libd.md#what-is-missing) |
| Updates | Shanling's recovery's folder contract, and that it removes the folder after flashing (inferred from installs); `mksquashfs`'s time and memory on the Q2 | [q2pkgd](pkgd.md#needs-a-q2) |
| Hot reload | `kill -HUP`'s in-place restart (QEMU's `/proc/self/exe` is QEMU) | [Hot reload](#hot-reload) |

## First boot on a Q2

Not done: the Q2 belongs to the main session, and **nothing goes on it until Shanling's recovery has been tried on that unit.** The card boot comes first; a flash install only after it works. Each step stops the plan if it fails.

1. **Prove the recovery on this unit, with stock.** Put Shanling's own recovery package's `recovery-update/` on a card, hold Previous and power on with Centre ([README](../README.md#restoring-stock-firmware)), and check the unit comes back on stock with `/mnt/data` (settings, pairings) intact. This is the way back from every later step.
2. **Back up `/mnt/data`**, on whatever firmware the unit then runs, over adb: `adb shell 'tar -C /mnt -cf - data' > q2-data-$(date +%Y%m%d).tar` and `tar tf` it to see `config.ini`, `database.db` and `bluetooth/` in it; keep it off the Q2.
3. **Read the unit, change nothing**: `cat /proc/cmdline /proc/mtd /proc/filesystems /proc/version /proc/devices`, `ls /dev/loop* /sys/module/loop 2>&1`, `cat /sys/class/watchdog/watchdog0/state`, `/proc/config.gz` if present. No `loop` in `/proc/devices` and no `/dev/loop0` means the card boot cannot work (it would log "loop mount of the image failed" and boot today's Vamp): stop and report.
4. **Install a `--dev` build of today's Vamp from this branch** (`tools/ci/rebuild.py --dev`, the usual update path). Boot it with no `.vamp/os` on the card: it must behave as before, and `/mnt/data/vamp/handoff.log` must not exist.
5. **Lay out the card**: on the computer, `tools/vampos.py build`, then `compose --zip ZIP`, then `card --image composed.squashfs --card CARD` (or `adb push` the image and its `.sha256` to `/mnt/mmc/.vamp/os/` and `adb shell touch /mnt/mmc/.vamp/os/boot-once`). Check with `adb shell 'cd /mnt/mmc/.vamp/os && sha256sum -c vampos.squashfs.sha256 && ls'`.
6. **Try a fallback first**: make the `.sha256` wrong (`adb shell 'echo 0 > /mnt/mmc/.vamp/os/vampos.squashfs.sha256'`), `touch boot-once`, `adb reboot`: today's Vamp, `boot-once` gone, and a "SHA-256 is" line in `handoff.log`. Put the right `.sha256` back.
7. **The card boot**: `touch boot-once`, `adb reboot`. Expect stock's boot and splash, then a black screen and `q2ui`'s Home (if `/mnt/data/database.db` is there). Over adb, which stays up: `cat /mnt/data/vamp/handoff.log /mnt/data/vamp/boot.log /mnt/data/vamp/kernel.txt`, `ps | grep -E 'q2ui|q2player|runsv'`, `ls /tmp/vamp-os-root/dev/fb0 /tmp/vamp-os-root/dev/input`. Songs lists the card as `q2libd` read it. Play one: whether it is heard is the DAC's power question, and how loud the driver's law ([q2playd: The volume](playd.md#the-volume)).
8. **Come back**: `adb reboot`, or hold Power 3 s (q2sysd's power off, which in a card boot reaches stock's `init`), or, if neither works, hold Power for the PMIC's cut-off. Today's Vamp must start (`boot-once` was spent), and `handoff.log` holds the hand-off's lines. If it does not, step 1's card restores stock, and step 2's tar has the data.
9. **Only after repeated card boots of that same image work, and with the owner present with a computer and Shanling's firmware ZIP, a boot from flash** (Shanling's recovery removes `recovery-update/` once it has flashed, so until Vamp OS's rcS has put the rescue copy back the keys have nothing to restore: [q2pkgd: The rescue copy](pkgd.md#the-rescue-copy)): `recovery-update/` with the unit's own `xImage` (from the same ZIP) and the composed image as `rootfs.squashfs`, laid out as Shanling's recovery package's folder is (compare names and sizes first: whether the recovery needs `xImage`, and what else it reads), then hold Previous and power on with Centre. Expect a black screen for the kernel's few seconds, then `q2ui`. Read what happened over Vamp OS's own adb ([adb](#adb): the cable, or Wi-Fi if `wlan0` came up), or, if it is not reachable, by restoring stock or today's Vamp from step 1's card: `cat /mnt/data/vamp/boot.log modules.log dmesg.log kernel.txt` (the last boot's log is kept as `boot.log.1`).

A second Q2 kept on a known image would halve the cost of each mistake ([M5](vamp-os.md#m5-the-vamp-os-base-and-the-installer)).
