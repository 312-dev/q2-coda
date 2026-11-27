# bluetoothd: Shanling's changes to BlueZ 5.66

Stock's `/usr/bin/bluetoothd` is BlueZ 5.66 with Shanling's changes, for which no source is published. Vamp's
own bluetoothd ([bluez](bluez.md)) has to carry each of them, or receiver mode, codec priority, the track
information a car shows and SyncLink stop working. This is what the stock binary does differently, read for
interoperability from its disassembly (`llvm-objdump` of V1.36's bluetoothd), its strings and its dynamic
symbols; the debug strings BlueZ keeps in the binary name the file and function around each change, and
comparing them with an upstream 5.66 build's finds every function Shanling added to or logged differently.
Addresses are the stock binary's. Nothing of Shanling's is copied: what a rebuild carries is written from
this description.

## The link to libbtctl

bluetoothd links Shanling's `libbtctl.so` and imports from it two functions and five data objects:

| Import                                     | Kind                        | What it is                                                                                                                     |
| ------------------------------------------ | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `bluez_main()`                             | function                    | Starts the `shanling.bluez` D-Bus service inside bluetoothd: `NotifyChanged`, `SetSynclink`, `GetVolumePath`, `PlayInfoChange` |
| `bluez_play_info_signal(event, data, len)` | function                    | Emits `PlayInfoChange` on `shanling.bluez`; demo's libbtctl hands it to the A2DP callback (`btrecvA2dpFunc`)                   |
| `g_track_meta`                             | pointer, 4 bytes            | The playing track as demo last sent it (below)                                                                                 |
| `g_play_status`                            | 4 bytes                     | Its play status                                                                                                                |
| `g_avrcp_event_fun`                        | function pointer            | Set by bluetoothd to `avrcp_hciplayer_event`; libbtctl calls it to send AVRCP notifications                                    |
| `g_notify_mutex`                           | `pthread_mutex_t`, 24 bytes | Guards the two above                                                                                                           |
| `g_high_priority_lcodec`                   | 1 byte                      | The codec the Quality page asks for first                                                                                      |
| `g_synclink_mode`                          | 1 byte                      | SyncLink on                                                                                                                    |

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

## What the adapter leaves out

Upstream's `adapter_register` makes a GATT database and an advertising manager for an LE controller
(`btd_gatt_database_new`, `btd_adv_manager_new`); stock's does neither. Both functions are still in the binary
(`src/gatt-database.c`, `src/advertising.c`), but the string upstream logs when the database cannot be made,
`Failed to create GATT database for adapter`, is in an upstream 5.66 build and not in stock's, and a running
stock bluetoothd has no `GattManager1` or `LEAdvertisingManager1` on `hci0` (`test/bluez_own.py` reads both
daemons' interfaces). So stock's bluetoothd does not listen on the LE attribute channel (L2CAP CID 4), and two
programs do in its place: SyncLink's `libSL.so` in demo and Vamp's `coda-rc`
([remote-protocol](remote-protocol.md#bluetooth-le-primary)). An upstream bluetoothd listens there itself
([bluez: The LE attribute channel](bluez.md#the-le-attribute-channel)).

## Build settings

- The store is `/mnt/data/bluetooth` (upstream: `/var/lib/bluetooth`), on the Q2's writable partition:
  `ADAPTER/settings`, `ADAPTER/DEVICE/info`, `ADAPTER/cache/DEVICE`, in upstream 5.66's format.
- Plugins: the ones [third-party](third-party.md#what-reaches-them) lists, and `shanling`; no `input`, `hog`
  or `network`. Its external plugin folder is a path on Shanling's build machine that the rootfs does not
  have, so none loads.
- It links, beyond upstream's GLib, D-Bus, libdl and librt: `libbtctl.so`, and the libraries libbtctl's
  D-Bus service leans on (`libgio-2.0`, `libgobject-2.0`, `libgmodule-2.0`, `libgthread-2.0`, `libffi`,
  `libpcre2-8`, `libncurses`, `libpthread`).
- A crash handler prints a backtrace (`bluez5 backtrace() returned %d addresses`) through glibc's
  `backtrace_symbols`.

## What a rebuilt bluetoothd needs

Link `libbtctl.so` as stock does and put each change above back in the upstream source; SyncLink's matter
only while the user has SyncLink on, the others always. How Vamp's own bluetoothd carries each is
[bluez: Shanling's changes](bluez.md#shanlings-changes).
