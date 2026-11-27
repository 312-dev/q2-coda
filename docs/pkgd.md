# q2pkgd

Vamp OS's update service ([roadmap: Installer and updates](vamp-os.md#installer-and-updates)), its first slice: the installer's steps 1 to 4 and the staging half of 5, as one command a person runs. Packages (apps, themes), a download from the release feed, the expected-hash table for more than one stock version, and Settings' Software Update page are later slices.

| What | Where |
| --- | --- |
| Source | `pkgd/q2pkgd.c` (with Monocypher, `patch/monocypher`, for Ed25519 and SHA-512), `pkgd/Makefile` |
| In the image | `/usr/bin/q2pkgd` with the release key's public half compiled in (`os/package/q2pkgd`, from `assets/signing/q2-coda-release.pub`), and Buildroot's `mksquashfs` with LZO |
| Making a release | `tools/vampos.py release --image vampos.squashfs --zip ZIP --tag TAG --out DIR` |
| Tests | `test/pkgd.py BUILD --zip ZIP` |

**It never writes flash.** The last step stages the composed image on the card, where Shanling's recovery looks for it; the flash is written by Shanling's recovery when the owner starts it with the Q2's keys (hold Previous, power on with Centre: [README](../README.md#restoring-stock-firmware)). Marking `mtd6` and rebooting into the recovery, the roadmap's automatic hand-off, is not built, and is not to be built until the recovery has been tried on a unit ([First boot](vamp-os-base.md#first-boot-on-a-q2)).

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

## `q2pkgd stage RELEASE_DIR [--rootfs DIR] [--out DIR]`

In order, stopping at the first failure with its reason, and leaving nothing in `--out` (default `/mnt/mmc/recovery-update`) when it fails:

1. `release.sig` checks against `release.txt` with the compiled-in key.
2. `vampos.squashfs` has `release.txt`'s SHA-512 and size.
3. Each vendor file is found in `--rootfs` (default `/`, the running system) with its SHA-256: at its stock path (a Q2 on stock or today's Vamp), else at its place under `/vendor` (a Q2 on Vamp OS). A file with another hash stops it by name: an unsupported stock version, or a damaged file.
4. The image is copied and `/vendor` appended by `mksquashfs` with the flags `tools/vampos.py compose` uses: one thread, owners root, `epoch` on every file and as the filesystem's time.
5. The result's SHA-256 must be one `release.txt` lists: then the image about to be installed is byte for byte the one that was built and tested.
6. It is renamed into `--out` as `rootfs.squashfs`.

## Tests

`test/pkgd.py BUILD --zip ZIP` makes a release from BUILD's image with a key made for the run, builds `q2pkgd` for this machine with that key's public half, and stages it from a stock root filesystem unpacked from the ZIP and from the same files under Vamp OS's `/vendor`: both give `rootfs.squashfs` byte for byte the image `tools/vampos.py compose` gives, and the work folder is gone after. It then runs **the image's own `mksquashfs`** (MIPS, under QEMU) with the same flags on the same inputs: the same bytes, so a Q2 composes what the build machine composed, the expectation the roadmap marked inferred and first to test (measured here under QEMU; a Q2's own run is the last step). Refusals, each with nothing staged: another key's signature, `release.txt` changed, the image changed, a composed hash that is not the result, a vendor file changed (named). Copies of `q2pkgd.c` that skip the signature, the composed check or the vendor hash each fail it (`Q2PKGD_SOURCE`). The test first found the image's `mksquashfs` built without LZO, which refuses to append to an LZO image: `BR2_PACKAGE_SQUASHFS_LZO` is set since.

## Needs a Q2

The recovery's folder: whether Shanling's recovery needs `xImage` beside `rootfs.squashfs` and reads anything else (step 3 of [First boot](vamp-os-base.md#first-boot-on-a-q2)); `mksquashfs`'s time and memory on 128 MB with the player stopped; the card's free space for the copy.
