# Q2SYS/1: the system

`q2sysd`'s socket, `/run/vamp/sys.sock` ([q2sysd](../sysd.md)), in [the common shape](README.md#the-common-shape): the screen, the battery, the USB mode and standby.

| Command | Answer |
| --- | --- |
| `screen` | `screen on` or `screen off` |
| `screen on`, `screen off`, `screen toggle` | `ok` |
| `brightness P` | `ok` for 0 to 100 percent, else `err brightness` |
| `battery` | `battery level=PERCENT mv=MILLIVOLTS status=TEXT cable=0\|1`; `status` is the kernel's text, percent-encoded (`Not%20charging`) |
| `usb` | `usb adb` or `usb storage` |
| `usb adb`, `usb storage` | `ok`, or `err ...` (a card that is busy is not shared) |
| `standby` | `ok`, before the Q2 suspends |
| `watch` | `ok`; then `screen on`, `screen off`, `battery ...`, `charge held level=N`, `charge allowed level=N`, `usb adb`, `usb storage`, `standby`, `resumed` and `power off` lines as they happen |
| `caps` | `caps screen brightness usb standby battery watch` |

```conformance
> Q2SYS/1
< Q2SYS/1 ok
> caps
< caps screen brightness usb standby battery watch
> screen
< screen on
> screen off
< ok
> screen
< screen off
> screen toggle
< ok
> screen on
< ok
> brightness 50
< ok
> brightness 101
< err brightness
> battery
< battery level=73 mv=3912 status=Discharging cable=0
> usb
< usb adb
> usb adb
< ok
> watch
< ok
> standby
<* ~ standby|screen off
< ok
```

```conformance
> Q2SYS/2
< err version
```
