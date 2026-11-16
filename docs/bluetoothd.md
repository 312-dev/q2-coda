# bluetoothd: Shanling's changes to BlueZ 5.66

Stock's `/usr/bin/bluetoothd` is BlueZ 5.66 with Shanling's changes, for which no source is published. A
bluetoothd rebuilt from upstream 5.66 has to carry them, or receiver mode, codec priority, the track
information a car shows and SyncLink stop working. This is what the stock binary does differently,
read from its disassembly (`llvm-objdump` of V1.36's bluetoothd; BlueZ's debug strings name the file and
function around each site). Addresses are the stock binary's.

## The link to libbtctl

bluetoothd links Shanling's `libbtctl.so` (its first `DT_NEEDED` after ncurses) and imports from it two
functions and four data objects, through 18 sites in all:

| Import | Sites | Where (upstream function) | What it does |
| --- | --- | --- | --- |
| `bluez_main()` | 1 | `src/main.c` `main` (`0xf228`), after the config is read, before the main loop | Starts the `shanling.bluez` D-Bus service inside bluetoothd: `NotifyChanged` (demo's `btctl_notify_track_changed` and `btctl_notify_playback_status_changed`), `SetSynclink`, `GetVolumePath`; it writes the four globals below |
| `bluez_play_info_signal(event, data, len)` | 4 | four small wrappers at `0xa5cf0` to `0xa5efc`, after `src/settings.c` | Emits `PlayInfoChange` on `shanling.bluez`, which demo's libbtctl turns into its A2DP callback (`btrecvA2dpFunc`): receiver mode |
| `g_track_meta` | 4 | `profiles/audio/avrcp.c`: GetElementAttributes (`0x3bb7c`, 3) and GetItemAttributes (`0x3bf74`, 1) | The playing track as demo last sent it |
| `g_play_status` | 2 | `avrcp.c`: GetPlayStatus (`0x37324`) and the PLAYBACK_STATUS_CHANGED notification (`0x38c14`) | Its play status byte |
| `g_high_priority_lcodec` | 1 | `profiles/audio/a2dp.c` `a2dp_find_eps` (`0x17bd0`) | The codec the Quality page asks for first |
| `g_synclink_mode` | 6 | `plugins/policy.c` `service_cb` (1), `avdtp.c` `session_cb` (2), `avctp.c` `avctp_confirm_cb` (2), `avrcp.c` target and controller setup (`0x3cfc8`, 1) | SyncLink on: no reconnects, no A2DP or AVRCP to a phone while Shanling's app holds the link |

## Each site

**`main` (`bluez_main`).** Called once, with no arguments, between reading `main.conf` and entering the
main loop. Everything `shanling.bluez` does is libbtctl's own code.

**Receiver mode (`bluez_play_info_signal`).** Four wrappers, each `bluez_play_info_signal(event, buf,
len)` then `return 0`:

- `0xa5cf0`: event `0x103`, its argument passed through, 12 bytes. Called from `avdtp.c`'s command
  parser inside `session_cb` when a phone's START (`0x07`) arrives, with `{-1, -1, 0x11}`, and on its
  SUSPEND (`0x09`), with `{-1, -1, 0x12}`; and from `avrcp.c` (`0x3e5f0`, the controller's side) with
  `{-1, x, -1}`. demo's `btrecvA2dpFunc` opens the receiver page on `0x11` and closes it on `0x12`.
- `0xa5d2c`: called from `avrcp_parse_attribute_list` (`0x3b190`, the controller's side) with the
  phone's track attributes (title, artist, album, genre, track number, duration) as it read them; it
  packs them into a buffer and emits them, which `btrecv_info_labels_update` shows.
- `0xa5ebc` (event `0x106`) and `0xa5efc` (event `0x105`), 4 bytes each: no caller in the binary.

**Track information (`g_track_meta`, `g_play_status`).** demo's `on_wm_timer_dialog` calls
`sendBtHeadsetPlayInfo` (`0x4fae8c` in demo) on a track change and `sendBtHeadsetPlayStatus`
(`0x4fae10`) on play or pause; they fill a record and call libbtctl's `btctl_notify_*`, which reach
`NotifyChanged` in bluetoothd. The record: track number (`+0`), track count (`+4`), playing time in
ms (`+8`), then pointers to title (`+0xc`), artist (`+0x10`), album (`+0x14`) and genre (`+0x18`).
Shanling's GetElementAttributes (`0x3bb7c`) ignores the attributes asked for and the registered
player: under `g_notify_mutex` it answers all seven (title, artist, album, track number, number of
tracks, genre, playing time) from `g_track_meta`, numbers formatted `%d`, character set UTF-8 (`0x6a`),
or an empty answer while it is null. GetItemAttributes (`0x3bf74`) answers the same way. GetPlayStatus
(`0x37324`) takes its status byte from `g_play_status`, and the PLAYBACK_STATUS_CHANGED notification
(`0x38c14`) too.

**Codec priority (`g_high_priority_lcodec`).** Shanling's `a2dp_find_eps` replaces upstream's "last
used first" with an order of codec ids, `0xffffff` (none), `0x12d` (LDAC's vendor id), `0xd7` (aptX
HD), `0x4f` (aptX), `2` (AAC), and moves the one `g_high_priority_lcodec` names to the front (`0x12`,
`0x11` and `2` among the values it maps), logged as `modify_priority` and `codec_list[%d]`. demo sets
it through `btctl_set_codec_priority` from the Quality page.

**SyncLink (`g_synclink_mode`).** Read as a byte at each site:

- `policy.c` `service_cb` (`0x13e80`): Shanling's `struct reconnect_data` has one more bool after
  `reconnect` (offset 5) holding what `reconnect_match` said; `reconnect` becomes `false` while
  SyncLink is on, else that value.
- `avdtp.c` `session_cb` (`0x24764`, `0x24c70`): A2DP signalling handled differently while SyncLink
  is on (to be read in full when porting).
- `avctp.c` `avctp_confirm_cb` (`0x33984`, `0x33ae8`): an incoming AVCTP connection is refused while
  SyncLink is on ("AVCTP: SyncLink Mode | disconnect connect from %s").
- `avrcp.c` (`0x3cfc8`, at `0x3d2cc`): a call made when a session's field `+0x20` is unset is skipped
  while SyncLink is on (which of the controller's or target's setups it is, to be read when porting).

## What a rebuilt bluetoothd needs

Link `libbtctl.so` as stock does, call `bluez_main()` where stock does, and put each site above back
in the upstream source; SyncLink's sites matter only when the user turns SyncLink on, the others
always. Then Q2 Pod's own additions (the library browsable from a car, cover art) go on top.
