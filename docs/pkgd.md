# q2pkgd

VampOS's update service ([roadmap: Installer and updates](vamp-os.md#installer-and-updates)): the installer's steps 1 to 4 and the staging half of 5, as commands a person runs, with the card's rescue copy kept throughout; and the [download](#q2pkgd-download) of a release from the feed into the card's place for the next [card boot](vamp-os-base.md#card-boot). The expected-hash table for more than one stock version and Settings' Software Update page (`q2ui`'s, which would run `download` and say its answer) are later slices.

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
| `q2pkgd confirm` | The same, and `pending` removed. VampOS runs it once it has been up a minute (`/etc/sv/q2pkgd-confirm`); with nothing pending it changes nothing |

**The recovery consumes the folder.** After each of the owner's Vamp installs of 2026-11-27 (stock's updater: `update.tar` to the card, `mtd6` marked, reboot, the recovery flashing) `/mnt/mmc/recovery-update/` was gone, and present again once staged anew: Shanling's recovery removes it after it has flashed (inferred from the folder's absence after several installs; not traced in the recovery's code). So after a flash of VampOS the card has **no recovery at all** until VampOS's rcS has run `restore`: an image that cannot boot as far as its modules and the card leaves the keys with nothing to flash, and the way back is then the owner at a computer with Shanling's ZIP (a card reader, or stock's USB recovery). Hence:

