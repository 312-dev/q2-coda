# LocalSend

Design for replacing Wi-Fi Transfer with [LocalSend](https://localsend.org), the open AirDrop-like protocol
(v2.2, [spec](https://github.com/localsend/protocol)). A phone or computer with the free LocalSend app sends
files to the Q2, and the Q2 sends files (Battery History's Share Log, the logs folder) to them. Approved by the user on
2026-11-16 ("replace with LocalSend yes"); the screens as the mock-up the user approved on 2026-11-18. How the demo
side works: [internals](internals.md#localsend).

## What it replaces

Stock's Wi-Fi File Transfer: a web page served by thttpd 2.29 on port 8888 while Settings > Network > Wi-Fi File
Transfer was open, to which Vamp had added a PIN, an idle stop and its own CGIs. None of that is in Vamp:
stock's thttpd binary, `/etc/thttpd.conf` and `/usr/share/web` stay in the rootfs as shipped (the repack keeps every
stock inode), but nothing starts them: `switch_wifitransport` keeps a hook that refuses to start the server, so no
stock path can bring port 8888 back. Network's third row (`netservice_wifitrans`) is **LocalSend** and opens its
page (`smart_wifi.c` `ringnav_netservice_row`).

## The protocol, as the reference app speaks it

Read from LocalSend 1.18.2 (2026-08-21), whose networking is now the Rust core (`packages/core`), and checked
against its CLI (`LocalSend-CLI-1.18.2-linux-x86-64`, the test peer). Where the written spec and the app differ,
the app wins:

- **Discovery.** UDP multicast to 224.0.0.167:53317: a JSON announcement (`alias`, `version` "2.2",
  `deviceModel`, `deviceType`, `fingerprint`, `port`, `protocol` "https", `download`, `announce` true), sent three
  times (100 ms, 500 ms, 2 s). A device that hears one answers with `POST /api/localsend/v2/register` over HTTPS to
  the announcer's address and port. The Rust core sends no UDP reply, so **the Q2 needs its HTTPS server running to
  be found at all**, even only to send. The Q2 still accepts a UDP reply (`announce` false) from older apps.
- **Identity.** A self-signed certificate; the fingerprint is the uppercase hex SHA-256 of its DER. A sender pins
  the receiver's certificate to the fingerprint it learned in discovery. The app makes RSA-2048, `CN=LocalSend
  User`, valid 1975 to 4096, and checks a peer certificate's dates.
- **Client certificates.** The reference server **requires** a client certificate whenever its web share is off
  (`server/mod.rs` `mandatory_client_auth`) and takes the sender's identity from it. So the Q2 presents its own
  certificate on every request it makes.
- **Receive.** `POST /prepare-upload` with the sender's info and the files (id, `fileName`, `size`, `fileType`,
  optional `sha256`). The receiver holds the request open while its user decides, then answers 200 with a
  `sessionId` and a token per accepted file, 403 when declined, 409 when another session is running. Then one
  `POST /upload?sessionId&fileId&token` per file, the body the bytes (422 on a `sha256` mismatch), and
  `POST /cancel?sessionId` from either side's cancel. A folder sent arrives as files whose `fileName` carries the
  relative path (`Album/CD1/01.flac`). The CLI sends each upload's body **chunked** (`Transfer-Encoding: chunked`,
  no `Content-Length`), so the server's parser takes both.
- **Send.** The same three calls, made by the Q2 to the peer.
- Not used: the PIN (the Q2 asks on its screen instead), the download API (`prepare-download`, served to browsers
  over plain HTTP), v3.

## Shape

A daemon, **`q2send`** (`/usr/bin/q2send`, from `patch/localsend*.c`, built by `compile_helper` like `coda-rc`), and
a small demo side. TLS, sockets and blocking file I/O stay out of demo's UI thread and its 128 KB scratch; the
daemon runs only while needed and dies cleanly.

- **`patch/localsend_core.c`**: the protocol with no sockets or files: JSON in and out, the HTTP request parser and
  router, the session (files, tokens, sizes, progress), the file-name check, the announcement and register bodies.
  Built for this machine with a host platform for `test/unit/test_localsend.py`, as `coda_rc_core.c` is.
- **`patch/localsend.c`**: the platform: the multicast socket, the TLS server on 53317 and the TLS client, files on
  the card, and the IPC files. Links `libc`, `libssl.so.1.1` and `libcrypto.so.1.1` from the rootfs (OpenSSL 1.1.1w,
  [third-party](third-party.md#openssl)); declarations by hand in `localsend_sys.h`, as `coda_rc_sys.h` does, since
  there is no MIPS sysroot.
- **`patch/localsend_ui.c`** in demo: starts and stops the daemon, draws the page, the Accept card, the progress and
  the Share sheet, and passes the user's answers. IPC is `/tmp/q2send.*` files, as Car Remote's shim does:
  `state` (devices, the transfer and its progress, written by the daemon), `ask` (an incoming request waiting for an
  answer), `answer` and `cmd` (written by demo: accept, decline, cancel, send these paths to that fingerprint).

### TLS

OpenSSL 1.1.1w is already in the rootfs with its server side, so TLS costs no flash. The identity is made once,
on the first start, and kept in `/mnt/data/q2send/` (`key.pem` mode 0600, `cert.pem`), so phones remember the Q2
across restarts; Reset Settings removes it. Validity is written as strings (`19750101000000Z` to
`40960101000000Z`, `ASN1_TIME_set_string`), so the Q2's unset clock and 32-bit `time_t` don't matter. Peer dates
are not checked (the Q2's clock may be unset); a peer is trusted by its fingerprint, as the app does.

Key type: **P-256 ECDSA**, made in milliseconds where RSA-2048 (the app's own) could take seconds on the Q2's
core with OpenSSL built without assembly. Checked on 2026-11-18 against the reference CLI 1.18.2 with a Python
stand-in for the Q2 (scratch `probe/peer.py`): with a P-256 identity, and again with RSA-2048, the CLI heard the
announcement, registered over HTTPS, sent a file through prepare-upload and upload, and accepted a file sent to it
with the identity as client certificate; a request without one was refused in the handshake (TLS 1.3
`certificate_required`). The phone apps of the same release use the same Rust core; an older app is untested.
ChaCha20-Poly1305 is preferred over AES-GCM, as the CPU has no AES instructions.

### When it listens

Off by default. Nothing listens until the LocalSend page opens; leaving the page stops the daemon unless a
transfer is running, which finishes in the background (Return on the progress page leaves it running; the
result is a toast on whatever page is up). The Share sheet starts it too, for as long as it is open. Smart Wi-Fi holds Wi-Fi on while either
is up, as it held Wi-Fi Transfer's page. Ponytail: no "always receivable" switch in the first version.

### Receiving

- **Trusted devices** (approved by the user on 2026-11-18, the adb prompt's pattern): the Accept card has an
  **Always Accept** switch, off by default (the approved "Always accept from this device" is wider than the card;
  USB debugging's is "Always Allow"). Accepted with it on, the sender's certificate
  fingerprint goes on the list in `/mnt/data/q2send/trusted` beside the identity, and its later requests skip the
  card, with a "Receiving from <name>" pill so a receive is never silent. Trust is the client certificate's
  fingerprint alone, never the name or a fingerprint a request's body claims; a sender with no client certificate
  is always asked. The space, name and one-session checks still apply. The LocalSend page has a Trusted Devices
  row: its page lists them, Center removes one, Remove All in red.
- Every incoming request asks on the Q2: the [confirmation card](oled-design.md#confirmation-card) with the
  sender's name and device glyph, what it sends ("12 files · 48.2 MB"), Accept and Decline, Decline after 60 s.
  Return declines. A second sender while one transfers gets 409, no card.
- Files land in **`/mnt/mmc/Received/`**, a folder sent keeps its own folders under it. A name clash gets " (2)".
  Each name is checked as q2cgi checked paths: no empty or dot-leading component (`..`, hidden files), no control
  characters, no `/` at the start, at most 255 bytes a component; refused names fail the request with 400 before
  anything is written.
- Refused before the card: the card in use over USB, the card not mounted, or the total over the free space less
  a 16 MB margin (the sender sees 403 and the Q2 a toast saying why).
- A file is written to `name.part` and renamed when its last byte is in and its `sha256` (when sent) matches; a
  cancel or a dropped connection removes the `.part`.
- Songs that arrive are added to the library: once the daemon has stopped and nothing plays, demo opens stock's
  Update Local Music on the whole library (`library_quiet`, as a card app's sync waits). ponytail: stock's scan
  takes no single folder without a new hook into its scan thread; scoping it to `Received` would make a large
  library's update seconds instead of minutes.

### Sending

- **Share** opens a sheet listing the devices found (an announcement burst as it opens, the list filling as they
  register), each with its glyph and system ("macOS", "Android"). Center sends. On the LocalSend page itself,
  Center on a device asks what to send it (Logs, Battery Log). The peer's user then accepts on their device: the Q2
  shows "Waiting for MacBook Air…" with Cancel until the answer, then progress, then a toast.
- Export Log on Battery History becomes **Share Log** and opens the sheet with `battery-log.txt` (written to
  `/tmp`, not the card).
- **Send Logs** on the LocalSend page sends the unified log and its companions: a fresh `q2guard` snapshot of
  `/mnt/mmc/.q2muse/logs` (`q2-log.0` to `.3`, `q2-heat`, `q2-bt`, `q2-btev`, about 1.5 MB), plus the battery log, and each card
  app's own logs as they are now (`ls_logs`): its log in RAM, `/tmp/q2-<id>.log`, and its log on the card,
  `.<id>/log.txt` (q2-subsonic's, where a failed download is written, its tokens and passwords redacted by the app),
  each with its `.1`. This is the one
  the user needs today: logs off the Q2 onto a work Mac with USB storage blocked.

## Costs

Measured where it says so; the rest are estimates.

| | Cost |
| --- | --- |
| Flash | `q2send` 43,524 bytes (measured, 2026-11-18); the demo side 15,296 bytes of payload code (measured: the payload's 1,217,256 bytes to 1,232,552). Less q2cgi (20 KB), `q2cgi-root` and the web patch. TLS: 0 (rootfs OpenSSL) |
| Payload scratch | 568 bytes (measured: 16,388 free to 15,820): the pages' state; the device list is read into the stack each poll |
| RAM, only while running | `q2send` about 1.5 to 2.5 MB RSS: libssl and libcrypto's pages are shared with demo, which maps libcrypto already; 64 KB file buffer per upload, at most two at once |
| CPU | one TLS handshake per request; ChaCha20 at Wi-Fi speed is expected to leave the player its headroom (to be measured on the device) |
| Attack surface | port 53317 TCP and UDP, only while the page or sheet is open; nothing written without the user's Accept. Less than today: thttpd and a set-uid CGI are gone |

## Status

Built and on the branch `feat/localsend`: the protocol core and `q2send` (`patch/localsend_core.c`,
`patch/localsend.c`), the demo side (`patch/localsend_ui.c`: the page, the share sheet, Send to, the transfer page,
the Accept card, Trusted Devices), Network's row, Battery History's Share Log, and Wi-Fi File Transfer's removal.
Tests: `test/unit/test_localsend.py` (the core against the reference app's texts), `test/localsend_e2e.py CLI`
(`q2send` beside the reference CLI 1.18.2: discovery both ways, accept, decline, hold, trust, sending, identity kept),
`test/fw/test_localsend_ui.py` and `test/fw/test_battery.py`. Measured in the emulator against the reference CLI and
a Python peer (2026-11-18): found both ways, logs sent and accepted, two files (43 MB) and three (9 MB) received
after Accept, a trusted sender received with no card, a name clash saved as " (2)".
