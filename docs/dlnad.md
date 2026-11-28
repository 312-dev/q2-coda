# q2dlnad

VampOS's DLNA renderer ([services: DLNA/UPnP renderer](vamp-os-services.md#dlnaupnp-renderer)): a UPnP AV MediaRenderer:1 that a control point on the network (BubbleUPnP on a phone, a media server's "play to", Windows' "Cast to Device") finds and plays to. On today's Vamp, stock's renderer inside `demo` does this (`libdmr.so`, [third-party](third-party.md)); in VampOS `q2dlnad` is a Q2PLAY/1 client like any app, and what it plays is a [source](platform/playback.md#audio-focus): the Library's queue is put aside and given back.

| What | Where |
| --- | --- |
| Source | `dlnad/q2dlnad.c` (one file, one thread, one `poll` loop, no library), `dlnad/Makefile` |
| In the image | `/usr/bin/q2dlnad`, the `q2dlnad` service (`os/package/q2dlnad`, `/etc/sv/q2dlnad`) |
| Tests | `test/dlnad.py` (this machine, stand-ins for Q2PLAY/1 and Q2NET/1, and once the real `q2playd`) |
| As a package | `kind=service`, `permissions=playback,network-hold,background` ([Packages](platform/packages.md#permissions)): built into the image until the store runs packages |

## On and off

It listens only while `[SYSSET] DLNA=1` in `/mnt/data/config.ini`, stock's own switch (stock ships it `0`, [third-party](third-party.md)), read every 5 s: off, it binds no port and holds nothing; turned off while it plays, it sends `ssdp:byebye`, drops its subscribers and gives the player back. The service itself always runs, so the switch needs no supervisor command.

## On the network

| Port | What |
| --- | --- |
| UDP 1900, group `239.255.255.250` | SSDP ([UPnP Device Architecture 1.0](https://openconnectivity.org/upnp-specs/UPnP-arch-DeviceArchitecture-v1.0.pdf), section 1): an `M-SEARCH` with `MAN: "ssdp:discover"` for `ssdp:all`, `upnp:rootdevice`, its UUID, `MediaRenderer:1` or one of its three services is answered to the sender, one answer a match; `ssdp:alive` for all five at start and every 600 s (`max-age=1800`), `ssdp:byebye` when it turns off |
| TCP 49494 | HTTP: `/description.xml`, the three SCPDs (`/AVTransport.xml`, `/RenderingControl.xml`, `/ConnectionManager.xml`), SOAP at `/ctl/<service>`, GENA at `/evt/<service>`; one request a connection, 16 KB at most, 8 at once, 5 s to send it |

The UUID is `uuid:0a2f2a3e-5661-6d70-0000-` and `wlan0`'s MAC, so a control point sees the same renderer every boot with nothing stored; its name is `Vamp Q2`. Each `LOCATION` names the address a reply to that asker leaves from.

## What each action does

| Action | Q2PLAY/1 | The transport after |
| --- | --- | --- |
| `SetAVTransportURI` | none; while playing, `source` at once with the new URL | `STOPPED` (`NO_MEDIA_PRESENT` for an empty URI), or still playing |
| `Play` | `source URL title= artist= album=` from the DIDL-Lite (`dc:title`, `upnp:artist` or else `dc:creator`, `upnp:album`); `resume` when it holds the player paused | `TRANSITIONING`, then `PLAYING` at the source's `ok` |
| `Pause` | `pause` (fault 701 unless it plays) | `PAUSED_PLAYBACK` once the `now` line says so |
| `Stop` | `focus release` | `STOPPED`; the queue comes back paused |
| `Seek` | `seek S` for `REL_TIME` or `ABS_TIME` (`H:MM:SS`, a fraction dropped); 710 for another unit, 711 for a bad time | |
| `Next`, `Previous` | fault 701: a renderer here holds one URL | |
| `GetTransportInfo`, `GetPositionInfo`, `GetMediaInfo`, `GetCurrentTransportActions`, `GetDeviceCapabilities`, `GetTransportSettings` | none: the state, and the position and length from the watch's `now` lines | |
| `SetVolume`, `GetVolume` | `volume V` (0 to 100, 402 outside), the watch's `volume` | |
| `SetMute`, `GetMute` | `volume 0`, and the level before it set again on unmute (Q2PLAY/1 has no mute); a `SetVolume` while muted is kept for the unmute | |
| `ListPresets`, `SelectPreset` | none: `FactoryDefaults` only | |
| `GetProtocolInfo`, `GetCurrentConnectionIDs`, `GetCurrentConnectionInfo` | none: the formats the player plays as `http-get` sink entries, connection 0 | |

A `focus lost` line (the URL played out, did not open, or another source or a `play` took the player) is a `STOPPED` transport; the next `Play` asks for a source again. A URL whose `source` line would pass Q2PLAY/1's 512 bytes is not sent (logged), the transport `STOPPED`.

## Events

GENA as UDA 1.0 section 4 has it: `SUBSCRIBE` with `CALLBACK: <http://...>` and `NT: upnp:event` answers a `SID` and `TIMEOUT` (at most 1800 s, the asked-for time otherwise), then sends the first event (`SEQ: 0`) once that answer is out; a `SUBSCRIBE` with the `SID` renews, `UNSUBSCRIBE` ends it (412 for an unknown one). AVTransport's and RenderingControl's events are `LastChange` (`TransportState`, `TransportStatus`, the URIs and metadata, `CurrentTrackDuration`, `CurrentTransportActions`; `Volume` and `Mute` on `Master`), sent to every subscriber at each change; ConnectionManager's three variables are sent once. Eight subscriptions at most; each `NOTIFY` waits a second at most for its callback, on the one thread.

## Decisions

- **A source, not a queue.** `SetNextAVTransportURI` (AVTransport:2's gapless next) is not offered; a control point that wants a playlist sends each URI when the last ends, which every common one does.
- **A cast comes back to a paused Library**, never playing on its own ([q2playd: Sources](playd.md#sources-and-audio-focus)).
- **`https://` URLs are played too**: Q2PLAY/1 takes both, and `q2player` checks the chain against the image's CA file.

ponytail: SSDP answers and `NOTIFY` are sent at once rather than after a random wait up to `MX` (UDA's advice for crowded networks), and a new address from DHCP waits for the next 600 s `ssdp:alive`; a home network with one Q2 is the case served. Lift: a timer per answer, and a Q2NET/1 `watch` to announce on `connected`.

## Tests

`python3 test/dlnad.py` (pytest) builds it for this machine and runs it under a temporary root with stand-ins for Q2PLAY/1 and Q2NET/1: off with `DLNA=0` and on and off again as the file changes; a search for a renderer answered with its `LOCATION`, `USN`, `EXT` and `max-age`, `ssdp:all` answered with the five targets, a server search and an unquoted `MAN` not answered; the description's type, UDN and name and each SCPD holding UPnP AV 1's required actions with every argument's state variable declared; a URI with escaped DIDL-Lite and `Play` sent as `source` byte for byte (the URL's `=` and the title's space encoded, the double-escaped entity kept), the transport following `PLAYING`, `PAUSED_PLAYBACK`, the position and length, `resume`, an hour-long `seek`, the faults for another seek unit and `Next`, and `Stop`'s `focus release`; `focus lost` stopping it; a new URI while playing played at once; the volume, a refused 101, the mute keeping the level; the radio held once it plays and not before; a subscriber's first event and its `SEQ` counting up, a renewal and the unsubscribed SID refused. With the real `q2playd` and its stand-in player, the URL reaches the player as `set-songurl` and the song's end stops the transport. Copies with no Q2PLAY/1 encoding of `=`, no unescaping of the metadata, no `MAN` check, `focus lost` ignored, the mute losing the level, no `SEQ` count, no first event, no hold, the setting ignored, no MAC in the UUID, a state variable missing from an SCPD, no `resume` from paused, or the hours of a seek dropped each fail it (`Q2DLNAD_SOURCE`). `test/os_image.py` checks the image's service list holds it.

## Needs a Q2

Whether the Q2's `wlan0` driver delivers multicast to a socket that joined the group with no `demo` (stock's renderer worked, so the driver can); a real control point (BubbleUPnP, a Windows "Cast to Device", a MinimServer push) against it; and the 16 KB request limit against the largest DIDL-Lite a server sends.
