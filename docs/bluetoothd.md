# bluetoothd: Shanling's changes to BlueZ 5.66

Stock's `/usr/bin/bluetoothd` is BlueZ 5.66 with Shanling's changes, for which no source is published. A
bluetoothd rebuilt from upstream 5.66 (`tools/thirdparty.py` `bluez`) has to carry them, or receiver
mode, codec priority, the track information a car shows and SyncLink stop working. This is what the
stock binary does differently, read from its disassembly (`llvm-objdump` of V1.36's bluetoothd); the
debug strings BlueZ keeps in the binary name the file and function around each change, and comparing
them with an upstream 5.66 build's finds every function Shanling added to or logged differently.
Addresses are the stock binary's.

## The link to libbtctl

bluetoothd links Shanling's `libbtctl.so` and imports from it two functions and five data objects:

| Import | Kind | What it is |
| --- | --- | --- |
| `bluez_main()` | function | Starts the `shanling.bluez` D-Bus service inside bluetoothd: `NotifyChanged`, `SetSynclink`, `GetVolumePath`, `PlayInfoChange` |
| `bluez_play_info_signal(event, data, len)` | function | Emits `PlayInfoChange` on `shanling.bluez`; demo's libbtctl hands it to the A2DP callback (`btrecvA2dpFunc`) |
| `g_track_meta` | pointer, 4 bytes | The playing track as demo last sent it (below) |
| `g_play_status` | 4 bytes | Its play status |
| `g_avrcp_event_fun` | function pointer | Set by bluetoothd to `avrcp_hciplayer_event`; libbtctl calls it to send AVRCP notifications |
| `g_notify_mutex` | `pthread_mutex_t`, 24 bytes | Guards the two above |
| `g_high_priority_lcodec` | 1 byte | The codec the Quality page asks for first |
| `g_synclink_mode` | 1 byte | SyncLink on |

## Track information, from demo to a controller

demo's `on_wm_timer_dialog` calls `sendBtHeadsetPlayInfo` (`0x4fae8c` in demo) on a track change and
`sendBtHeadsetPlayStatus` (`0x4fae10`) on play or pause; libbtctl's `btctl_notify_*` send them as
`NotifyChanged(type, bytes)` to the service `bluez_main` runs in bluetoothd. Its handler (`0x298e4` in
libbtctl):

- type `0x107`: `g_play_status` = the first byte, under `g_notify_mutex`; then
  `g_avrcp_event_fun(1, &status)`, AVRCP's PLAYBACK_STATUS_CHANGED.
- type `0x108`: a 16-byte header (track number, track count, playing time in ms, attribute count, each
  32 bits) and attributes `{u16 id, u16 len, bytes}` with ids 1 title, 2 artist, 3 album, 6 genre,
  become a record: `+0` track number, `+4` track count, `+8` playing time, `+0xc` title, `+0x10` artist,
  `+0x14` album, `+0x18` genre. It replaces `g_track_meta` under the mutex (freeing the old one), then
  `g_avrcp_event_fun(2, &uid)`, TRACK_CHANGED, the UID 0 with a track and all ones without.

In bluetoothd (`profiles/audio/avrcp.c`):

- `avrcp_init` (`0x388c0`) sets `g_avrcp_event_fun` to `avrcp_hciplayer_event` when it is still null.
- `avrcp_hciplayer_event(id, data)` (`0x38e2c`) sends a CHANGED vendor-dependent RegisterNotification
  response (company `0x001958`, PDU `0x31`) for event 1 (status byte from `data[0]`) or 2 (UID from
  `data[0..7]`) on the one session in `g_hci_session`, when it registered for that event, then clears the
  registration, as upstream's `avrcp_player_event` does per session. Other ids log "Unknown event".
- `g_hci_session`, a global of Shanling's, is the target session last set up (`session_init_control`,
  `0x3d388` and `0x3d4b8`), set to null by `adapter.c`'s `dev_disconnected` (`0x79e50`) at any device's
  disconnection.
