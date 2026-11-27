# Q2BT/1: Bluetooth

`q2btd`'s socket, `/run/vamp/bt.sock` ([q2btd](../btd.md)), in [the common shape](README.md#the-common-shape): the radio, scanning, pairing, the paired devices, the connected headset's codec and battery. Where the music goes follows from it: while a headset's A2DP stream is up `q2btd` asks `q2playd` for `output bluetooth` ([Q2PLAY/1](playback.md)), and when it goes, `output dac`. Addresses are `AA:BB:CC:DD:EE:FF`; names are percent-encoded.

| Command | Answer |
| --- | --- |
| `status` | `status power=0\|1 state=absent\|off\|on\|scanning\|connecting\|connected device=ADDR name=... kind=KIND codec=CODEC battery=PERCENT output=dac\|bluetooth`; `device`, `name`, `kind` and `codec` are empty and `battery` -1 with no headset connected, `battery` -1 too while the headset has sent none. `absent` is no adapter (the radio not attached) |
| `power on`, `power off` | `ok` once BlueZ has it, `err power`, `err no adapter`; the choice is kept across boots |
| `scan` | `ok` once discovery runs, `err power` with the radio off: 60 s of discovery, the Q2 discoverable for as long; each device heard is a `found` line to watchers |
| `devices` | a `device addr=ADDR name=... kind=KIND paired=0\|1 connected=0\|1 battery=PERCENT` line for each device BlueZ knows (the paired ones, and those a scan heard), and `ok` |
| `pair ADDR` | `ok` once paired (trusted, then connected to), `err pair`, `err no such device`, `err busy` (another pairing runs) |
| `connect ADDR` | `ok` once connected, `err connect`, `err not paired`, `err no such device` |
| `disconnect`, `disconnect ADDR` | `ok`, `err not connected`, `err disconnect`. A disconnect asked for is not reconnected |
| `forget ADDR` | `ok`, `err forget`, `err no such device`: the pairing removed |
| `codec` | `codec name=CODEC rate=HZ available=CODEC,CODEC,... choice=auto\|CODEC` |
| `codec CODEC`, `codec auto` | `ok`, or `err codec` for one the headset does not offer; kept, and asked for at each connection of a headset that offers it. `auto` is bluealsa's own choice |
| `watch` | `ok`; then `status ...` lines as they change, `found addr=ADDR name=... kind=KIND` for a device a scan hears, `pair-request addr=ADDR passkey=NNNNNN answer=yes\|no` for each pairing question the agent answered |
| `caps` | `caps status power scan devices pair connect disconnect forget codec watch` |

**`kind`**, from the device's Class of Device: `headphones`, `headset`, `speaker`, `car`, `audio` (another audio device), `phone`, `computer`, `other`. **Codecs** are bluealsa's names: `SBC`, `aptX`, `aptX-HD`, `LDAC` (and `AAC` in an image built with it, [vamp-os-base: Bluetooth](../vamp-os-base.md#bluetooth)).

**Pairing questions.** Until the notice design is served, nothing asks the user: the agent says yes to the device `pair` names, and to a device pairing with the Q2 during a `scan`'s 60 s, and no to any other ([q2btd: The agent](../btd.md#the-agent)). The `pair-request` line says what it answered.

The transcripts' stand-in (`test/btd.py`): a pair of headphones, `Fixture Buds` (`AA:BB:CC:DD:EE:01`, paired, the last device, battery 80 %, offering SBC, aptX and LDAC), connected by the auto-reconnect before the first line, and a speaker a scan hears, `Fixture Speaker` (`AA:BB:CC:DD:EE:02`).

```conformance
> Q2BT/1
< Q2BT/1 ok
> caps
< caps status power scan devices pair connect disconnect forget codec watch
> status
< status power=1 state=connected device=AA:BB:CC:DD:EE:01 name=Fixture%20Buds kind=headphones codec=SBC battery=80 output=bluetooth
> devices
< device addr=AA:BB:CC:DD:EE:01 name=Fixture%20Buds kind=headphones paired=1 connected=1 battery=80
< ok
> codec
< codec name=SBC rate=48000 available=SBC,aptX,LDAC choice=auto
> codec LDAC
< ok
> codec AAC
< err codec
> connect AA:BB:CC:DD:EE:01
< ok
> connect AA:BB:CC:DD:EE:09
< err no such device
> watch
< ok
> disconnect
<* ~ status .*
< ok
> disconnect
<* ~ status .*
< err not connected
```

```conformance
> Q2BT/1
< Q2BT/1 ok
> scan
< ok
> pair AA:BB:CC:DD:EE:02
< ok
> forget AA:BB:CC:DD:EE:02
< ok
> forget AA:BB:CC:DD:EE:02
< err no such device
```

```conformance
> Q2BT/1
< Q2BT/1 ok
> power off
< ok
> status
< ~ status power=0 state=off .*
> scan
< err power
> power on
< ok
```

```conformance
> Q2BT/2
< err version
```
