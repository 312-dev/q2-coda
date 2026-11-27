# q2sysd

Vamp OS's system service ([roadmap: Who owns what](vamp-os.md#who-owns-what)): the power key, the screen's light, the battery level, Charge Limit, USB modes and Standby, with no `demo` ([vamp-os-base](vamp-os-base.md)). Heat, the jack, the DAC's power, the MCU, the screen's idle timeout and the settings store are later slices.

| What | Where |
| --- | --- |
| Source | `sysd/q2sysd.c` (one file, one `poll` loop), `sysd/Makefile` |
| In the image | `/usr/bin/q2sysd`, the `q2sysd` service (`os/package/q2sysd`, `/etc/sv/q2sysd`) |
| Tests | `test/sysd.py` (this machine, a stand-in `/sys` and `/dev/input`), `test/os_image.py`'s boot (the image's own under QEMU) |

## What it does

| Duty | How | Known from |
| --- | --- | --- |
| **The power key** | Every `/dev/input/event*` whose keys include `KEY_POWER` (116) is watched: `md-gpio-keys`' power button (`PE31`), the AXP2101's key (`axp2101-pek`), and `hyn_ts`, which reports the touchscreen's double tap as `KEY_POWER` while its gesture mode is on. A press shorter than 3 s turns the screen off or on; held 3 s it powers off (`/sbin/poweroff`: init runs `rcK`, which stops the services and unmounts the card and `/mnt/data`, then powers off). The release after a power off, and a release with no press, do nothing | read: [hardware](hardware.md), `module_driver/keyboard_gpio_add.sh`, [internals: Screen off and wake](internals.md#screen-off-and-wake) |
| **The screen's light** | Off: the backlight's `bl_power` 4 (`FB_BLANK_POWERDOWN`) and `/sys/hynitron_debug/hyngescontrol` 1 (gestures on, as stock's `double_onclick_wake` writes as the screen goes off); on: `bl_power` 0 and gestures off. The backlight is the first device in `/sys/class/backlight`, the one `q2guard` and `q2ui` read: `q2ui` takes `bl_power` not 0 as a dark screen and drops input then (`ui/ui.c` `dark`), so a press that lights the screen is nothing else | read: `patch/q2guard.c`, `ui/ui.c`, [internals](internals.md#screen-off-and-wake) |
| **Brightness** | `brightness P` (0 to 100) sets the backlight's `brightness` to P of its `max_brightness`, linearly | inferred: stock's own scale (`toolSetBackLight`, `SYSSET LIGHTNESS`) is not read |
| **The battery** | Every 30 s and at each change of the screen: the CW2015's `cw2015_capacity` and `cw2015_voltage` (`/sys/devices/i2c-0/0-0062/`), `/sys/class/power_supply/battery/status` and `ac/online`; on a change written whole to `/run/vamp/battery` (`level`, `mv`, `status`, `cable`, a `key=value` line each) and told to watchers | read: [hardware: Battery reporting](hardware.md), `patch/q2guard.c` (the same files, logged on a Q2) |

| **Charge Limit** | `Q2POD CHARGELIMIT=1` in `config.ini`, the payload's setting: at each battery read, charging is held off at 80% and handed back at 75% (`CHARGE_STOP`, `CHARGE_RESUME`) through the charger's active-low enable line `PE22`, driven as the kernel's GPIO 150 (`/sys/class/gpio`, exported once, `1` holds it off). Off, the line is never touched | read: [internals: Charge Limit](internals.md#charge-limit) |
| **USB modes** | `usb storage`: the player stopped (Q2PLAY/1 `stop`), the card synced and unmounted (refused while it is busy, nothing else changed), adb's gadget parked (`q2adb-gadget park`), and a mass-storage gadget made as stock's `storage_config.sh` makes one (`0525:a4a5`, one LUN, the card's block device, writable, removable) and bound to the controller. `usb adb`: the gadget unbound and removed, the card mounted again, adb's gadget back (`unpark`). Not chosen on its own at a cable: that is the UI's | read: stock's `storage_config.sh`, [adb: The USB gadget](adb.md#the-usb-gadget) |
| **Standby** | `standby`: the screen off, Q2PLAY/1 `pause`, Q2NET/1 `off` (not on a card boot, whose Wi-Fi is the developer's way in), `sync`, then `mem` written to `/sys/power/state`, which returns when a wake source (the power key) has resumed the Q2; then the screen on and Q2NET/1 `wake`. A refused suspend (the kernel's error) still brings the screen back | inferred: the Linux interface; stock's own order is [internals: Screen off and wake](internals.md#screen-off-and-wake)'s |

## Q2SYS/1

On `/run/vamp/sys.sock` (mode 0600), the same shape as [Q2PLAY/1](playd.md#q2play1): the client's first line `Q2SYS/1`, answered `Q2SYS/1 ok` (anything else: `err version`, closed); then `screen` (`screen on` or `screen off`), `screen on|off|toggle`, `brightness P`, `usb` (`usb adb` or `usb storage`), `usb adb|storage`, `standby` (answered `ok` before the suspend), `battery` (`battery level=73 mv=3912 status=Discharging cable=0`; `status` is the kernel's text, percent-encoded as Q2PLAY/1's free-text fields are ([Fields](playd.md#q2play1)), so `Not charging` is `Not%20charging`; the others are numbers), each `ok` or `err ...`, and `watch`, after which `screen on`, `screen off`, `battery ...`, `charge held level=N`, `charge allowed level=N`, `usb adb`, `usb storage`, `standby`, `resumed` and `power off` lines come as they happen. `q2ui` does not use it yet; a Settings Brightness row and a status row's battery would.

## Tests

`python3 test/sysd.py` (pytest) builds `q2sysd` for this machine and runs it on a stand-in tree (a FIFO for an input device, files for `/sys`, `--poweroff` a command that leaves a file): a short press off and on with the gesture file following, a hold powering off at 3 s and not before and leaving the screen alone, other keys and a lone release doing nothing, the battery file, and Q2SYS/1 (the screen, brightness and its range, a fresh battery read and a status with a space in it, a watcher hearing a press, another version refused). It also checks Charge Limit (held at 81, still held at 76, allowed at 75; the line never touched with the limit off), USB storage (the player's stop, then `sync`, `umount`, `park`, the gadget's LUN and controller, and back: `mount`, `unpark`, the controller cleared; a busy card refused with nothing parked) and Standby (the screen, the pause, Wi-Fi off and woken, `mem` written; on a card boot Wi-Fi untouched), against stand-ins for the other services' sockets and the system's commands. Copies with a 1 s hold, without the gesture write, taking a release with no press, taking any key, with the charge line's sense inverted, without the 75% hysteresis, ignoring a busy card, not stopping the player, or turning a card boot's Wi-Fi off each fail it (`Q2SYSD_SOURCE`). `test/os_image.py`'s boot runs the image's own: the battery file from stand-in CW2015 files, a press on an input device of its own turning the screen off and on, and a hold starting init's shutdown (`rcK`'s line in the boot log); `--perturb sysd-off` fails those.

## Needs a Q2

- Whether `PE22` can be driven through the kernel's GPIO class at all (stock drives it through `soc_gpio.ko`'s own ioctl, bypassing gpiolib), and the AXP2101's charging stopping when it is high.
- The storage gadget on the real controller beside q2adb's park, a computer seeing the card, and the card mounting again after.
- `mem` suspend under Vamp OS: what the drivers do with our services running, which wake sources the kernel arms (the power key, a cable: [usb: Standby](usb.md#standby-limit)), and the current drawn.

- Which input devices carry the power button (watching every device with `KEY_POWER` covers both candidates), and whether one press arrives on two of them, which would turn the screen off and on again at once. ponytail: a press seen on two devices within a few milliseconds would then have to count once.
- Whether `bl_power` 4 turns this panel's light off at all, or stock blanks the framebuffer (`FBIOBLANK`) or powers the panel down (`lcd_h0209s`) instead; the brightness scale; and the double tap waking a dark screen through `hyngescontrol`.
- Whether `poweroff` reaches the AXP2101 and the Q2 switches off, and how long the PMIC's own long press takes to cut power (above 3 s, or this hold is never seen).
- The battery's `status` and `ac/online` names are those `q2guard` logs on a Q2; the level's accuracy at the ends of the range is the CW2015's.