- A flash install of VampOS is done **only after repeated successful card boots** of that same image ([vamp-os-base: Card boot](vamp-os-base.md#card-boot)), which write nothing to flash.
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
3. Each vendor file is found in `--rootfs` (default `/`, the running system) with its SHA-256: at its stock path (a Q2 on stock or today's Vamp), else at its place under `/vendor` (a Q2 on VampOS). A file with another hash stops it by name: an unsupported stock version, or a damaged file.
4. The image is copied and `/vendor` appended by `mksquashfs` with the flags `tools/vampos.py compose` uses: one thread, owners root, `epoch` on every file and as the filesystem's time.
5. The result's SHA-256 must be one `release.txt` lists: then the image about to be installed is byte for byte the one that was built and tested.
6. It is renamed into the card's `.vamp/stage/recovery-update/` as `rootfs.squashfs`; `recovery-update/` is not touched ([The rescue copy](#the-rescue-copy)).

## `q2pkgd download`

`q2pkgd download [--feed URL] [--tag TAG] [--ca FILE] [--timeout S] [--rootfs DIR] [--card DIR]` fetches a release and puts its composed image where the next card boot reads it: the card's `.vamp/os/` ([vamp-os-base: Card boot](vamp-os-base.md#card-boot)). It is Network Update's shape ([internals: Network Update](internals.md#network-update)) for VampOS's release, and like it trusts nothing it has not checked:

1. **The tag.** `--tag TAG`, else a GET of `FEED/releases/latest`, not followed: the tag is what follows the last `/` of its redirect's `Location` (`.../releases/tag/TAG`), 1 to 31 of `A-Za-z0-9._-`. Anything else is "no release at FEED". `FEED` is a build setting (`pkgd/Makefile`, default `https://github.com/312-dev/q2-coda`); `--feed` another.
2. **The signature first.** `releases/download/TAG/release.sig` (exactly 64 bytes; a 404 is "release not signed") and `release.txt`, each following GitHub's redirect to its asset host, into `.vamp/download/` on the card, and checked as `stage`'s step 1 checks them. `release.txt`'s own `tag` must be TAG, so a release cannot be served under another tag's name.
3. **Whether to fetch the image.** A card whose `vampos.squashfs.sha256` names one of the release's composed hashes has it already: nothing more is fetched (exit 0). Without `--tag`, a tag not newer than the tag in the card's `.vamp/os/release.txt` (compared as versions: `v1.10.0` after `v1.9.0`) is refused, so a held-back feed never puts an older release on the card. The card must have twice the image's size and 16 MB free (the download and the composed copy, beside the card's current image).
4. **The image**, `vampos.squashfs`, onto the card: its `Content-Length` must be `release.txt`'s size before a byte is written, a transfer with nothing for `--timeout` seconds (30) ends, a body shorter than its length fails, and the file is flushed and `fsync`ed.
5. **Staged as `stage` stages it**, on the card's copy (so the bytes checked are the bytes on the card): the signature again, the image's SHA-512, this Q2's vendor files, the compose, the composed hash.
6. **Into `.vamp/os/`**: the composed image renamed over `vampos.squashfs` (a card boot running from the old one keeps its open copy), then `vampos.squashfs.sha256` (`sha256sum`'s line, as `q2vampos` reads it) and `release.txt`, each written beside and renamed, then the folder `fsync`ed. Between the image's rename and its `.sha256`'s, `q2vampos` sees a mismatch and boots today's Vamp.

The switch (`boot-once`, `boot-always`) is left as it is: the owner's choice of when to boot it. Any failure leaves `.vamp/os/` byte for byte as it was and `.vamp/download/` gone; a download killed part way leaves `.vamp/download/`, which the next one clears first. One at a time: a second, while one runs, is refused (`.vamp/download.lock`, `flock`). TLS is mbedTLS (the image's `q2-mbedtls`), verified against `/etc/scrobble-ca.pem` (`q2scrobd`'s package installs it; it holds GitHub's and its asset host's roots). Where VampOS releases are published is the owner's to choose: a release is `tools/vampos.py release`'s three files uploaded under those names to a tag of `FEED`, and q2-coda's latest release, today's Vamp, carries none, so `download` with the default feed says "release not signed" until one does.

**Out of scope, not built:** anything past the card. `download` never runs `install`, never writes `recovery-update/` and never marks `mtd6`: VampOS reaches a Q2 by card boot only ([Needs a Q2](#needs-a-q2)).

## Tests

`test/pkgd.py BUILD --zip ZIP` makes a release from BUILD's image with a key made for the run, builds `q2pkgd` for this machine with that key's public half, and stages it from a stock root filesystem unpacked from the ZIP and from the same files under VampOS's `/vendor`: both give `rootfs.squashfs` byte for byte the image `tools/vampos.py compose` gives, the work folder is gone after, and the card's `recovery-update/` (stand-ins for stock's two files) is untouched. It then runs **the image's own `mksquashfs`** (MIPS, under QEMU) with the same flags on the same inputs: the same bytes, so a Q2 composes what the build machine composed, the expectation the roadmap marked inferred and first to test (measured here under QEMU; a Q2's own run is the last step). The rescue copy: an install refused when the rescue folder cannot be written and on a card with no `recovery-update/`, each leaving it as it was; an install keeping the copy whole in `.vamp/rescue/` and putting the staged image beside the rescue's `xImage`; a second install while pending keeping the first copy; after `recovery-update/` is removed (as the recovery does), `restore` putting it back with the install still pending; `confirm` clearing it and then changing nothing. The boot test checks rcS's `restore`: with an install pending and `recovery-update/` gone, the rescue copy is back before the framebuffer and the services, and `--perturb rescue-late` (rcS without it) fails that. Refusals, each with nothing staged and `recovery-update/` untouched: another key's signature, `release.txt` changed, the image changed, a composed hash that is not the result, a vendor file changed (named). **Download**, against a local feed shaped as GitHub's (HTTPS with a CA made for the run, `releases/latest` a 302 to the tag, each asset a 302 to another port): the latest release's composed image lands as the card's `.vamp/os/vampos.squashfs` with its `.sha256` and `release.txt`, the switch untouched, nothing staged for the recovery, its work folder gone; a second run fetches no image (already there); an older latest is refused. Refusals, each leaving `.vamp/os/` as it was: another key's signature, a release signed for another tag, one too large for the card's free space, one with no signature (none of them fetching the image), the image altered, a byte short of its length, cut off part way, redirected to `http://`, stalled (`--timeout 2`), a latest that is no redirect, a tag that is not plain, a server the CA did not sign, a vendor file changed, a download already running; and a killed download's leftovers cleared by the next. Fourteen copies of `q2pkgd.c` each fail it: the signature checked only by stage (the image fetched first), no tag check, no already-there check, no older-tag refusal, no free-space check, no length check, a short body taken, an `http://` redirect followed, no lock, no read timeout, any tag taken from `latest`, the server's certificate unchecked, a `boot-once` made, the work folder kept after a failure. Copies of `q2pkgd.c` that skip the signature, the composed check or the vendor hash, stage into `recovery-update/`, install without keeping the rescue copy, or confirm without putting it back each fail it (`Q2PKGD_SOURCE`). The test first found the image's `mksquashfs` built without LZO, which refuses to append to an LZO image: `BR2_PACKAGE_SQUASHFS_LZO` is set since.

## Needs a Q2

**Not to be run on the owner's Q2 yet:** `q2pkgd install` (and any `mtd6` mark) writes flash through a recovery that has not been seen to rescue that unit: the key combo booted it normally on 2026-11-27, `recovery-update/` untouched, and U-Boot's strings show only the `ota:kernel2` mark path ([vamp-os-base: First boot](vamp-os-base.md#first-boot-on-a-q2)). VampOS is tried there by card boot only until a rescue path has worked on it.


`download` on a Q2 (a card boot with Wi-Fi up): `adb shell q2pkgd download --tag TAG` against a published release, then `cat /mnt/mmc/.vamp/os/vampos.squashfs.sha256`, `sha256sum /mnt/mmc/.vamp/os/vampos.squashfs` (the same), and that the running card boot is unharmed by the rename over its loop-mounted image (`ls /tmp/vamp-os-root`, a song plays); then a power cycle boots the new image (`cat /mnt/data/vamp/handoff.log`). Its time for an 18 MB image over the Q2's Wi-Fi, the compose's time and memory with the player playing, and the card's free space are measured there.

The recovery's folder: whether Shanling's recovery needs `xImage` beside `rootfs.squashfs` and reads anything else (step 3 of [First boot](vamp-os-base.md#first-boot-on-a-q2)); `mksquashfs`'s time and memory on 128 MB with the player stopped; the card's free space for the copy.
