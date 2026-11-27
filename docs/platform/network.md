# Q2NET/1: the network

`q2netd`'s socket, `/run/vamp/net.sock` ([q2netd](../netd.md)), in [the common shape](README.md#the-common-shape): Wi-Fi's state, the saved networks, and holding the radio up (Smart Wi-Fi). SSIDs and passphrases are percent-encoded both ways.

| Command | Answer |
| --- | --- |
| `status` | `status state=off\|searching\|scanning\|associated\|connected ssid=... ip=... smart=0\|1 held=SECONDS` |
| `scan` | the radio on (a 5 minute hold), then an `ap ssid=... signal=DBM secure=0\|1` line for each network heard, and `ok`; with the radio just on it first waits up to 5 s for `wpa_supplicant`, the connection's later lines answered after; `err scan` when it does not answer |
| `networks` | a `network ssid=...` line for each saved network, and `ok` |
| `add SSID PASSPHRASE`, `add SSID` (an open network) | `ok`, `err saved already`, `err network` |
| `forget SSID` | `ok`, `err no such network` |
| `wake` | `ok`: the radio up, held 30 minutes as at a wake |
| `hold S` | `ok`: held up S seconds more |
| `off` | `ok`: the radio off now |
| `smart on`, `smart off` | `ok`: Smart Wi-Fi's turning off when nothing holds it, or the radio kept up |
| `watch` | `ok`; then `status ...` and `radio on`, `radio off` lines as they change |
| `caps` | `caps status scan networks add forget wake hold off smart watch` |

```conformance
> Q2NET/1
< Q2NET/1 ok
> caps
< caps status scan networks add forget wake hold off smart watch
> networks
< network ssid=Home
< ok
> add Caf%C3%A9%20Two secret12
< ok
> add Home other123
< err saved already
> forget Caf%C3%A9%20Two
< ok
> forget Nowhere
< err no such network
> status
< ~ status state=\S+ ssid=\S* ip=\S* smart=1 held=\d+
> smart off
< ok
> smart on
< ok
> hold 60
< ok
> wake
< ok
> scan
< ~ ap ssid=\S+ signal=-?\d+ secure=[01]
<* ~ ap ssid=\S+ signal=-?\d+ secure=[01]
< ok
> watch
< ok
> off
<* ~ radio off|status .*
< ok
```

```conformance
> Q2NET/2
< err version
```
