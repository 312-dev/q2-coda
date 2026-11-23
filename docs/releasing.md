# Standard release procedure

To prepare a release, bump `VERSION` in `tools/build.py` (and set `BETA` there to the beta number for a pre-release, 0 for the release itself) and add a `- **<version>**` entry to the top of `docs/changelog.md`, then commit both, since packaging refuses uncommitted build inputs. Packaging turns that entry into the release notes (the bold version heading, then one bullet a sentence) and fails without it, or when it isn't the newest entry. Keep the proprietary stock ZIP local. Use the Python environment with `requirements.txt`
installed for these commands:

```sh
python3 tools/release.py package 'Q2 Firmware V1.36.zip' --out /tmp/q2-release
python3 test/release.py
# Sign update.tar with the q2-coda release key (below, Signing a release).
python3 tools/release.py digest /tmp/q2-release
python3 tools/release.py sign /tmp/q2-release --signature 'BASE64'
# Create or update the release with the validated update tar and its signature, and verify remote bytes.
python3 tools/release.py upload /tmp/q2-release
# Same, then publish after verification.
python3 tools/release.py upload /tmp/q2-release --publish
```

Packaging also rebuilds the user guide from `docs/manual/guide.html` (needs Chromium and `pdftotext`), and upload attaches it to every release, since the README links `releases/latest/download/Q2-Muse-User-Guide.pdf`.

Packaging requires a fresh output directory, builds the update twice, runs the shared MIPS
suite and asset checks on the first build, and compares the two builds' update.tar bytes. The checks
are twenty suites, each a process of its own, all started at once: they take as long as the longest,
the MIPS suite (about 3.5 minutes), and about 3.4 GB of memory between them. It writes
update.tar, the manifest, SHA256SUMS, release notes and source revision/hash, and refuses a build whose card apps (`bundled_apps`) are not the `BUNDLED_APPS` pins; `release.json` and the notes name each carried app's zip and SHA-256. Before it builds, it runs `python3 -I docs/rebasing/sitenotes.py ZIP --check` and stops when `tools/stock_sites.tsv`, the record of each patched stock site ([rebasing](rebasing.md)), is not current: run `python3 -I docs/rebasing/sitenotes.py 'Q2 Firmware V1.36.zip'`, read its diff (rows for new sites, or for sites whose use moved; a site it lists in no named function needs a name in `tools/stock_map.tsv`) and commit the table. It refuses a build whose release key isn't `assets/signing/q2-coda-release.pub` and a `sources.list` that key didn't sign. A single direct build is never a release input. Upload sends the release notes as packaged (to fix them afterwards, use `gh release edit --notes-file`), re-checks them, the packaged bytes and the source hash
and refuses stale, missing or changed artifacts and published releases. An upload/download
failure leaves the release unpublished; rerun upload to repair the draft. Packaging uses no
GitHub credentials or network.

## Signing a release

Software Update's download installs only an `update.tar` whose `update.tar.sig` the q2-coda release key signed, and the App Store reads only a `sources.list` it signed ([Release signatures](internals.md#release-signatures)). The key is Ed25519. Its public half is `assets/signing/q2-coda-release.pub`, which every build compiles in; its private half lives only in the password vault, reached through a secret broker that runs a command with the key's PEM in an environment variable. Nothing here writes the private key to disk.

The broker runs commands under `/bin/sh` as another user who can't write beside the release, and hands back what they print. So signing takes three steps, and only the middle one has the key:

