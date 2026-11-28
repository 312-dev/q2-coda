# q2gate

`q2ui` (`ui/` at `Q2UI_COMMIT`) talks to `demo` on today's Vamp: it writes settings and verbs down a pipe it is handed (`--ctl FD`, `patch/q2ui_gate.c` on `feat/render-engine`), and reads what `demo` says in three files. With no `demo`, `q2gate` (`gate/q2gate.c`) is that other end, so the UI's status row, Control Center, Wi-Fi page and Bluetooth page work on Vamp OS until `q2ui` speaks Vamp API 1 itself ([ui-rewrite: The seams](ui-rewrite.md) on `feat/render-engine`).

| What | Where |
| --- | --- |
| Source | `gate/q2gate.c`, `gate/Makefile` |
| In the image | `/usr/bin/q2gate`, the `q2gate` service (`os/package/q2gate`, `/etc/sv/q2gate`) |
| The pipe | `/run/vamp/ui.ctl`, a FIFO: the `q2ui` service opens it both ways on fd 3 and passes `--ctl 3`; `q2gate` opens it both ways too, so neither waits for the other and neither sees an end when the other restarts |
| Tests | `test/gate.py` (this machine, stand-ins for the four services) |

## The pipe

A line each, at most 80 bytes with its `\n` (a longer one is dropped), as `q2ui_gate.c`'s `ctl` reads them:

| Line | Vamp API 1 |
| --- | --- |
| `set wifi 0\|1` | Q2NET/1 `off` or `wake` |
| `set bt 0\|1` | Q2BT/1 `power off` or `power on` |
| `set smart 0\|1` | `[Q2POD] SMARTWIFI` and Q2NET/1 `smart off\|on` |
| `set car 0\|1` | `[Q2POD] CARREMOTE`, which `q2carrd` follows |
| `set bright N` (0 to 100) | Q2SYS/1 `brightness N` and `[SYSSET] LIGHTNESS` |
| `set gapless`, `xfade` | `[PLAYSET] GAPLESS`, `[Q2POD] XFADE`, which `q2playd` reads again |
| `set light` (0 to 2), `motion`, `home` (0, 1), `songartist` (0 to 3), `songlength` (0 to 2), `night` (0 to 2), `nightk` (2000 to 5000 in hundreds) | `[IPOD] LIGHT`, `REDUCEMOTION`, `HOME`, `SONGARTIST`, `SONGLENGTH`, `[Q2POD] NIGHTSHIFT`, `NIGHTK`: `q2ui` reads its own settings back from `config.ini` |
| `scan` | Q2NET/1 `scan` (on a thread of its own: it waits for the radio) |
| `join NAME` | a saved network is `q2netd`'s to join; an open one heard is saved (`add`); then `wake`. A secured network not saved needs its password typed, on a page Vamp OS has not got (`demo`'s `kbwifipass_dialog`): it reads `Couldn't connect` |
| `btscan` | Q2BT/1 `scan` (60 s of discovery) |
| `btlink MAC` | as the row's click: the connected one `disconnect`ed, a paired one `connect`ed, a found one `pair`ed |
| `btforget MAC` | Q2BT/1 `forget` |
| `screenoff`, `wake` | Q2SYS/1 `screen off`, `screen on` |

A value out of its setting's range is refused (logged); a setting is written by changing its one line in `config.ini` (added to its section, or a new section at the end), the file written beside and renamed.

## The files

Asked of the services once a second and after each line, each written whole beside itself and renamed, only when it changes:

- **`/tmp/q2ui.status`**, `status_row.c` `status_row_state` then `control_center.c` `q2ui_cc_state`, in their order: `out` (`wire` when a jack is plugged and the music goes to the DAC, else `other`), `bt` (`none` with Bluetooth off, `connecting`, `rune` on and idle, `headset`, `car`), `btbatt` (a headset's level, else -1), `relay`, `lock`, `sync`, `syncpm`, `offline` (none of those on Vamp OS yet: `none` or 0), `net` (1), `clock24` (`[SYSSET] TIME24H`, 1 when absent); then `wifi` (the radio not off), `bton`, `btbusy` (0), `airplay` (0), `car`, `smart`, `eq` (0), `usb` (`[Q2POD] USBPICK`'s name: Automatic, Charge Only, Transfer Files, USB Audio, USB Debugging, Ask Every Time), `cable` (Q2SYS/1 `battery`'s), `bright` (`[SYSSET] LIGHTNESS`, 50 when absent), `vol` (Q2PLAY/1 `now`'s) and `volmax` (`[PLAYSET] MAXVOLUME`). No `tz`: the zone is the system's (`/etc/localtime`, [sysd: The clock](sysd.md#the-clock)).
- **`/tmp/q2ui.wifi`**, `wifi_flows.c` `wifi_flows_networks`: `scan 1` while a search runs, else `scan 0`; then, with Wi-Fi on, `LEVEL LOCK STATE NAME` a network the last search heard, the ones joining, joined or failed first; LEVEL 1 to 4 by stock's steps (-65, -75, -80 dBm), STATE 0, 1 Connecting, 2 Connected, 3 Couldn't connect (30 s after a join that did not connect); a name's `\` and newline as `\\` and `\n`.
- **`/tmp/q2ui.bt`**, `bt_ui.c` `bt_q2ui_devices`: `scan 1` while a scan runs, else `scan 0`; then, with Bluetooth on, `PAIRED STATE MAC NAME` a device Q2BT/1 `devices` lists, the paired first; STATE 2 connected, else 0.

## Not here yet

- **Key Tone**: `click 0|1` is read and nothing is done with it (the Clicker's route and the buzzer: [Migration readiness](vamp-os.md#migration-readiness)).
- `set eq` is refused: the EQ's on and off is `q2player`'s state file, not set from here yet. `eq=` says 0.
- A password typed for a new secured network, `relay` (Guest Play), `sync` (a card app's sync), `offline` (a server out of reach) and `net` (whether the internet answers).
- `q2ui` writes the pipe blocking: with `q2gate` down for good, a UI that keeps writing would fill the pipe (64 KB, some 800 lines) and wait. `runsv` starts `q2gate` again at once.

## Tests

`python3 test/gate.py` (pytest) builds `q2gate` for this machine and runs it under a temporary root with stand-ins for Q2SYS/1, Q2NET/1, Q2BT/1 and Q2PLAY/1 that answer each command and keep it: the status file byte for byte, a jack on Bluetooth reading `other` and a car the `car` slot, Bluetooth off `none`; every `set` sent or written (its section, a section added) and a value out of range refused; a search's networks with stock's levels, the joined one first; a join saving an open network and following it to Connected; a secured one not saved reading Couldn't connect with nothing added; the devices paired first and `btlink`'s three ways with `btforget`; the screen's two words and a line too long dropped. Copies of `q2gate.c` without the escape, with other levels, the paired not first, `btlink` never disconnecting, no `add`, an `add` for a secured network, `wire` on any output, the brightness not saved, no range check, no Connected, the clock's form fixed, the screen's words swapped or a key read from any section each fail it (`Q2GATE_SOURCE`).
