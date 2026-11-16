# coda-rc: the car remote protocol

coda-rc lets a phone app (the q2-auto-remote Android Auto app) show and control what a Q2 plays, while the Q2 itself plays into the car over Bluetooth or AUX. The phone browses the Q2's library, starts songs and lists, sees Now Playing with its cover and position, and sends transport commands. It never carries audio.

It replaces nothing: Shanling's SyncLink is still in demo, kept off ([internals](internals.md#synclink)). coda-rc exists because SyncLink has no authentication at all (any host that reaches TCP 1751 is "logged in" and can read the Wi-Fi password) and because its Bluetooth Classic mode diverts the AVRCP link the car depends on; the research is in `q2-emu/design/remote-protocol-research.md`.

The message set is [`remote/coda-rc.proto`](../remote/coda-rc.proto). This page is everything else: the frame, the transports, pairing and authentication, versioning and errors. The daemon that serves it is `patch/coda_rc*.c` ([internals: Car remote](internals.md#car-remote)); a reference client is `tools/coda_rc_client.py`.

## Principles

- **Off by default.** Nothing listens until the user turns on Settings > Car Remote. Turning it off stops the daemon.
- **Authenticated on every transport.** Nothing but the handshake is answered before a session proves it holds a key that the user approved on the Q2's own screen. After that, every frame in both directions is encrypted and authenticated.
- **Nothing sensitive in the schema.** No network credentials, no settings writes, no file paths in or out, no firmware update, no serial number or MAC address. A paired phone can do what a passenger holding the Q2 could do with its wheel, and read what its screen would show.
- **The device's state lives in the daemon, not in demo.** demo's payload adds only a small shim (start a list, show the pairing code, list the paired phones).

## Frames

Every transport carries the same byte stream of frames, in order, in each direction:

```
u16 len | u8 type | u8 flags | u16 req_id | payload
```

- All integers are big-endian.
- `len` counts the bytes after itself: 4 + the payload's length (+ 16 when sealed). A frame is at most 2 + 4 + 8192 + 16 bytes; the plain payload is at most 8192 bytes. Either side closes the session on a `len` below 4 or above 8212.
- `type` is the message (the table at the top of `coda-rc.proto`). Types are never renumbered.
- `flags`: bit 0 is `SEALED`. Every frame after `AuthResp` has it, and no frame before. The other bits are 0; a frame with any other bit set closes the session.
- `req_id` ties a response to its request. The client picks it (1..65535, any scheme; incrementing is fine); the device copies it into the answer. Pushes from the device have `req_id` 0.
- `payload` is the message in protobuf's binary encoding. An empty message is an empty payload.

A request gets exactly one answer frame: its response type or `Error`. Requests may be pipelined; the device answers them in the order it received them.

### Sealing

After `AuthResp`, each frame's payload is replaced by its ChaCha20-Poly1305 (RFC 8439) ciphertext followed by the 16-byte tag:

- key: `kc` for client-to-device frames, `kd` for device-to-client (see [Keys](#keys));
- nonce: 4 zero bytes, then that direction's frame counter as a 64-bit little-endian integer. Each direction's counter starts at 0 with the first sealed frame and goes up by one per frame;
- associated data: the frame's 6 header bytes, as sent (so `len`, `type`, `flags` and `req_id` are authenticated, not hidden).

A frame that fails to open closes the session: a forged, replayed, dropped or reordered frame cannot be told apart from an attack, and the transports never reorder.

## Transports

### Bluetooth LE (primary)

The Q2 is the GATT peripheral, the phone the central. One primary service with three characteristics:

| What | UUID | Properties |
|---|---|---|
| Service | `b67cefa7-194f-4988-8820-4974e36704c3` | primary, advertised |
| `control` | `1666f4ad-18d6-4692-b723-b5ae83ce5ef3` | write, write without response: client-to-device bytes |
| `event` | `5645283b-18bb-419e-b338-f089cfc8dced` | notify: device-to-client bytes |
| `info` | `c7f73d6b-93b1-46b5-8892-1ba59bdb7513` | read: `coda-rc 1` (ASCII), so a scanner can tell the version before connecting |

- The frame stream is split across ATT PDUs with no extra header: each write to `control` and each notification from `event` carries the next bytes of the stream, at most ATT_MTU - 3 of them, and the receiver reassembles by `len`. ATT writes and notifications on one connection arrive in order, which is all the stream needs. Clients should request an MTU of 247 and use write without response for throughput; the device accepts either.
- The device's notifications start once the client enables them on `event` (its CCCD); the client should do that before writing `Hello`.
- One session per connection; disconnecting ends it. The device serves one BLE connection at a time.
- The characteristics require an encrypted link (`encrypt-write`, `encrypt-notify` in BlueZ terms), so the phone bonds with the Q2 (LE Secure Connections, Just Works) before the first write. That bond only keeps strangers' radios from talking to the GATT server cheaply; it is not what authenticates the phone, because Just Works has no user check. The app-level pairing below is.
- Advertising carries the service UUID and the local name `Q2 Muse`. It runs only while Car Remote is on.

Implemented with BlueZ's D-Bus API (`org.bluez.GattManager1`, `org.bluez.LEAdvertisingManager1`) in `patch/coda_rc_ble.c`. The emulator has no Bluetooth, so this transport is proven only on a real Q2: see [Testing on a Q2](#testing-on-a-q2).

### TCP (fallback, and the emulator)

TCP port 7720, the frame stream as is. For the emulator, and for a car whose hotspot the Q2 and phone share, the research's fallback when LE cannot run beside A2DP. It is not advertised (no mDNS, no multicast responder); the client is given the address. The daemon accepts at most 4 TCP sessions at once and drops one that has not sent `Hello` within 10 seconds or `Auth` within 150 seconds. With Smart Wi-Fi on, Car Remote on keeps the Q2's Wi-Fi on, as AirPlay does ([internals: Smart Wi-Fi](internals.md#smart-wi-fi)), so the phone finds the Q2 on the network whenever the car starts.

Being on the same network grants nothing: TCP sessions authenticate and seal exactly as BLE ones do, and a passive listener sees frame sizes and types only.

## Pairing and authentication

```
client                                  device
Hello{nonce_c, peer_id?}         ->
                                 <-     HelloResp{nonce_d, paired}
-- not paired yet --
PairStart{client_name}           ->     shows a 6-digit code on its screen
                                 <-     PairChallenge{device_point}
  (the user types the code into the phone)
PairFinish{client_point, client_confirm} ->
                                 <-     PairResult{peer_id, device_confirm}
-- paired --
Auth{client_proof}               ->
                                 <-     AuthResp{device_proof}
-- sealed from here --
```

The session id `sid` is `nonce_c || nonce_d` (32 bytes). Both nonces are fresh random bytes per session.

### Pairing: a PAKE on the 6-digit code

A 6-digit code has only a million values, so it must never be used where it can be guessed offline: anyone recording a pairing over TCP could try every code against a hash. coda-rc uses the code in a balanced PAKE in the style of CPace (draft-irtf-cfrg-cpace), on X25519, so that a recording reveals nothing about the code and an active attacker gets exactly one guess per code shown:

1. On `PairStart` the device picks a code uniformly from 000000-999999 and shows it (Settings' style dialog, [Pairing on the Q2](#pairing-on-the-q2)). A phone whose connection drops while its code is up gets the same code on its next `PairStart` (same client name, within the code's 120 s), so the user typing it is not sent back to a new one. The new session draws its own scalar, and the generator takes its own `sid`, so a session that sends no `PairFinish` learns nothing of the code; a wrong `client_confirm` still ends it (step 6).
2. Both sides compute the generator `G = elligator2(SHA-512("coda-rc pake v1" || code || sid)[0:32])`, where `code` is the 6 ASCII digits and `elligator2` is Monocypher 4's `crypto_elligator_map` (the top two bits of the input are ignored).
3. Each picks a random 32-byte scalar: the device sends `device_point = X25519(b, G)`, the client `client_point = X25519(a, G)`. X25519 clamps scalars, so both points lie in the prime-order subgroup.
4. Both compute `K = X25519(own scalar, other's point)`. An all-zero `K` (a low-order point was sent) fails the pairing.
5. `prk = HMAC-SHA-512(K, "coda-rc pair v1" || sid || client_point || device_point)`
6. `client_confirm = HMAC-SHA-512(prk, "client confirm")[0:32]`; the device checks it in constant time. If it is wrong, the pairing ends (`Error{PAIRING_FAILED}`), the code is wiped, and the next `PairStart` gets a new one.
7. The device makes a random 8-byte `peer_id`, keeps `ltk = HMAC-SHA-512(prk, "long-term key")[0:32]` for it with the client's name, and answers `device_confirm = HMAC-SHA-512(prk, "device confirm" || peer_id)[0:32]`. The client keeps `peer_id` and `ltk` only if `device_confirm` matches.

Limits, all on the device: one pairing at a time device-wide (`BUSY` to a second phone while the first is connected; once it has gone, a second phone's `PairStart` ends its code and gets a new one); a code lives 120 s from when it is first shown, across the phone's reconnections; 3 wrong codes in a row lock pairing for 5 minutes (`PAIRING_LOCKED`); at most 8 paired phones (`PAIRING_FULL`); closing the dialog on the Q2 cancels (`Error{PAIRING_CANCELLED}`, pushed with `req_id` 0). A stranger can at worst make a code appear on a Q2 whose Car Remote is on, which the user can close.

The confirm keys are bound to the transcript (`sid` and both points), so a pairing cannot be spliced from two sessions.

### Authentication: every session

`Auth.client_proof = HMAC-SHA-512(ltk, "coda-rc auth v1" || "client" || sid)[0:32]`, checked in constant time; then `AuthResp.device_proof = HMAC-SHA-512(ltk, "coda-rc auth v1" || "device" || sid)[0:32]`, which the client checks, so each side knows the other holds `ltk`. A wrong proof gets `Error{UNAUTHORIZED}` and the session closes.

### Keys

`HMAC-SHA-512(ltk, "coda-rc session v1" || sid)` gives 64 bytes: the first 32 are `kc` (client to device), the last 32 `kd` (device to client). They exist only for that session.

Labels are ASCII with no terminator; `||` is concatenation. `tools/coda_rc_crypto.py` is the reference, and `test/unit/test_coda_rc.py` checks it and the daemon against each other and against Monocypher.

### Where keys live

- **Device:** `/mnt/data/coda-rc/peers`, mode 0600 in a 0700 directory: up to 8 fixed 76-byte records, `peer_id[8] | ltk[32] | name[32] (NUL-padded UTF-8) | created u32 LE (Unix seconds)`. `/mnt/data/coda-rc/id` holds the 8-byte device id. Neither is on the card, so a card lent or sold carries no keys. Settings > Car Remote lists the names and forgets a phone by removing its record; the daemon notices within a second and closes that phone's sessions.
- **Phone:** `peer_id` and `ltk` per device id, in the app's private storage (Android Keystore-wrapped).

## Versioning

- `Hello.proto_version` is the highest version the client speaks; `HelloResp.proto_version` is the one the session uses, and `min_version` the oldest the device still speaks. A client older than `min_version` gets `Error{VERSION}` and the session closes. This is version 1.
- Within a version, change is additive: new frame types, new fields, new enum values. Readers skip unknown fields and treat unknown enum values as their zero value. An unknown frame type gets `Error{UNSUPPORTED}` and the session goes on.
- `HelloResp.caps` says what this Q2 serves (`Capability` in the proto): a Q2 without the play shim, or whose demo is not answering it, leaves `CAP_PLAY` clear, and the app grays out Play rather than failing.
- Commands added within version 1 come with a capability bit, so a phone and a Q2 of different ages still work together: `SetMode` (`CAP_MODES`), `Rate` with `NowPlaying.hearts` and `rateable` (`CAP_RATE`), `PlayQueueItem` (`CAP_QUEUE_JUMP`), and `Play.resume` with the playlists' `ACTION_RESUME` (`CAP_RESUME`). An older Q2 leaves the bits clear and answers the frames `UNSUPPORTED`; the app then hides the car's Shuffle, Repeat and heart buttons, leaves the queue untappable and shows no Resume row, as before. An older app never sends them and ignores the new fields. All four need demo's shim, as Play does: they are clear when it is not answering. See [Modes, rating, the queue and Resume](#modes-rating-the-queue-and-resume).
- A change that cannot be additive (a new frame layout, a new key schedule) is a new `proto_version`. The labels carry the version (`"coda-rc pake v1"`), so a v2 key schedule never collides with v1.

## Modes, rating, the queue and Resume

Each goes to demo's shim as one line in `/tmp/coda-rc.cmd`, as Transport does, and is acked once the Q2 has done it; the State push that follows shows the result.

- **SetMode** sets shuffle or repeat where the Q2 Muse keeps them. Normally that is Now Playing's Shuffle and Repeat buttons (stock's play mode), so the buttons show the phone's change and NowPlaying shows theirs. While the Play/Pause hold menu's extended play runs (one of its shuffles or repeats chosen), it is that menu's Shuffle and Repeat rows (`playback_set`); the Q2 Muse has more modes there than the protocol names, so a SetMode keeps the nearest one: shuffle on keeps a shuffle by album, Off keeps Play One Song or Play One Album (else Play All), One is Repeat Song, All keeps Repeat Album (else Repeat All). `UNAVAILABLE` when there is no queue to apply it to.
- **Rate** gives the playing song 0 to 5 hearts as Now Playing's heart and its picker do ([Ratings](internals.md#ratings)): 5 puts it in Favorites, 1 to 4 keep that many hearts, 0 clears both. It names the song (`track_id`), so a song that changed meanwhile answers `NOT_FOUND`; a card app's streaming song is named by its server song's id ([A streaming song](#a-streaming-song)); a radio stream (`rateable` false) answers `UNAVAILABLE`.
- **PlayQueueItem** plays the queue's song at an index of `GetQueue`'s list, as a row of the Q2's View Queue does: the playing song plays on, another starts unless Headphones required forbids it (`UNAVAILABLE`). It names the row's song too, and a queue that changed since the phone fetched it answers `NOT_FOUND`.
- **Play with `resume`** carries on a list where it was left. The daemon remembers, for each list a phone played (not shuffled), the song it was on, in `/mnt/data/coda-rc/places` (the 16 most recent lists). If the Q2's queue is still that list's, Resume is Play; otherwise the list starts again from that song, and the Q2's Resume Songs takes a long song back to its place. A list never played from a phone starts at its first song. The app offers it as the playlists' Resume row (`ACTION_RESUME`), as Symfonium does.

There is no song radio to start: the Q2's Internet Radio plays stations, not songs like the one playing.

## A streaming song

A card app's song played without an offline copy plays from its download, `/mnt/mmc/.<app>/stream/<hash>/original/0/<title> [<sid>].<suffix>`, which the library has no record of. `NowPlaying` then carries the catalogue's server song for the `<sid>` in the name's last brackets (`servermeta.sid`, joined to `servertable` by `fileurl`): its `track_id` (`song:c<id>`), title, artist, album, its length while the player reports none, and its album's `art_id` (`s<album>`). `GetCover` for a server album with no `covers/<album>.jpg` answers the picture embedded in a download of one of its songs, the one the Q2 Muse shows. A stream not in the catalogue keeps what the Q2 Muse reports. It is `rateable`, and `Rate` takes its `song:c<id>`: the Q2 Muse rates the download as its server song, as its own heart does.

## Errors

`Error{code, message}` answers a request that failed; codes are `ErrorCode` in the proto. `message` is English for logs, never for display. The device closes the session, after sending the error when it can, on:

- any malformed frame (bad `len`, flags, or a sealed frame that does not open);
- a handshake out of order (`PairFinish` before `PairChallenge`, a second `Hello`);
- `Hello` without a 16-byte nonce, or with no version in common;
- `Auth` with a wrong proof, or before the session is paired.

Everything else (`NOT_FOUND`, `BAD_REQUEST` on a field, `UNAVAILABLE`, `BUSY`, the pairing errors) leaves the session open.

## Pairing on the Q2

When a phone sends `PairStart`, the Q2 asks on its pairing card over whatever is on screen: a phone glyph, the title "Pair with Pixel 8?" (the phone's name, scrolling when long), "Enter this code in Q2 Muse Remote", the code large as two groups of three, and one row, Cancel, with the pairing's two minutes as a bar along its foot. Return is Cancel. It closes by itself when the phone finishes, fails or gives up, with a toast: "Paired with Pixel 8" or "Pairing failed".

Settings > Car Remote: the first row is the switch (Off by default); under it, after a gap, each paired phone by name, its value Connected while it has an authenticated session and Not connected otherwise; Center on one asks `Forget "Pixel 8"?` on the confirmation card ("It has to pair again to control the Q2."), Cancel first, then Forget in red. With no phones paired the page shows the empty state, "No Paired Phones", under the switch. The page follows the daemon while it is open: a phone paired, forgotten, connecting or going shows within half a second, without leaving the page (while the Forget card is up, at the first poll after it closes).

## Testing on a Q2

The emulator proves the protocol, the daemon, the data and the TCP transport end to end (`test/coda_rc_e2e.py`). It has no Bluetooth, so the following can only be proven on a real Q2, with a `--dev` build and `tools/q2remote.py` for a shell and logs:

1. **Daemon and adapter.** Turn Car Remote on. `q2remote.py sh 'ps; cat /tmp/coda-rc.log'`: coda-rc runs at nice 19, and its log shows `ble: registered` (the GATT application and advertisement accepted by bluetoothd) or the D-Bus error that refused them. If bluetoothd is too old or lacks `GattManager1`, the log says so and TCP still works.
2. **Advertising.** On the phone, nRF Connect: scan, see `Q2` with service `b67cefa7-...`. Turn Car Remote off: the advertisement goes within a second.
3. **Bond and GATT.** Connect from nRF Connect: it bonds (Just Works), discovers the three characteristics, reads `info` as `coda-rc 1`. Enable notifications on `event`, write a `Hello` frame by hand (`tools/coda_rc_client.py frame hello` prints its hex): a `HelloResp` arrives as notifications.
4. **A full session over BLE** with the app (or a central running `coda_rc_client.py`'s logic): pair (the code appears on the Q2), browse, play a list, watch State pushes for 10 minutes. Note dropped frames (the session closes on any) and cover fetch times.
5. **LE while A2DP streams (the critical one, research test 2).** With the Q2 playing to the car or a Bluetooth speaker over A2DP, run step 4 with continuous browsing and cover fetches for 10 minutes. Pass: no audible dropouts, and the LE link stays up. Repeat with AAC and SBC. If this fails, use TCP over the car's hotspot.
6. **AVRCP untouched.** With the car connected (A2DP + AVRCP: steering-wheel buttons and the car's track display working), connect and use the app over LE. Pass: the car's buttons and metadata keep working throughout (coda-rc never touches Classic profiles; SyncLink's `"SyncLink Mode | disconnect"` line must not appear in `logread`/demo's log).
7. **Cover latency.** Time a cold `GetCover` for a 96 px thumbnail and a `Browse` of 50 items, over LE and TCP.
8. **Forget.** Forget the phone in Settings > Car Remote while it is connected: its session closes within a second and its next `Hello` reports `paired: false`.
9. **Off means off.** Car Remote off: `netstat -ltn` shows nothing on 7720, and no advertisement is seen.