- GetPlayStatus (`0x37324`): the position and duration from the registered player as upstream, a
  duration of 0 reported as `0xffffffff`, and with no player duration `0xffffffff` and position 0; the
  status byte from `g_play_status`.
- RegisterNotification (`0x38c14`): as upstream, but PLAYBACK_STATUS_CHANGED's interim status comes from
  `g_play_status` and TRACK_CHANGED's interim UID is always 0.
- The control channel's GetElementAttributes (`0x39454`) is upstream's: it reads a registered player,
  and stock has none, so it answers nothing useful.
- The browsing channel: Shanling's handler table (`0x13345c`) has GetFolderItems (`0x3c310`: the media
  player list only, one entry, from the default features; other scopes refused with INVALID_SCOPE),
  GetItemAttributes (`0x3bf74`) and a PDU `0x20` (`0x3bb7c`), both answering from `g_track_meta`: all
  seven attributes (title, artist, album, track number, number of tracks, genre, playing time; numbers
  as `%d`, UTF-8) whatever was asked, or a title of one space with no track; and GetTotalNumberOfItems
  as upstream.

## Receiver mode (a phone playing to the Q2)

`bluez_play_info_signal` is called through four small wrappers after `src/settings.c` (`0xa5cf0` to
`0xa5efc`):

- `0xa5cf0(int v[3])`: event `0x103`, 12 bytes `{duration, position, status}`. `avdtp.c`'s command parser
  sends `{-1, -1, 0x11}` when a phone's START arrives and `{-1, -1, 0x12}` on its SUSPEND, which open and
  close demo's receiver page; `avrcp.c`'s controller side sends the phone's play status
  (`avrcp_get_play_status_rsp`, `0x38124`, after upstream's `media_player_set_*`) and `{-1, position, -1}`
  on a PLAYBACK_POS_CHANGED notification (`0x3e5f0`).
- `0xa5d2c(recv_meta *)`: event `0x104`, the phone's track: `{u32 duration, u32 count}` then entries
  `{u16 id, u16 len, text and its NUL}` for 1 title, 2 artist, 3 album. `recv_meta` is a static record
  (`{int duration; GString *title, *artist, *album}`) `avrcp_init` creates and `avrcp_parse_attribute_list`
  (`0x3b190`, the controller side) fills from the phone's attributes as well as upstream's, sending it
  when one changed (it also cuts a text at a `C0 80` pair, the modified-UTF-8 NUL some phones send).
- `0xa5ebc` (event `0x106`) and `0xa5efc` (event `0x105`), 4 bytes each: no caller.

## Codec priority

`a2dp.c`'s `a2dp_find_eps` (`0x17bd0`) lists the remote endpoints in a fixed codec order in place of
upstream's "last used first": the codec `g_high_priority_lcodec` names first (`0x13` LDAC, `0x12` aptX HD,
`0x11` aptX, `2` AAC, anything else SBC), then LDAC (vendor `0x12d`), aptX HD (`0xd7`), aptX (`0x4f`),
AAC (`2`) and SBC (`0`), each once; endpoints of any other codec are left out. `codec_compare`
(`0x180dc`) matches a standard codec by its id and a vendor codec (`0xff`) by the vendor id its local
endpoint's capabilities carry. demo sets the byte through `btctl_set_codec_priority` from the Quality page.

