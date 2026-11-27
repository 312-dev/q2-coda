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
| `tools/vampos.py` | `build`, `compose`, `vendor-manifest`, `qemu` (its docstring). Not `tools/os.py`: a module named `os` in `tools/` is imported in place of Python's own by a script there run with `-S` |
| `test/os_image.py` | The checks below |

**Pinned.** Buildroot 2026.02.3, the long-term line, by SHA-256 (the one in buildroot.org's signed `.sign`). Every package Buildroot fetches is checked against its `.hash` file; ours carry the hashes `tools/player.py` (FFmpeg 7.1.5), `tools/thirdparty.py` (mbedTLS 3.6.8) and `tools/ui.py` (stb_truetype, stb_image) pin, and `test/os_image.py` fails when the two differ. Vamp's own sources are not read from the working tree: `build` exports `os/`, `tools/`, `player/`, `patch/` and `assets/fonts` at `HEAD`, and `ui/` and `patch/` at `Q2UI_COMMIT` (a commit of `feat/render-engine`, read-only here), and `manifest.json` names both commits. `--worktree` builds the working tree instead and marks the commit `-dirty`. The configure lines of FFmpeg and the mbedTLS settings come from `tools/player.py` and `tools/thirdparty.py` themselves (`os/support/pins.py`), so they have one definition.

## The toolchain

Buildroot's own: GCC 14.3, glibc 2.42, binutils 2.44, kernel headers 5.10 (the Q2 runs 5.10.21, so glibc assumes no system call it lacks), and 64-bit `time_t` across the image (`BR2_TIME_BITS_64`). The ABI is stock's, fixed by the hardware and every card app: MIPS32R2 little-endian, o32, hard float with a 64-bit FPU, NaN 2008 and abs 2008, `ld-linux-mipsn8.so.1` ([third-party: Toolchain](third-party.md#toolchain)).

Two departures from [the roadmap's toolchain plan](vamp-os.md#toolchain-and-libc), with reasons:

- **Buildroot's toolchain from the start, not Codescape first.** The roadmap's first step keeps stock's Codescape 2020.06 so the image can run `demo` and its libraries during the transition. This image runs no `demo`, so nothing needs glibc 2.28 itself, and Codescape is an x86-64 program the Mac's native image does not run. glibc keeps its old symbol versions, so a card app built against 2.28 should still load (inferred from glibc's compatibility rule; not tried).
- **One patch to Buildroot.** Buildroot ties MIPS32R2 to legacy NaNs and offers NaN 2008 only on R5 and R6. `0001-arch-mips-offer-nan-2008-on-generic-mips32r2.patch` lets the generic R2 variant choose it; `tools/vampos.py` applies it to the unpacked tree. GCC is then configured `--with-arch=mips32r2 --with-nan=2008 --with-fp-32=64`, and `-mabs=2008` is `BR2_TARGET_OPTIMIZATION`.

## What boots

BusyBox `init` (`/sbin/init`, and `/linuxrc` for a kernel line that names it) reads [`/etc/inittab`](../os/board/q2/overlay/etc/inittab): `/etc/init.d/rcS` once, then `runsvdir` over `/etc/sv`, restarted if it ends, and a login on `ttyS3` where the port exists, as stock has. [`rcS`](../os/board/q2/overlay/etc/init.d/rcS) in order:

1. `/proc`, `/sys`, a tmpfs `/dev` with `null` and `console`, `devpts`, `/dev/shm`, `/tmp`, `/run`, as stock's inittab makes them (stock's kernel may not have devtmpfs: stock mounts a tmpfs there, so this image does too).
2. `mdev -s`, then `mdev -d`, its daemon, so the devices the modules make get their nodes. [`/etc/mdev.conf`](../os/board/q2/overlay/etc/mdev.conf) loads no module (no `modprobe` in the image) and mounts the card on `/mnt/mmc` when it comes.
3. `/mnt/data`: the UBI volume on the partition `/proc/mtd` names `userdata`, attached as `ubi1` as stock's `mount_ubifs.sh` does, mounted `-o sync`. **Never erased or formatted:** stock's script runs `flash_erase` on the partition when the attach or the mount fails; this image has no tool that could (`test/os_image.py` checks), and on any failure `/mnt/data` is a tmpfs for that boot and the boot log says why.
4. The modules: Shanling's `driver_default_init_script.sh`, unchanged, from `/module_driver` (a link to `/vendor/module_driver`), as stock's `S11module_driver_default` runs it. It is 24 `insmod` lines with their parameters, run by this image's BusyBox. Then each `.ko` there is looked for in `/proc/modules` and the ones missing are logged.
5. A wait of up to 5 s for `/dev/fb0`, and the input devices with their names.

Then init starts `runsvdir`, which starts `q2player` and `q2ui` ([`/etc/sv`](../os/board/q2/overlay/etc/sv)) and starts each again when it ends; a `finish` script waits 10 s after a failure, so a service that cannot run yet does not spin. Each step writes a line to `/run/vamp/boot.log`, and once `/mnt/data` is mounted also to `/mnt/data/vamp/boot.log` (the last boot's kept as `boot.log.1`), with `dmesg.log`, `modules.log` (the module script's output) and `kernel.txt` (`/proc/cmdline`, `/proc/modules`) beside it. Nothing of Shanling's userspace runs: no program of theirs is in the image, and the vendor scripts are shell lines run by BusyBox.

| Service | Runs | What it waits for |
| --- | --- | --- |
| `q2player` | `/usr/bin/q2player`, built as every Vamp image's (Car Audio's output on) against this image's alsa-lib, FFmpeg 7.1.5 and mbedTLS | its server on `/tmp/sock_play`, `q2playd`, which does not exist yet: without it the player ends after 5 s and is started again |
| `q2ui` | `/usr/bin/q2ui --fonts /usr/share/vamp/fonts --icons /usr/share/vamp/icons` | `/mnt/data/database.db`, stock's library, which a Q2 that ran stock has; without it `q2ui` ends at once ("cannot read") |

## The vendor set

The files of Shanling's the image needs at run time: the 24 modules, their 25 scripts, the radio's 4 firmware and table files and the MCU's firmware, 54 files. [`os/vendor/V1.36.tsv`](../os/vendor/V1.36.tsv) names each by its stock path, SHA-256 and mode (made by `tools/vampos.py vendor-manifest ZIP`); the hashes are facts about the files, and the files are never committed or shipped ([study hygiene](vamp-os.md#study-hygiene)). `vampos.squashfs` has no `/vendor`; `tools/vampos.py compose` copies the image and appends one with `mksquashfs` (one thread, owners root, the base image's time on every file), from either source:

- `--zip ZIP`: the user's own stock firmware download, checked against the manifest's ZIP hash; for tests and a first install from stock with a computer.
- `--rootfs DIR`: an unpacked root filesystem, which on a Q2 is its own `/`: the installer's step 2 ([How an image is made on the device](vamp-os.md#how-an-image-is-made-on-the-device)). A file whose hash is not the manifest's stops the compose with its name.

`/vendor/module_driver`, `/vendor/firmware` and `/vendor/mcu` hold them; `/module_driver` and `/firmware` are links there, the paths the scripts, the Wi-Fi driver and `rtk_hciattach` name. `rtk_hciattach` and `cmd_mcu` are not in the set yet: nothing here starts Bluetooth or the MCU.

## The emulator, and what it cannot stand in for

`test/os_image.py BUILD --zip ZIP` composes the image and boots it under QEMU's user-mode MIPS emulator, as [the emulator](building.md#emulator-pool) runs stock: BusyBox `init` as PID 1 of a `bwrap` sandbox (`--as-pid-1`), every MIPS program started through the host's `binfmt_misc` entry for mipsel, which finds a static `qemu-mipsel` bound at its `-binfmt-P` path. That is `tools/vampos.py qemu`'s: `qemu-user-static` 8.2.2 for arm64 from Ubuntu 24.04 (Launchpad's copy of `qemu-user-static_8.2.2+ds-0ubuntu1.20_arm64.deb`, by SHA-256), unpacked once under `~/.cache/q2-os/qemu`; on an x86-64 machine pass `--qemu ~/q2-emu/qemu-mipsel-static`.

| Stands in | Does not stand in |
| --- | --- |
| Every program of the image, its dynamic linker and libraries, the ABI (QEMU picks a P5600 for NaN 2008 code) | The kernel: there is none. Shanling's kernel, its configuration, its system calls and their errors are the VM's Linux 6.x seen through QEMU |
| `init`, `rcS`, `runsvdir`, `runsv`, the services' scripts, in the image's order | The modules: `insmod`, `mount`, `mknod`, `mdev`, `ubiattach` and `dmesg` are stand-ins that record their arguments. No module is loaded, so whether each loads into Shanling's kernel under this init is a Q2 question |
| The framebuffer as a file of two 320x375 pages, the keys and the wheel as FIFOs (no ioctl answers: `q2ui` takes the file's size) | `/proc/mtd`, UBI, the card, the panel's timing, vsync, the DAC, ALSA (no sound card: `q2player` never opens one here) |
| `q2player`'s server, by the test on `/tmp/sock_play` | Power, suspend, the watchdog, the PMIC's keys |

The boot passes when the record shows, in this order: `/proc`, the tmpfs `/dev`, `mdev -s` and `mdev -d`, the data step (here: no `userdata` in `/proc/mtd`, so a tmpfs), the 24 `insmod` calls in the order and with the parameters Shanling's scripts give (read from the vendor files themselves), `modules: done`, `fb0: present` and `rcS: done`, all before either service starts; then `q2player` connects to the test's socket and `q2ui` draws into the framebuffer.

## Size and time

RESULTS

## What is missing

In the order it is needed:

1. **A first boot on a Q2** ([below](#first-boot-on-a-q2)): whether the modules load, whether the kernel has what this init assumes (a tmpfs `/dev` and mdev, UBI, LZO squashfs), whether the watchdog runs.
2. **A way back in without stock**: adbd over USB and Wi-Fi (the roadmap's priority, M1), and the rescue page. Until then a Vamp OS Q2 is reached by restoring stock.
3. **q2playd**, so `q2player` has a server and `q2ui` a player, and **q2libd**, so `q2ui` has a library on a Q2 that never ran stock. Until then the UI draws its pages and plays nothing.
4. **q2sysd**: power (the power key does nothing here; whether the PMIC's long press still cuts power is a Q2 question), battery, the jack and the DAC, screen off.
5. **The installer** (`q2pkgd`'s system half) and the expected-hash check of a composed image on the device: `mksquashfs` in the image and the test that a Q2 and the build machine compose the same bytes.
6. q2ui's icons: it looks for `oled_*_w.png` in `--icons`, stock's art, which the image does not have; the pages draw without them.
7. Bluetooth, Wi-Fi, USB modes, the MCU's firmware load, the splash.

## First boot on a Q2

Not done: the physical Q2 belongs to the main session, and **nothing is installed on it until the recovery path has been tried on that unit.** The plan, in order; each step stops the plan if it fails:

1. **Read the unit, change nothing.** Over adb on stock: `cat /proc/cmdline /proc/mtd /proc/filesystems /proc/version`, `ls /dev | head`, `cat /sys/class/watchdog/watchdog0/state` if it exists, `grep -c . /proc/modules`, and `/proc/config.gz` if present. These settle the assumptions above (`init=`, the partition names, squashfs with LZO, ubifs, devtmpfs, the watchdog).
2. **Prove the recovery path on this unit, with stock.** Put Shanling's own recovery package's `recovery-update/` on a card, hold Previous and power on with Centre ([README](../README.md#restoring-stock-firmware)), and check the unit comes back on stock with `/mnt/data` (settings, pairings) intact. This is the way back from every later step; it is tried before anything of ours is flashed.
3. **Compare the folder.** List the recovery package's `recovery-update/` (file names, sizes) against stock's `update.tar`'s: whether the recovery needs `xImage` beside `rootfs.squashfs`, and anything else it reads.
4. **Make the card for Vamp OS**: `tools/vampos.py compose --zip ZIP` on the build machine, then `recovery-update/` with the unit's own `xImage` (from the same ZIP) and the composed image as `rootfs.squashfs`, laid out as step 3 found. A second card keeps the stock recovery folder of step 2.
5. **Boot it.** Expect a black screen (no splash) for the kernel's few seconds, then `q2ui` if `/mnt/data/database.db` is there. No key powers it off (no q2sysd): hold Power for the PMIC's cut-off, or let the battery run down.
6. **Read what happened, whatever happened**: restore stock from the second card (step 2's route), then over adb `cat /mnt/data/vamp/boot.log`, `modules.log`, `dmesg.log`, `kernel.txt`. The boot log has a line per step with the time since boot, the modules that did not load, the framebuffer and the input devices, the watchdog's state.
7. If it boot-loops (the watchdog, or `init` failing), the same route restores stock; the boot log of each attempt is kept (`boot.log`, `boot.log.1`) when `/mnt/data` was mounted.

A second Q2 kept on a known image would halve the cost of each mistake ([M5](vamp-os.md#m5-the-vamp-os-base-and-the-installer)).
