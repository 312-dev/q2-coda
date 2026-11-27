# q2pkgd

Vamp OS's update service ([roadmap: Installer and updates](vamp-os.md#installer-and-updates)), its first slice: the installer's steps 1 to 4 and the staging half of 5, as commands a person runs, with the card's rescue copy kept throughout. Packages (apps, themes), a download from the release feed, the expected-hash table for more than one stock version, and Settings' Software Update page are later slices.

| What | Where |
| --- | --- |
| Source | `pkgd/q2pkgd.c` (with Monocypher, `patch/monocypher`, for Ed25519 and SHA-512), `pkgd/Makefile` |
| In the image | `/usr/bin/q2pkgd` with the release key's public half compiled in (`os/package/q2pkgd`, from `assets/signing/q2-coda-release.pub`), and Buildroot's `mksquashfs` with LZO |
| Making a release | `tools/vampos.py release --image vampos.squashfs --zip ZIP --tag TAG --out DIR` |
| Tests | `test/pkgd.py BUILD --zip ZIP` |

**It never writes flash.** The flash is written by Shanling's recovery when the owner starts it with the Q2's keys (hold Previous, power on with Centre: [README](../README.md#restoring-stock-firmware)), a combo that on the owner's Q2 booted normally instead ([Needs a Q2](#needs-a-q2)). Marking `mtd6` and rebooting into the recovery, the roadmap's automatic hand-off, is not built, and is not to be built until the recovery has been tried on a unit ([First boot](vamp-os-base.md#first-boot-on-a-q2)).


It also checks an app's folder and manifest, `q2pkgd app-check DIR`: Vamp API 1's [Packages](platform/packages.md), tested by `test/apps.py`; it installs nothing. And it keeps the packages' users and groups: `app-add DIR`, `app-remove ID`, `accounts` (rcS's, at each boot) and `app-run DIR`, each with `--root DIR` for a test ([Packages: Users and groups](platform/packages.md#users-and-groups), tested by `test/perm.py`). It is the launcher too: `q2pkgd serve`, the image's `q2pkgd` service, starts and stops apps under runit as their users over Q2PKG/1 on `/run/vamp/pkg.sock` ([Packages: Starting and stopping](platform/packages.md#starting-and-stopping-q2pkg1), tested by `test/launch.py` and `test/os_image.py`'s boot).

## The rescue copy

The card's `recovery-update/` holds stock's `xImage` and `rootfs.squashfs` on purpose: the owner cannot reach the card by hand, and Shanling's recovery from it (Previous and Centre) is the only way back if a boot fails; `vamp recovery-stage` (`feat/vamp-cli`) puts it back after every install. So `q2pkgd` never leaves the card without a known-good `recovery-update/` while idle:

| Command | What it does with `recovery-update/` |
| --- | --- |
| `q2pkgd stage RELEASE_DIR` | Nothing: the composed image goes to `.vamp/stage/recovery-update/rootfs.squashfs` |
| `q2pkgd install` | First keeps the rescue copy: `recovery-update/xImage` and `rootfs.squashfs` copied to `.vamp/rescue/`, each written beside its name, read back byte for byte, then renamed; with an install already pending (`.vamp/rescue/pending`), the copy there is kept as it is. Only then the staged image is put in `recovery-update/` (beside the rescue's own `xImage`) and `pending` written. Refused, with `recovery-update/` as it was, when nothing is staged, when the card has no `recovery-update/` to keep, or when the copy cannot be kept |
| `q2pkgd restore` | With an install pending: `.vamp/rescue/`'s files put back in `recovery-update/` (the folder made again if it is gone), unless they are there already; `pending` kept. **rcS runs it as soon as the card is mounted**, right after the modules (the card's controller is one of them), before the framebuffer, the services or anything else of the boot that can fail |
| `q2pkgd confirm` | The same, and `pending` removed. Vamp OS runs it once it has been up a minute (`/etc/sv/q2pkgd-confirm`); with nothing pending it changes nothing |

**The recovery consumes the folder.** After each of the owner's Vamp installs of 2026-11-27 (stock's updater: `update.tar` to the card, `mtd6` marked, reboot, the recovery flashing) `/mnt/mmc/recovery-update/` was gone, and present again once staged anew: Shanling's recovery removes it after it has flashed (inferred from the folder's absence after several installs; not traced in the recovery's code). So after a flash of Vamp OS the card has **no recovery at all** until Vamp OS's rcS has run `restore`: an image that cannot boot as far as its modules and the card leaves the keys with nothing to flash, and the way back is then the owner at a computer with Shanling's ZIP (a card reader, or stock's USB recovery). Hence:

- A flash install of Vamp OS is done **only after repeated successful card boots** of that same image ([vamp-os-base: Card boot](vamp-os-base.md#card-boot)), which write nothing to flash.
- It needs **the owner present, with a computer and Shanling's firmware ZIP** at hand, for the case above.
- Between `install` and the flash, `recovery-update/` holds the new image; between the flash and rcS's `restore`, it holds nothing.

## The release

A folder: `vampos.squashfs`, `release.txt` and `release.sig`.

```text
Q2REL/1
tag v1.0.0
image <SHA-512 of vampos.squashfs> <its size in bytes>
epoch <the image's mkfs time: every appended file's time>
file module_driver/soc_fb.ko <SHA-256> <mode, octal>       one line per vendor file (os/vendor/V1.36.tsv)
composed V1.36 <SHA-256 of the image composed with V1.36's vendor set>
```

`release.sig` is the release key's 64-byte Ed25519 signature of `q2-coda-vampos-v1 TAG SHA512(release.txt)\n`, the shape of today's update signatures (`q2-coda-update-v1`, [internals: Release signatures](internals.md#release-signatures)) under a kind of its own, so no other signed file can pass for a release. The hashes in `release.txt` are facts about Shanling's files; the files themselves are never in a release ([study hygiene](vamp-os.md#study-hygiene)).

## `q2pkgd stage RELEASE_DIR [--rootfs DIR] [--card DIR]`

In order, stopping at the first failure with its reason, and leaving nothing staged when it fails (`--card`, default `/mnt/mmc`):

1. `release.sig` checks against `release.txt` with the compiled-in key.
2. `vampos.squashfs` has `release.txt`'s SHA-512 and size.
3. Each vendor file is found in `--rootfs` (default `/`, the running system) with its SHA-256: at its stock path (a Q2 on stock or today's Vamp), else at its place under `/vendor` (a Q2 on Vamp OS). A file with another hash stops it by name: an unsupported stock version, or a damaged file.
4. The image is copied and `/vendor` appended by `mksquashfs` with the flags `tools/vampos.py compose` uses: one thread, owners root, `epoch` on every file and as the filesystem's time.
5. The result's SHA-256 must be one `release.txt` lists: then the image about to be installed is byte for byte the one that was built and tested.
6. It is renamed into the card's `.vamp/stage/recovery-update/` as `rootfs.squashfs`; `recovery-update/` is not touched ([The rescue copy](#the-rescue-copy)).

## Tests

`test/pkgd.py BUILD --zip ZIP` makes a release from BUILD's image with a key made for the run, builds `q2pkgd` for this machine with that key's public half, and stages it from a stock root filesystem unpacked from the ZIP and from the same files under Vamp OS's `/vendor`: both give `rootfs.squashfs` byte for byte the image `tools/vampos.py compose` gives, the work folder is gone after, and the card's `recovery-update/` (stand-ins for stock's two files) is untouched. It then runs **the image's own `mksquashfs`** (MIPS, under QEMU) with the same flags on the same inputs: the same bytes, so a Q2 composes what the build machine composed, the expectation the roadmap marked inferred and first to test (measured here under QEMU; a Q2's own run is the last step). The rescue copy: an install refused when the rescue folder cannot be written and on a card with no `recovery-update/`, each leaving it as it was; an install keeping the copy whole in `.vamp/rescue/` and putting the staged image beside the rescue's `xImage`; a second install while pending keeping the first copy; after `recovery-update/` is removed (as the recovery does), `restore` putting it back with the install still pending; `confirm` clearing it and then changing nothing. The boot test checks rcS's `restore`: with an install pending and `recovery-update/` gone, the rescue copy is back before the framebuffer and the services, and `--perturb rescue-late` (rcS without it) fails that. Refusals, each with nothing staged and `recovery-update/` untouched: another key's signature, `release.txt` changed, the image changed, a composed hash that is not the result, a vendor file changed (named). Copies of `q2pkgd.c` that skip the signature, the composed check or the vendor hash, stage into `recovery-update/`, install without keeping the rescue copy, or confirm without putting it back each fail it (`Q2PKGD_SOURCE`). The test first found the image's `mksquashfs` built without LZO, which refuses to append to an LZO image: `BR2_PACKAGE_SQUASHFS_LZO` is set since.

## Needs a Q2

**Not to be run on the owner's Q2 yet:** `q2pkgd install` (and any `mtd6` mark) writes flash through a recovery that has not been seen to rescue that unit: the key combo booted it normally on 2026-11-27, `recovery-update/` untouched, and U-Boot's strings show only the `ota:kernel2` mark path ([vamp-os-base: First boot](vamp-os-base.md#first-boot-on-a-q2)). Vamp OS is tried there by card boot only until a rescue path has worked on it.


The recovery's folder: whether Shanling's recovery needs `xImage` beside `rootfs.squashfs` and reads anything else (step 3 of [First boot](vamp-os-base.md#first-boot-on-a-q2)); `mksquashfs`'s time and memory on 128 MB with the player stopped; the card's free space for the copy.
