# Q2SYS/1: the system

`q2sysd`'s socket, `/run/vamp/sys.sock` ([q2sysd](../sysd.md)), in [the common shape](README.md#the-common-shape): the screen, the battery, the USB mode, standby and the clock.

| Command | Answer |
| --- | --- |
| `screen` | `screen on` or `screen off` |
| `screen on`, `screen off`, `screen toggle` | `ok` |
| `awake` | `ok`: a use, as a key press is, so the screen's off time starts again ([q2sysd: The screen off time](../sysd.md#the-screen-off-time)); for an app that shows something with no key pressed (a video, a game); capability word `awake` |
| `brightness P` | `ok` for 0 to 100 percent, else `err brightness` |
| `battery` | `battery level=PERCENT mv=MILLIVOLTS status=TEXT cable=0\|1`; `status` is the kernel's text, percent-encoded (`Not%20charging`) |
| `usb` | `usb adb`, `usb storage`, `usb dac` or `usb car` |
| `usb adb`, `usb storage` | `ok`, or `err usb` (a card that is busy is not shared) |
| `usb dac` | `ok`, or `err usb`: the Q2 a USB DAC for the computer, the player stopped ([dacloop](../dacloop.md)); capability word `usb-dac` |
| `usb car` | `ok`, or `err usb`: Car Audio, the Q2 shown to the host as an iPhone until q2-iap decides; a car stays (a `car` event), anything else ends in `usb adb` ([Car Audio on Vamp OS](../car-audio-os.md)); capability word `usb-car`. q2sysd also changes the mode itself at a cable in and out ([q2sysd: The cable](../sysd.md#the-cable)), each change told as below |
| `standby` | `ok`, before the Q2 suspends |
| `heat` | `heat STATE`: the overheat guardian's state, `pending`, `ok`, `nosensor`, `warn`, `loadwarn`, `poweroff-hot` or `poweroff-load`, or `off` where it does not run (a card boot: today's Vamp's own guards) ([q2sysd: Heat](../sysd.md#heat)); capability word `heat` |
| `jack` | `jack po=P bal=B`: the 3.5 mm (`po`) and 4.4 mm (`bal`) jacks, 1 plugged, 0 not, -1 not read yet ([q2sysd: The jacks](../sysd.md#the-jacks)); capability word `jack` |
| `time` | `time zone=NAME`: the zone in effect, the zoneinfo name config.ini's `[SYSSET] TIMEZONE` gives, or `none` ([q2sysd: The clock](../sysd.md#the-clock)); capability word `time` |
| `time set SECONDS` | `ok`, or `err time`: the system clock set to SECONDS since 1970 (UTC), then the RTC written, as stock's Set Time does |
| `time save` | `ok`, or `err time`: the system clock written to the RTC (UTC), as stock does after a network sync (`q2netd`'s) |
| `find` | `ok`, or `err find` with no buzzer driver: Find My Vamp's jingle on the internal buzzer, the screen lit, until any key, the wheel or a touch, `find stop` or a minute; again while it plays, its minute starts again ([q2sysd: Find My Vamp](../sysd.md#find-my-vamp)) |
| `find stop` | `ok`: the jingle stopped |
| `find state` | `find 1` while it plays, else `find 0` |
| `heat cancel` | `ok`: the load warning's Cancel, for a UI that does not serve Q2UI/1's `ask` |
| `watch` | `ok`; then `screen on`, `screen off`, `battery ...`, `charge held level=N`, `charge allowed level=N`, `usb adb`, `usb storage`, `usb dac`, `usb car`, `car`, `usb ask` (a cable in on Ask Every Time: the chooser's to draw), `standby`, `resumed`, `charge wait` (Auto Power Off with a cable in: the Q2 stays on until the cable is out or the battery is full, [q2sysd](../sysd.md#auto-standby-and-auto-power-off); the charge screen is the UI's to draw), `jack po 1`, `jack po 0`, `jack bal 1`, `jack bal 0` (a plug, an unplug that lasted), `heat warn`, `heat nosensor`, `heat ok`, `heat loadwarn left=S`, `heat cancelled`, `heat off` (the guardian's events: the music paused, the power off 10 s later), `time zone=NAME` (the zone changed) and `time set` (the clock set by hand), `lowbattery warn` (under 16%, once a discharge) and `lowbattery off` (the power off that follows five low readings: [q2sysd: A low battery](../sysd.md#a-low-battery)), `find on` and `find off` (the jingle starting and ending), `power off` and `reboot` lines as they happen |
| `caps` | `caps screen awake brightness usb usb-dac usb-car standby heat jack battery time find watch` |

```conformance
> Q2SYS/1
< Q2SYS/1 ok
> caps
< caps screen awake brightness usb usb-dac usb-car standby heat jack battery time find watch
> screen
< screen on
> find state
< find 0
> find stop
< ok
> awake
< ok
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
> usb dac
< ~ ok|err usb
> usb adb
< ok
> usb car
< ~ ok|err usb
> usb adb
< ok
> heat
< ~ heat (pending|ok|nosensor|off)
> jack
< ~ jack po=-?[01] bal=-?[01]
> heat cancel
< ok
> time
< ~ time zone=[A-Za-z0-9_+/-]+
> time save
< ~ ok|err time
> time set 1767225600
<* ~ time set
< ~ ok|err time
> time set 12x
< err time
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
