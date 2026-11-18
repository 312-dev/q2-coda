# LocalSend

Design for replacing Wi-Fi Transfer with [LocalSend](https://localsend.org), the open AirDrop-like protocol
(v2.2, [spec](https://github.com/localsend/protocol)). A phone or computer with the free LocalSend app sends
files to the Q2, and the Q2 sends files (Export Log, the logs folder) to them. Approved by the user on
2026-11-16 ("replace with LocalSend yes"). The screens are drafts until the user approves the mock-up; nothing
here is built yet except where [Status](#status) says so.

## What it replaces

Wi-Fi Transfer is Shanling's web page: thttpd 2.29 on port 8888, started by `switch_wifitransport(1)` while
Settings > Network > Wi-Fi File Transfer is open, with Q2 Muse's PIN, session cookie, idle stop and safe CGIs on
top ([internals: Wi-Fi transfer](internals.md#wi-fi-transfer)). Removing it takes:

| Where | What goes |
| ----- | --------- |
| `patch/wifi_transfer.c`, `patch/cgi_root.c`, `assets/web/q2cgi` | the PIN, the idle stop, the set-uid CGI wrapper, the file-manager script |
| `tools/build.py` | the two `HOOKS` (`switch_wifitransport`, `systemset_wifitransport_page_init`), `patch_web` and `WEB`, `THTTPD_USER`, the q2cgi and CGI inode installs, `CGI_MODE` |
| `patch/system.c` | the `wifi_transfer_poll` call |
| `patch/smart_wifi.c` | `wifitransport_page` and `g_wifitransport_flag` in the Wi-Fi hold; the LocalSend page takes its place |
| `patch/navigation.h` | the `wifi_transfer.c` declarations |
| tests | `test/fw/test_wifi_transfer.py`, `test/fw/test_cgi_root.py`, `test/wifi_transfer_cgi.sh`, the q2cgi checks in `test/build.py`, `tools/scan.py`'s q2cgi rules and `test/unit/test_scan.py` |
| docs | internals' `## Wi-Fi transfer`, third-party.md's thttpd lines and its "What reaches them" row, inefficiencies.md, source-map.md, the manual's Network page |

Stock's thttpd binary, `/etc/thttpd.conf` and `/usr/share/web` stay in the rootfs (the repack keeps every stock
inode), but nothing starts them: `switch_wifitransport` keeps a hook that refuses to start the server, so no
stock path can bring port 8888 back. Network's third row, today "Wi-Fi File Transfer" (`netservice_wifitrans`),
becomes **LocalSend** and opens the new page (`smart_wifi.c` `ringnav_netservice_row` already owns that row's
click).

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
  "Always accept from this device" switch, off by default. Accepted with it on, the sender's certificate
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
- Songs that arrive are added to the library: at the end of the session demo runs the stock scan on
  `/mnt/mmc/Received` (Update Library's code path, scoped to the folder). Ponytail: a whole-library rescan if
  scoping it turns out to need a new hook.

### Sending

- **Share** opens a sheet listing the devices found (an announcement burst as it opens, the list filling as they
  register), each with its glyph and system ("macOS", "Android"). Center sends. On the LocalSend page itself,
  Center on a device asks what to send it (Logs, Battery Log). The peer's user then accepts on their device: the Q2
  shows "Waiting for MacBook Air…" with Cancel until the answer, then progress, then a toast.
- Export Log on Battery History becomes **Share Log** and opens the sheet with `battery-log.txt` (written to
  `/tmp`, not the card).
- **Send Logs** on the LocalSend page sends the unified log and its companions: a fresh `q2guard` snapshot of
  `/mnt/mmc/.q2muse/logs` (`q2-log.0` to `.3`, `q2-heat`, `q2-bt`, about 1 MB), plus the battery log. This is the one
  the user needs today: logs off the Q2 onto a work Mac with USB storage blocked.

## Costs

Measured where it says so; the rest are estimates until the demo side is built.

| | Cost |
| --- | --- |
| Flash | `q2send` 43,524 bytes (measured, 2026-11-18); the demo side about 12 KB of payload (estimate). Less q2cgi (20 KB), `q2cgi-root` and the web patch. TLS: 0 (rootfs OpenSSL) |
| Payload scratch | under 2 KB: the page's device list (8 devices), the transfer's numbers |
| RAM, only while running | `q2send` about 1.5 to 2.5 MB RSS: libssl and libcrypto's pages are shared with demo, which maps libcrypto already; 64 KB file buffer per upload, at most two at once |
| CPU | one TLS handshake per request; ChaCha20 at Wi-Fi speed is expected to leave the player its headroom (to be measured on the device) |
| Attack surface | port 53317 TCP and UDP, only while the page or sheet is open; nothing written without the user's Accept. Less than today: thttpd and a set-uid CGI are gone |

## Status

- Design and mock-up: approved by the user on 2026-11-18 as drawn (`mock_localsend.py`, 14 screens).
- Protocol core and daemon: built. `patch/localsend_core.c` (JSON, info, peers, HTTP heads, chunked bodies, safe
  names, the session; 13.4 KB of MIPS code) and `patch/localsend.c` (`q2send`, 43.5 KB linked, installed at
  `/usr/bin/q2send`). `test/unit/test_localsend.py` checks the core with the texts the reference app sent;
  `test/localsend_e2e.py CLI` runs `q2send` beside the reference CLI 1.18.2 and passes its 19 checks both with a host
  build and with the build's own MIPS program under qemu-mipsel against the rootfs's OpenSSL 1.1.1w: each finds the
  other, a file received after Accept, refused after Decline and while held, two files sent and accepted, one
  declined, the identity kept across a restart. A peer resumes its TLS session on each request, which OpenSSL refuses
  with a client certificate asked for until the server context has a session id context (found by the run: the
  uploads' handshakes failed until it was set).
- Not built yet: the demo side (`patch/localsend_ui.c`), the Network row, Battery History's Share Log, and the
  removal of Wi-Fi Transfer.