`endpoint_setconf_ind` (`0x1d954`) also registers the remote endpoint a headset configured itself
(`avdtp_register_remote_sep` and `register_remote_sep`, logged "AAA Unable to register Endpoint" and "AAA
avdtp_unregister_remote_sep" on failure) when the channel already knows other remote endpoints, where
upstream only starts a reverse discovery when it knows none.

## SyncLink

`g_synclink_mode` is read at six places:

- `policy.c` `service_cb` (`0x13e80`): Shanling's `struct reconnect_data` has one more bool after
  `reconnect` (offset 5) holding what `reconnect_match` said; `reconnect` becomes `false` while SyncLink
  is on, else that value.
- `avdtp.c` `session_cb` (`0x24764`): a DISCOVER command is rejected as if no endpoint existed; and
  (`0x24c70`) a peer's accepted DISCOVER response is turned into a reject (NOT_SUPPORTED_COMMAND, `0x19`).
- `avctp.c` `avctp_confirm_cb` (`0x33984`, `0x33ae8`): an incoming AVCTP connection is shut down ("AVCTP:
  SyncLink Mode | disconnect connect from %s").
- `avrcp.c` `session_init_control` (`0x3d2cc`): the passthrough handler is not registered.

## The shanling plugin

`profiles/shanling/shanling.c`, a `btd_profile` named `shanling-target` for the remote UUID
`0000cc0c-0000-1000-8000-00805f9b34fb`, whose adapter probe adds an SDP record to the adapter: browse
group public, service class `0xcc0c`, profile descriptor `0xcc0c` version `0x0101`, L2CAP and RFCOMM
channel 28, name "SHANLING TG". It listens on nothing itself: demo's `libSL.so` serves SyncLink's RFCOMM
channel; the record lets Shanling's phone app find it.

## `main`

`bluez_main()`, with no arguments, right after `umask(0077)`.

## What a rebuilt bluetoothd needs

Link `libbtctl.so` as stock does and put each change above back in the upstream source; SyncLink's
matter only while the user has SyncLink on, the others always. Q2 Pod's own additions (the control
channel answering from `g_track_meta`, the library browsable from a car, cover art) go on top.

## The rebuild

`tools/thirdparty.py` builds BlueZ 5.66 (`bluez`) with `tools/bluez/shanling.patch`, which puts back every change
above, against GLib 2.74.4's and D-Bus 1.12's headers and soname stubs of stock's libraries (`stubs.tsv`, read from
the stock rootfs), linking libbtctl and the GIO set as stock does, position independent as stock is. `check_abi`
resolves its imports against the real libraries. Under `qemu-mipsel` with the real libbtctl it owns `shanling.bluez`
and `org.bluez` and loads stock's plugins. `test/bluez_port.py` runs the hooks as MIPS under Unicorn
(`test/bluez_emu.py`: the binary at a load address of its own, its GOT and relocations as ld.so leaves them, imports
answered in Python): GetPlayStatus and RegisterNotification from `g_play_status`, `avrcp_hciplayer_event`'s CHANGED on
`g_hci_session` and its clearing at a disconnection, `avrcp_init` setting `g_avrcp_event_fun`, the codec order for
each priority, the phone's track and START or SUSPEND to demo, SyncLink's DISCOVER rejection, and the additions below:
GetElementAttributes from `g_track_meta`, the browsing PDUs and PlayItem handed to coda-rc (and upstream's player list
when it does not answer), SetAddressedPlayer for the Q2's player, and `shanling_browse`'s frames. It is not installed:
stock's bluetoothd stays the shipped one until the rebuild is proven on a Q2.

## The rebuild's additions