1. `python3 tools/release.py digest /tmp/q2-release` reads `update.tar` and prints one line, a command for the broker. It signs the message `q2-coda-update-v1 TAG SHA512` (the release's tag and the tar's SHA-512): the message is written to a `mktemp` file (openssl's `-rawin` needs a file it can size; the message is not secret), and the key goes to openssl through a pipe. For release 1.8.3 it reads, with the real digest in place of `<sha512>`:

   ```sh
   bash -c 'set -o pipefail; m=$(mktemp) && printf "%s\n" "q2-coda-update-v1 1.8.3 <sha512>" > "$m" && openssl pkeyutl -sign -rawin -inkey <(printf "%s\n" "$Q2_SIGNING_KEY") -in "$m" | base64 -w0; r=$?; rm -f "$m"; exit $r'
   ```

   `--env NAME` names another variable than `Q2_SIGNING_KEY`, the one the broker puts the PEM in.

2. Run that line through the broker with the release key's PEM in that variable. It prints the signature in base64 (88 characters) and nothing else.
3. `python3 tools/release.py sign /tmp/q2-release --signature 'BASE64'` checks the signature against `assets/signing/q2-coda-release.pub` and writes `update.tar.sig` (64 bytes) beside `update.tar`, with its hash in `release.json`. It refuses a signature that doesn't check.

With the PEM in `Q2_SIGNING_KEY` in the shell that runs it instead, `python3 tools/release.py sign /tmp/q2-release` (no `--signature`) signs in one step, the key through a pipe to openssl. Upload refuses a release without a signature that checks, uploads `update.tar.sig` with `update.tar`, and checks both remote copies. A Q2 refuses a release whose signature is missing ("Release not signed: not installed") or doesn't check ("Signature check failed: not installed"), so q2-pod's upstream releases, which carry none, are never installed by a q2-coda Q2.

A changed key means a new `assets/signing/q2-coda-release.pub` in a release signed by the old one: a Q2 trusts only the key it was built with.

## The app list

`sources.list` is the App Store's list, which q2-coda's Q2s fetch from this repository's `main` ([Q2 Shelf](internals.md#q2-shelf)). Each app's line pins the exact release zip a Q2 installs, by its tag, name, size and SHA-256; `serial N` numbers the list; its last line is the release key's signature. A Q2 refuses a list that is unsigned, signed by another key, or numbered below the one it has, and a zip whose SHA-256 is not its pin. Edit it only through `tools/shelf.py`:

```sh
python3 tools/shelf.py pin                      # every app's latest release: its first .zip fetched and hashed
python3 tools/shelf.py pin DiamondBond/q2-rockbox  # or only these
python3 tools/shelf.py digest                   # the broker's command, as for update.tar
python3 tools/shelf.py sign --signature 'BASE64'
python3 tools/shelf.py verify
```

`pin` reads public GitHub releases only. It writes each changed pin, raises the serial and drops the old signature; check what changed before signing (`git diff sources.list`), since the signature vouches for every zip the list names. To add an app, add its `owner/repo Name` line and pin it; to drop one, put `-` before its line (a dropped app needs no pin). An app the firmware carries as builtin (Subsonic; `BUNDLED_APPS` in `tools/build.py`) has no line: Q2s skip one anyway (`shelf_sources`), and its updates are firmware releases. To ship a new version of it, build its release zip, set the new version, zip name, size and SHA-256 in its `BUNDLED_APPS` entry ([building](building.md#bundled-apps)), and release the firmware; nothing here changes. `digest` raises the serial itself when the list was edited after it was signed, and refuses a list a Q2 would skip a line of. `sign` checks the broker's signature before it writes the last line. Commit `sources.list` once `verify` passes: packaging refuses a tree whose list doesn't verify, and Q2s keep the last list they took until the signed one is on `main`.

## Betas, and the release after them

A beta keeps the release's `VERSION` and so its tag. `upload --publish` refuses one (`BETA` is not 0), so a beta goes out by hand, and the flag matters: without `--prerelease` it becomes Latest, which Network Update offers to everyone.

```sh
python3 tools/release.py upload /tmp/q2-release
gh release edit 1.8.0 --draft=false --prerelease
```

Before the next beta, or the release itself, delete the published one together with its tag:

```sh
gh release delete 1.8.0 --yes --cleanup-tag
```

GitHub's web page deletes the release and leaves the tag, and GitHub then keeps that tag on the beta's commit whatever `--target` says. Upload refuses a tag that is on another commit than the packaged one, so a tag left behind stops it there. For the release itself set `BETA = 0`, package into a fresh directory and `upload --publish`.
