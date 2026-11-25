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
- The device's notifications start once the client enables them on `event` (its CCCD); the client should do that before writing `Hello`. A frame answered before then ends the session.
- One session per connection; disconnecting ends it. The device serves one BLE connection at a time: a second phone's connection is closed at once.
- The device takes a request's frame only once its answer to the one before has left, so a phone that asks for several covers at once gets them one after another, never interleaved: LE shares the radio with A2DP. A phone that writes more than two whole frames ahead of its answers is disconnected. A frame whose `len` is out of range closes the session and the link at once, whatever bytes follow it.
- No bond and no encrypted link: the characteristics are open, and the phone must not bond (`createBond`). The app-level pairing and sealing below are what protect a session, as on TCP; a bond would add nothing they don't (Just Works has no user check) and risks the phone treating the Q2 as an audio device.
- The GATT database also has the GAP service (`0x1800`): Device Name (the Bluetooth name) and Appearance.
- Advertising carries the Flags and the service UUID, and the scan response the Q2's Bluetooth name (Settings > Bluetooth > Name, `Vamp` unless renamed; cut to 29 characters and marked shortened when longer). It runs only while Car Remote is on, and stops while a phone is connected.
- The connection interval is the phone's choice within what the kernel allows: on a peripheral link outside 30-50 ms (its `conn_min_interval` and `conn_max_interval`, 24 and 40 units by default) the Q2's kernel asks the phone for that range when the link comes up. The app keeps Android's balanced priority (30-50 ms) and must not ask for `CONNECTION_PRIORITY_HIGH`, which the Q2 does not refuse.

Implemented in `patch/coda_rc_ble.c` without bluetoothd, whose V1.36 build offers neither `GattManager1` nor `LEAdvertisingManager1` (its adapter registration never makes a GATT database or an advertising manager): the daemon listens on the LE ATT fixed channel itself (an L2CAP `SOCK_SEQPACKET` socket bound to CID 4, LE public, any address), answers ATT (MTU exchange; Find Information; Find By Type Value; Read By Type; Read By Group Type; Read and Read Blob; Write Request and Write Command; notifications), and advertises through the kernel's management socket (`MGMT_OP_ADD_ADVERTISING`, instance 2, connectable and general discoverable), as SyncLink's `libSL.so` does with its own GATT server and instance 1. While SyncLink is on (config.ini `[SYSSET] SYNCLINK` above 0, off unless the user turns it on), libSL holds that channel, so the daemon opens nothing for LE and says so in its log; it looks again every 5 s. While Bluetooth is off there is no `hci0`: the daemon waits for the kernel's Index Added and advertises 2 s after it; nothing advertises meanwhile, and the Q2 is found over Wi-Fi only. The emulator has no Bluetooth, so the radio side is proven only on a real Q2: see [Testing on a Q2](#testing-on-a-q2). `test/unit/test_coda_rc_ble.py` runs the daemon with `--ble-sim DIR` (AF_UNIX `SOCK_SEQPACKET` sockets in place of the L2CAP and management ones) and plays the phone's GATT client and the kernel.

### TCP (fallback, and the emulator)