- The control channel's GetElementAttributes answers from `g_track_meta` under `g_notify_mutex`
  (`shanling_get_element_attributes`), as `tools/bt_meta.c` makes stock's binary do
  ([internals](internals.md#track-metadata)). The browsing channel's PDU `0x20`, which stock answers from the
  same record, is not carried over: it is no PDU AVRCP defines, and GetItemAttributes on Now Playing answers
  the queue's songs instead.
- SetAddressedPlayer with player 1 is accepted while no BlueZ player is registered (the Q2 registers none):
  the player GetFolderItems lists, below.

## Browsing

A car or headset browses the library and plays from it as a phone's Car Remote does
([remote-protocol](remote-protocol.md)): the same lists, orders, letter buckets and, with Local + Server on,
server songs. The rebuilt bluetoothd hands SetBrowsedPlayer, GetFolderItems, ChangePath, GetItemAttributes,
GetTotalNumberOfItems and Search (the browsing channel) and PlayItem (the control channel) to `coda-rc --avrcp`
(`patch/coda_rc_avrcp.c`), a second instance of the car remote daemon that serves nothing else, over the UNIX
socket `/tmp/coda-rc.avrcp` (mode 0600). bluetoothd starts it (`g_spawn_async`) at the first request it cannot
deliver, then at most every 10 seconds. Nothing waits for coda-rc: the handler sends the request and
returns `AVCTP_DEFERRED`, so avctp answers nothing yet, and the reply goes out (`avctp_send_browsing_rsp`,
`avctp_send_vendordep`) when the main loop reads coda-rc's answer; other AVRCP, A2DP and D-Bus work goes on
meanwhile. A request unanswered after 1.5 seconds closes the connection and fails every request on it. With
no answer GetFolderItems and GetTotalNumberOfItems fall back to upstream's (the registered players: none)
and the others answer INTERNAL_ERROR.

A frame is `{u16 length, request}` both ways, big-endian. A request is `{pdu, tag u32, room u16, the PDU's
parameters}`: the tag is the session (the car) whose path the daemon keeps, and room the most the reply may
take, the browsing channel's MTU after the AVCTP and browsing headers; the reply is `{pdu, its parameters}`.
`{0xff, tag}` asks nothing back: the session closed, its path is dropped.

- One player, id 1, named Q2, its play status from Now Playing (`/tmp/coda-rc.now`), advertising browsing,
  searching, Now Playing and GetTotalNumberOfItems, and no AddToNowPlaying.
- The player is database unaware: the UID counter is always 0 and an item's UID is its place in the listing
  it came from, plus one. A folder is its coda-rc node (Browse); the daemon keeps a car's path as the ids it
  walked into, up to 12 deep, for 4 cars at a time (the least recently heard from is dropped).
- The root is coda-rc's Library: Tracks, Playlists, All artists, Albums, Genres, Years, Folders. A folder's
  type is what it lists (an album Titles, an artist Albums, Albums Albums); it is playable when Play takes
  it as a list of songs (an album, artist, genre, playlist, year, Tracks or a Tracks bucket).
- A song is a media element with its title, artist, album and length (read from the library: a Browse row's
  subtitle is the artist in one list and the length in another), as many as the room holds; an item that
  does not fit is left for the next request, and a room too small for one is INTERNAL_ERROR.
- Search keeps the car's query; the Search scope lists Tracks, then Albums, then Artists, 20 of each at most.
  Now Playing is the queue (`/tmp/coda-rc.queue`).
- PlayItem plays a song from its place in its folder (its album, playlist or bucket), a playable folder from
  its start, a search's song alone, a Now Playing song by its place (`jump`), each through demo's shim
  (`patch/coda_rc_shim.c`), which serves the daemon's files while `/tmp/coda-rc-avrcp.pid` exists, Car Remote
  on or off. Demo not answering is MEDIA_IN_USE.

coda-rc --avrcp keeps the library open (`lib_keep_open`), with `s2` and `albums` made once as indexed
tables, and opens it again only when the database's or catalogue's length or SQLite change counter,
Long Songs' cache or the Library setting changes. Measured as MIPS under `qemu-mipsel` on a 20,000-song,
2,000-album library: the first request 530 ms (the tables made), a 50-album page 4 to 5 ms, an album's songs
2 ms, Search 71 ms, but a letter bucket of Tracks 300 to 700 ms (`coda_rc_lib.c`'s bucket queries over every
song; `ponytail:` not yet measured on a Q2). Covers (AVRCP 1.6's
image handles and the BIP responder) are not done yet.

Tests: `test/unit/test_coda_rc_avrcp.py` (each PDU against a library in stock's schema, on this machine),
`test/coda_rc_avrcp.py BUILD` (the MIPS daemon from a build under `qemu-mipsel`: its socket, frames and a walk
into an album), and `test/bluez_port.py` (bluetoothd's side).