TCP port 7720, the frame stream as is. For the emulator, for a car whose hotspot the Q2 and phone share (the research's fallback when LE cannot run beside A2DP), and for pairing with Bluetooth off. The Q2 announces it over mDNS ([Discovery](#discovery)), so the Vamp app's Pair a Vamp lists it beside the Vamps it finds by Bluetooth; the address the Q2's Pair a Phone page shows can still be typed in the app's Connection screen. The daemon accepts at most 4 TCP sessions at once and drops one that has not sent `Hello` within 10 seconds or `Auth` within 150 seconds; outside a pairing (a person typing the code) a session may stay short of `Auth` 15 seconds at most in any one state, and with all 4 taken a new connection takes the place of the oldest that has said no more than `Hello`. With Smart Wi-Fi on, Car Remote holds the Q2's Wi-Fi on while a phone could use it: with a link, for 10 minutes after Car Remote started and after a phone was last connected by any way ([internals: Smart Wi-Fi](internals.md#smart-wi-fi)). With no network joined it holds nothing, as the phone in the car comes over Bluetooth LE, which needs no Wi-Fi.

Being on the same network grants nothing: TCP sessions authenticate and seal exactly as BLE ones do, and a passive listener sees frame sizes and types only.

A phone on another network reaches the daemon through `q2-iroh`, the iroh sidecar the Q2 runs while Car Remote is on and Wi-Fi has a link ([remote: Car Remote across networks](remote.md#car-remote-across-networks)): QUIC to the Q2's iroh endpoint, through a relay or a hole-punched direct path, which the sidecar carries to the daemon's loopback port 7721, naming the phone's iroh key first ([below](#across-networks-only-paired-phones)). The frame stream crosses it unchanged, so a session over it pairs, authenticates and seals as any TCP one; the phone learns the endpoint from Pair a Phone's QR code ([Pairing by QR code](#pairing-by-qr-code)) or, once paired, from `GetRemote` (type `0x60`, `CAP_REMOTE`): `RemoteInfo{endpoint_id, relay_url}`, sealed like every request after Auth, `UNAVAILABLE` while the sidecar has not started.

### Across networks: only paired phones

The Q2's endpoint id is no secret (Pair a Phone's QR code shows it, and a phone forgotten on the Q2 still knows it), so `q2-iroh` lets in only the iroh keys of paired phones (`CAP_IROH_ALLOW`):

- **The phone's key** is its iroh endpoint's secret key, 32 bytes made once per install and kept in the app's private storage; the endpoint id is its public half, and iroh's handshake proves it, so it cannot be claimed by anyone else.
- **Registering it.** On every authenticated session, on any transport (Bluetooth, Wi-Fi or iroh), right after `AuthResp`, a phone with a Q2 that has `CAP_IROH_ALLOW` sends `RegisterRemote{endpoint_id}` (type `0x62`, the 32 raw bytes) and gets `Ack`. The Q2 keeps it with that phone's pairing (`/mnt/data/coda-rc/iroh-peers`), in place of any key it registered before. A session that pairs or authenticates through `q2-iroh` registers its key by itself. So a phone paired before this needs no new pairing: it is let in across networks after its next session by Bluetooth or on the Wi-Fi.
- **The list.** The daemon writes the registered keys of the phones still paired to `/tmp/coda-rc.iroh-allow` at its start, on each registration and whenever the paired phones change. `q2-iroh` reads it at each handshake and at each new stream: a key not in it is refused at the handshake (the connection closed with code 1, "not allowed"), and a phone forgotten on the Q2 (Settings > Car Remote > Forget Phone) is refused from its next stream, its session already ended by the daemon.
- **Pairing across networks.** While Pair a Phone shows its code (`/tmp/coda-rc.qr` exists), `q2-iroh` lets in a key it does not know, and the daemon answers it the handshake and the pairing alone (`Hello`, `PairStart`, `PairFinish`, `Auth`; anything else, `Ping` included, gets `UNAUTHORIZED` and the session closes, as it does at any time outside the page). A key that leaves the page's pairing without pairing or authenticating is refused for 10 s at its next connection, then 20 s, doubling to 5 minutes.
- **Versions.** A phone app from before `RegisterRemote` never registers a key (its iroh key was new at each start), so it has no reach across networks on a Q2 with `CAP_IROH_ALLOW`; Bluetooth and the Wi-Fi are as before. A Q2 without `CAP_IROH_ALLOW` answers `RegisterRemote` `UNSUPPORTED`, and its `q2-iroh` lets anyone with its endpoint id reach the handshake.

### Discovery

While Car Remote is on and Wi-Fi is joined, the daemon answers multicast DNS (RFC 6762, IPv4 only, UDP 5353 on `wlan0`) for one DNS-SD service (RFC 6763), `_q2muse._tcp`:

| Name | Type | Value |
|---|---|---|
| `_q2muse._tcp.local` | PTR | `<name>._q2muse._tcp.local` |
| `<name>._q2muse._tcp.local` | SRV | priority 0, weight 0, port 7720, `q2muse-<6 hex>.local` |
| `<name>._q2muse._tcp.local` | TXT | `id=<device id>` `v=1` |
| `q2muse-<6 hex>.local` | A | `wlan0`'s address |
| `_services._dns-sd._udp.local` | PTR | `_q2muse._tcp.local` (service type enumeration) |

- `<name>` is the Q2's Bluetooth name (Settings > Bluetooth > Name, config.ini's `[Q2POD] BTNAME`), `Vamp` unless renamed. The host label is `q2muse-` and the first three bytes of the device id in hex: stock's own hostname, `ingenic`, is every Q2's.
- `id` is `HelloResp.device_id` as 16 lowercase hex digits, the key the app keeps a Q2's pairing under, so the app shows a Q2 once however many addresses it has had. `v` is this page's protocol version. The device id is in every `Hello` anyway; it names a Q2, never its owner.
- Every record has a TTL of 120 s, so a Q2 that leaves without a goodbye drops out of a phone's list within two minutes.
- It sends two announcements a second apart when it starts and whenever the address or the name changes (a goodbye for the old records first), and a goodbye (TTL 0) when the daemon stops (Car Remote off) or Wi-Fi loses its address. Otherwise it only answers: no periodic announcements, and a record is multicast at most once a second. A query whose known answers already hold the PTR with half its TTL left gets no answer (RFC 6762 section 7.1).
- It answers only the names above, only to queries from port 5353, and ignores responses and malformed packets.

The responder is `patch/coda_rc_mdns.c`, part of the daemon's poll loop. Stock's AirPlay receiver, shairport, has an mDNS responder of its own built in, with no way for another program to register with it; each binds 5353 with `SO_REUSEADDR`, as librespot's does, and all of them get every packet. ponytail: no probing or conflict resolution (RFC 6762 section 9), so two Q2s with the same Bluetooth name on one network claim one instance and the phone lists one of them; renaming either fixes it.

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

Limits, all on the device: one pairing at a time device-wide (`BUSY` to a second phone while the first is connected; once it has gone, a second phone's `PairStart` ends its code and gets a new one); a code lives 120 s from when it is first shown, across the phone's reconnections; 3 wrong codes in a row lock pairing for 5 minutes (`PAIRING_LOCKED`); at most 8 paired phones (`PAIRING_FULL`); closing the dialog on the Q2 cancels (`Error{PAIRING_CANCELLED}`, pushed with `req_id` 0). New codes go up four a minute at most, however many names ask (`BUSY` past that; the same phone asking again keeps its code and is not counted). A stranger can at worst make a code appear on a Q2 whose Car Remote is on, which the user can close.

The confirm keys are bound to the transcript (`sid` and both points), so a pairing cannot be spliced from two sessions.

### Pairing by QR code

Settings > Car Remote > Pair a Phone shows a code made the same way (uniform over 000000-999999, from the Q2's randomness) in two groups of three, and above it a QR code while `q2-iroh` runs and can be reached: it has a relay (any network), or Wi-Fi has an address (a phone on the same Wi-Fi; without a relay iroh cannot cross networks). The QR code carries the code with what the phone needs to reach the Q2:

```
Q2MUSE:1:<code>:<endpoint id>:<Wi-Fi address>:<relay URL>
```

in capitals (QR's alphanumeric mode keeps it at version 6); `1` is this format's version, an empty field is unknown, and the relay URL, last, keeps its own colons. The phone keeps the endpoint id, relay and address as the Q2's link, connects (the Wi-Fi address and the iroh endpoint at once, the first to answer kept), and sends `PairStart` then `PairFinish` on the code it read, with nothing typed.

The code is the next pairing's, whichever way the phone came (TCP, iroh or Bluetooth, the QR code scanned or the code typed): `PairStart` takes the code the page shows instead of a new one (the pairing card still shows it, with the phone's name), and when that pairing ends, paired, wrong or timed out, the code is spent and the page shows a new one. So the PAKE is unchanged and a code still gives one guess: someone who reached the Q2 without seeing its screen gets one try at a code they never saw, and the Q2's screen shows them asking. Closing the page takes its code away. The QR code puts the endpoint id on the Q2's screen, where only someone holding the Q2 sees it; knowing it grants nothing but a pairing request.

### Authentication: every session

`Auth.client_proof = HMAC-SHA-512(ltk, "coda-rc auth v1" || "client" || sid)[0:32]`, checked in constant time; then `AuthResp.device_proof = HMAC-SHA-512(ltk, "coda-rc auth v1" || "device" || sid)[0:32]`, which the client checks, so each side knows the other holds `ltk`. A wrong proof gets `Error{UNAUTHORIZED}` and the session closes.

### Keys

`HMAC-SHA-512(ltk, "coda-rc session v1" || sid)` gives 64 bytes: the first 32 are `kc` (client to device), the last 32 `kd` (device to client). They exist only for that session.

Labels are ASCII with no terminator; `||` is concatenation. `tools/coda_rc_crypto.py` is the reference, and `test/unit/test_coda_rc.py` checks it and the daemon against each other and against Monocypher.

### Where keys live

- **Device:** `/mnt/data/coda-rc/peers`, mode 0600 in a 0700 directory: up to 8 fixed 76-byte records, `peer_id[8] | ltk[32] | name[32] (NUL-padded UTF-8) | created u32 LE (Unix seconds)`. `/mnt/data/coda-rc/iroh-peers` (0600) holds each phone's registered iroh key, `peer_id[8] | key[32]` ([Across networks: only paired phones](#across-networks-only-paired-phones)). `/mnt/data/coda-rc/id` holds the 8-byte device id. None of them is on the card, so a card lent or sold carries no keys. Settings > Car Remote lists the names and forgets a phone by removing its record; the daemon notices within a second, closes that phone's sessions and takes its iroh key off `q2-iroh`'s list.
- **Phone:** `peer_id` and `ltk` per device id, in the app's private storage (Android Keystore-wrapped).

## Versioning

- `Hello.proto_version` is the highest version the client speaks; `HelloResp.proto_version` is the one the session uses, and `min_version` the oldest the device still speaks. A client older than `min_version` gets `Error{VERSION}` and the session closes. This is version 1.
- Within a version, change is additive: new frame types, new fields, new enum values. Readers skip unknown fields and treat unknown enum values as their zero value. An unknown frame type gets `Error{UNSUPPORTED}` and the session goes on.
- `HelloResp.caps` says what this Q2 serves (`Capability` in the proto): a Q2 without the play shim, or whose demo is not answering it, leaves `CAP_PLAY` clear, and the app grays out Play rather than failing.
- Commands and lists added within version 1 come with a capability bit, so a phone and a Q2 of different ages still work together: `SetMode` (`CAP_MODES`), `Rate` with `NowPlaying.hearts` and `rateable` (`CAP_RATE`), `PlayQueueItem` (`CAP_QUEUE_JUMP`), and `Play.resume` with the playlists' `ACTION_RESUME` (`CAP_RESUME`), `NowPlaying.buffering` with `buffered_bytes`, `total_bytes` and `buffered_ms` (`CAP_BUFFERING`, 8192), and `StartRadio` with `NowPlaying.radio` (`CAP_RADIO`, 16384), and `NowPlaying.notice` with `notice_id` (`CAP_NOTICE`, 32768). An older Q2 leaves the bits clear and answers the frames `UNSUPPORTED`; the app then hides the car's Shuffle, Repeat, heart and Radio buttons, leaves the queue untappable and shows no Resume row, as before. An older app never sends them and ignores the new fields. All seven need demo's shim, as Play does: they are clear when it is not answering. See [Modes, rating, the queue, Resume and Radio](#modes-rating-the-queue-resume-and-radio). `GetRemote` (`CAP_REMOTE`, 65536) needs no shim: its bit is set whenever the daemon serves it, and the answer is `UNAVAILABLE` while `q2-iroh` is not running. `RegisterRemote` (`CAP_IROH_ALLOW`, 131072) needs no shim either; an app that never sends it keeps Bluetooth and the Wi-Fi but has no reach across networks ([Across networks: only paired phones](#across-networks-only-paired-phones)).
- A change that cannot be additive (a new frame layout, a new key schedule) is a new `proto_version`. The labels carry the version (`"coda-rc pake v1"`), so a v2 key schedule never collides with v1.

## Modes, rating, the queue, Resume and Radio

Each goes to demo's shim as one line in `/tmp/coda-rc.cmd`, as Transport does, and is acked once the Q2 has done it; the State push that follows shows the result.

- **SetMode** sets shuffle or repeat where the Vamp keeps them. Normally that is Now Playing's Shuffle and Repeat buttons (stock's play mode), so the buttons show the phone's change and NowPlaying shows theirs. While the Play/Pause hold menu's extended play runs (one of its shuffles or repeats chosen), it is that menu's Shuffle and Repeat rows (`playback_set`); the Vamp has more modes there than the protocol names, so a SetMode keeps the nearest one: shuffle on keeps a shuffle by album, Off keeps Play One Song or Play One Album (else Play All), One is Repeat Song, All keeps Repeat Album (else Repeat All). `UNAVAILABLE` when there is no queue to apply it to.
- **Buffering** (`CAP_BUFFERING`): while a server song waits to start, or playback has caught up with its download, `NowPlaying.buffering` is true, with the bytes on the card, the whole song's (0 unknown) and the bytes' share of its length in ms, which Android Auto shows as its spinner (`STATE_BUFFERING`). The shim takes them from Now Playing's own test (`np_stream_loading`, [server-library.md](server-library.md#download-ahead-and-the-streaming-state)) into `/tmp/coda-rc.now` (`buf`, `bhave`, `btotal`, `bms`); a change of `buffering` is pushed at once, the bytes at most twice a second.
- **Rate** gives the playing song 0 to 5 hearts as Now Playing's heart and its picker do ([Ratings](internals.md#ratings)): 5 puts it in Favorites, 1 to 4 keep that many hearts, 0 clears both. It names the song (`track_id`), so a song that changed meanwhile answers `NOT_FOUND`; a card app's streaming song is named by its server song's id ([A streaming song](#a-streaming-song)); a radio stream (`rateable` false) answers `UNAVAILABLE`.
- **GetQueue**'s list under a shuffle is the play order the Q2's View Queue shows: the songs already played, the one playing (its `position`, and `NowPlaying`'s queue place), then the songs to come in the order they will play; otherwise it is the queue's own order ([internals: Car Remote](internals.md#car-remote)).
- **PlayQueueItem** plays the queue's song at an index of `GetQueue`'s list, as a row of the Q2's View Queue does: the playing song plays on, another starts unless Headphones required forbids it (`UNAVAILABLE`). It names the row's song too, and a queue that changed since the phone fetched it answers `NOT_FOUND`.
- **Play with `resume`** carries on a list where it was left. The daemon remembers, for each list a phone played (not shuffled), the song it was on, in `/mnt/data/coda-rc/places` (the 16 most recent lists). If the Q2's queue is still that list's, Resume is Play; otherwise the list starts again from that song, and the Q2's Resume Songs takes a long song back to its place. A list never played from a phone starts at its first song. The app offers it as the playlists' Resume row (`ACTION_RESUME`), as Symfonium does.

- <a id="radio"></a>**StartRadio** (type `0x46`) starts a song radio as Now Playing's Radio does ([internals: Radio](internals.md#radio)): songs like a seed, endless, the seed first. An empty `seed_id` is the playing song, which plays on where it is; a `song:` id is that song, from its start; an artist node (`artist:<k>`) is one of its songs, picked at random. A server song's radio is its card app's (the server's similar songs); any other song's is the Vamp's own library (the seed's artist, then its genre, shuffled, nothing played lately). `stop` ends the radio playing; the songs queued play on. The daemon sends the shim `radio <record id>` (0 the playing song) or `radio stop`; the Ack comes once the Q2 took it (for a server song, once its app was asked), and `NowPlaying.radio` (field 17) says when a radio plays, as the Vamp's blue Radio pill does: the car then shows its Radio button on, and should treat Shuffle and Repeat as the radio's. `NOT_FOUND` for a song id the Q2 has not, or an artist with no songs; `BAD_REQUEST` for any other seed.

## Notices

The Vamp's toasts ("Radio isn't available for this song", "Starting Radio…") are also `NowPlaying.notice` (`CAP_NOTICE`, field 18) for the 4 s after each (`CR_NOTE_MS`), then empty; `notice_id` (field 19) counts them, so the same words twice are two notices. A change of either is pushed at once. Android Auto draws no phone toast and draws a playback-state error as a pill at most about once a minute, so the Vamp app shows a notice in place of the song's artist for a few seconds. The shim takes every toast (`library_pages.c` `toast`, through `coda_rc_note`) while Car Remote is on.

## Favorites and Recently Added

The Library level lists two more nodes after Folders, each a flat list of songs that Play and Shuffle take whole:

- **Favorites** (`favorites`, `CAP_FAVORITES`, `ICON_FAVORITES`): the liked songs, stock's My Fav (the heart, [Ratings](internals.md#ratings)), in the tracks' orders (Title first, then Artist, Album and Recently added), lettered past 120 as Tracks is. It can be empty.
- **Recently Added** (`recent`, `CAP_RECENT`, `ICON_RECENT`): the 100 songs added to the library last, newest first, in that one order and never lettered.

A Q2 without the bits answers either id `NOT_FOUND` and does not list it, so the app shows its Favorites tab only when `CAP_FAVORITES` is set. ponytail: Most Played is not served. Its counts are demo's (`/mnt/data/ringnav-plays`, keyed by `listen_key`, a hash of each path, `patch/navigation.c`); serving it means the daemon reading that record and hashing each song's path the same way.
## Links from now playing

`NowPlaying.album_id` (field 20) and `artist_id` (field 21) name the Browse pages of the playing song's album and of its album's artist, so Android Auto's artist and album lines on the now-playing screen open them. `album_id` is the album's node (`album:` and the album's key), whose page lists the playing song by its `track_id`; `artist_id` is the album artist's node as the Artists list has it (`artist:r` and the hash of the album artist's name), whose page lists that album. Each is exactly the id Browse serves for that page, card song or server song. A field is left out when there is no such page: a station, a stream or a file not in the library, a server song while its server is not signed in, a song with no album artist (no `artist_id`). The daemon looks them up once per song (`np_nodes`, `coda_rc_lib.c` `lib_song_nodes`), not at each State. They were added within version 1 with no capability bit: an older Q2 never sends them, so the app links nothing (or falls back to `album:` and `art_id`), and an older app skips the two fields.

## A streaming song

A card app's song played without an offline copy plays from its download, `/mnt/mmc/.<app>/stream/<account>/<quality>/<x>/<title> [<sid>].<suffix>` (`x` a hex digit of the sid's hash), which the library has no record of. `NowPlaying` then carries the catalogue's server song for the `<sid>` in the name's last brackets (`servermeta.sid`, joined to `servertable` by `fileurl`): its `track_id` (`song:c<id>`), title, artist, album, its length while the player reports none, and its album's `art_id` (`s<album>`). `GetCover` for a server album with no `covers/<album>.jpg` answers the picture embedded in a download of one of its songs, the one the Vamp shows. A stream not in the catalogue keeps what the Vamp reports. It is `rateable`, and `Rate` takes its `song:c<id>`: the Vamp rates the download as its server song, as its own heart does. `GetQueue` names a queued download the same way: the shim's queue line for a song with no library record carries its path, title and artist, so the row is the catalogue's song (its id, title, artist, cover and length), else the record's title and artist, never `[Not in the library]`; `PlayQueueItem` takes that row's `song:c<id>`.

A server song pressed (a Play from the car, Shuffle Songs included) is `NowPlaying`'s from the press, as the Vamp's own Now Playing shows it while the app fetches it (`catalogue_np_now`, [server-library.md](server-library.md#now-playing-while-it-loads)): its id, title, artist, album, art id and the catalogue's length, at 0 and not `rateable` until the player has it, with `buffering` while it loads.

## Playlist covers

A row of `playlists` has an `art_id` like any other: its first song's album's, so `GetCover` sends that album's cover. A server playlist (one of a signed-in card app's catalogue) has its server's own cover instead once the app has fetched it (Navidrome makes one from the playlist's albums): its `art_id` is then `p<playlist id>`, and `GetCover` sends the app's file of it, a JPEG of at most 160 px a side. Until the app has it, the row keeps its first album's. Art ids stay opaque to the phone; a new cover for the same playlist answers with a new `Cover` version (field 5).

## A server album's cover

A server album's cover that the Vamp doesn't have yet (its card app hasn't fetched it, and no download of its songs has a picture) is fetched for the car when it asks: `GetCover` at offset 0 for its `s<album>` art id asks the card app for it at the size the car asked for (512 px over 160, else 160), when the app offers it, and the answer waits for the app, at most 5 seconds, then is the cover or `NOT_FOUND`. Other requests are answered meanwhile, and their answers may come before it: a client matches answers by `req_id`. Whatever the answer, the cover is kept on the Q2 once the app has it, so a `GetCover` again later (the car drawing the row again) gets it. Each album is asked of the app once; one it has none for answers `NOT_FOUND` at once for a while. How the Q2 does it: [internals: A cover the card lacks](internals.md#car-remote-covers).

## Errors

`Error{code, message}` answers a request that failed; codes are `ErrorCode` in the proto. `message` is English for logs, never for display. The device closes the session, after sending the error when it can, on:

- any malformed frame (bad `len`, flags, or a sealed frame that does not open);
- a handshake out of order (`PairFinish` before `PairChallenge`, a second `Hello`);
- `Hello` without a 16-byte nonce, or with no version in common;
- `Auth` with a wrong proof, or before the session is paired.

Everything else (`NOT_FOUND`, `BAD_REQUEST` on a field, `UNAVAILABLE`, `BUSY`, the pairing errors) leaves the session open.

A command the Vamp carries out (`Transport`, `SetMode`, `Rate`, `PlayQueueItem`, `Play`, `StartRadio`) is answered once it is done: within 4 s, or `UNAVAILABLE`. A `Play` or a `StartRadio` the Vamp has taken but is still starting (a shuffled list of a big library can take seconds) is answered when it has started, within 7 s of being taken, else `UNAVAILABLE`: under the 8 s the Vamp app waits for any answer before it drops the session.

## Pairing on the Q2

When a phone sends `PairStart`, the Q2 asks on its pairing card over whatever is on screen: a phone glyph, the title "Pair with Pixel 8?" (the phone's name, scrolling when long), "Enter this code in the Vamp app", the code large as two groups of three, and one row, Cancel, with the pairing's two minutes as a bar along its foot. Return is Cancel. It closes by itself when the phone finishes, fails or gives up, with a toast: the phone's name and "paired" ("Pixel 8 paired") or "Pairing failed".

Settings > Car Remote: the first row is the switch (Off by default), its caption under it while selected. On, Pair a Phone follows, then each paired phone by name over its state: "Connected over Wi-Fi" or "Connected over Bluetooth" while it has an authenticated session on that link, else "Not connected", with "· last seen Friday" once the Vamp has seen it connect or leave. Pair a Phone opens one page with the pairing's code, which `PairStart` takes however the phone came ([Pairing by QR code](#pairing-by-qr-code)). With `q2-iroh` running and reachable (a relay, or a Wi-Fi address), the QR code on the left, the code under it, and beside it "On your phone, open the Vamp app. Tap Pair a Vamp, then scan this or type the code", where it works from ("Works from any network" with a relay, else "Phone on the same Wi-Fi as this Vamp"), "Or nearby, by Bluetooth" while the Vamp advertises, and "Wi-Fi" with the address when it has one. Else, while a phone can find it by Bluetooth (advertising) or on the Wi-Fi (an address), the code alone, large, over "On your phone, open the Vamp app, tap Pair a Vamp and type this code", then "Bluetooth  Phones nearby can find it" while it advertises and the Wi-Fi address with "Phone on the same Wi-Fi" (the app's default port is this one, 7720); with no code made (no randomness) the words say the code shows when the phone asks, on the pairing card. With neither, the steps "Turn on Bluetooth, or join a Wi-Fi network in Settings > Wi-Fi", "Come back here to pair", and "Not reachable yet". The Vamp waits for the phone's `PairStart`. Centre on a phone opens its page: Status, Paired, Last Connected, Rename (a local name, written into the peers record) and Forget Phone, which asks `Forget Pixel 8?` on the confirmation card ("It has to pair again to control this Vamp."), Cancel first. With no phones paired the page shows the empty state, "No Paired Phones", under the switch, with Pair a Phone as its button. The page follows the daemon while it is open: a phone paired, forgotten, renamed, connecting or going shows within half a second, without leaving the page (while the Forget card is up, at the first poll after it closes). The daemon keeps two more files for it: `/tmp/coda-rc.online`, `peer_id[8] | link u8` (0 TCP, 1 BLE) per authenticated session, and `/mnt/data/coda-rc/seen`, `peer_id[8] | when u32 LE` (Unix seconds) per paired phone, stamped when a session of it authenticates or ends.

## Testing on a Q2

The emulator proves the protocol, the daemon, the data and the TCP transport end to end (`test/coda_rc_e2e.py`); `test/coda_rc_iroh_e2e.py` proves the allow-list across networks on this machine, through n0's relay ([Across networks: only paired phones](#across-networks-only-paired-phones)). It has no Bluetooth, so the following can only be proven on a real Q2, with a `--dev` build and `tools/q2remote.py` for a shell and logs:

1. **Daemon and adapter.** Turn Car Remote on. `q2remote.py sh 'ps; cat /tmp/coda-rc.log'`: coda-rc runs at nice 19, and its log shows `ble: advertising` (`/tmp/coda-rc.ble` exists), or once the reason it is not: `ble: Bluetooth is off; waiting for it`, `ble: off while SyncLink is on`, `ble: no LE ATT channel (N)` (errno N: something else listens on CID 4), `ble: advertising refused: status 0xNN` (a management status: `0x0c` not supported or `0x0b` rejected would mean LE is off on the adapter). `btmgmt info` (if present) or `hciconfig hci0` shows LE among the settings.
2. **Advertising.** On the phone, nRF Connect: scan, see the Q2's Bluetooth name with service `b67cefa7-...`, connectable. Turn Car Remote off: the advertisement goes within a second.
3. **GATT, no bond.** Connect from nRF Connect without bonding: it discovers the GAP service and the three characteristics, reads `info` as `coda-rc 1` and Device Name as the Bluetooth name. The advertisement stops while connected (a second scanner no longer sees it). Request MTU 247. Enable notifications on `event`, write a `Hello` frame by hand (`tools/coda_rc_client.py frame hello` prints its hex): a `HelloResp` arrives as notifications. The phone's Bluetooth settings must not list the Q2 as a paired or audio device afterwards.
4. **A full session over BLE** with the app (or a central running `coda_rc_client.py`'s logic): pair (the code appears on the Q2), browse, play a list, watch State pushes for 10 minutes. Note dropped frames (the session closes on any) and cover fetch times.
5. **LE while A2DP streams (the critical one, research test 2).** With the Q2 playing to the car or a Bluetooth speaker over A2DP, run step 4 with continuous browsing and cover fetches for 10 minutes. Pass: no audible dropouts, and the LE link stays up. Repeat with AAC and SBC, and with LDAC. Note the connection interval the phone settled on (`btmon` or `hcidump` on the Q2 if present, else Android's HCI snoop log: the LE Connection Update Complete event) and that it is 30 ms or more, and compare the Bluetooth link log (`tools/q2bt.py`) with and without the LE session. If this fails, use TCP over the car's hotspot.
6. **AVRCP untouched.** With the car connected (A2DP + AVRCP: steering-wheel buttons and the car's track display working), connect and use the app over LE. Pass: the car's buttons and metadata keep working throughout (coda-rc never touches Classic profiles; SyncLink's `"SyncLink Mode | disconnect"` line must not appear in `logread`/demo's log).
7. **Cover latency.** Time a cold `GetCover` for a 96 px thumbnail and a `Browse` of 50 items, over LE and TCP.
8. **Forget.** Forget the phone in Settings > Car Remote while it is connected: its session closes within a second and its next `Hello` reports `paired: false`.
9. **Off means off.** Car Remote off: `netstat -ltn` shows nothing on 7720, `netstat -lun` no coda-rc on 5353, and no advertisement is seen.
10. **Discovery.** With Car Remote on and the phone on the Q2's Wi-Fi, the Vamp app's Pair a Vamp lists the Q2 under its Bluetooth name within a few seconds, and the log says `mdns: <name> on <address>`. From a computer on that Wi-Fi, `avahi-browse -rt _q2muse._tcp` shows its SRV, TXT and address. Turn Car Remote off: the entry goes at once (the goodbye). The emulator proves the same against python-zeroconf with the daemon on the host's interface (`--mdns-if eth0`), as of 2026-11-17.
