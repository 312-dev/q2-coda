# Vamp OS: the remaining services

Design for five features the owner kept in Vamp OS's scope ([roadmap: Scope](vamp-os.md#scope); the owner's ruling of 2026-11-26): DLNA/UPnP, scrobbling, LocalSend, the AirPlay sender and Guest Play. Each is today a piece of `demo`'s payload, a helper or a card app; in Vamp OS each is a service under the supervisor, a client of the [Vamp API 1](platform/README.md) sockets, with the permissions a package of its kind would declare ([Packages](platform/packages.md#permissions)). The order below is the build order: what needs the least that is missing comes first.

| Feature | Today | Vamp OS | Needs first | Fits now |
| --- | --- | --- | --- | --- |
| Scrobbling | `patch/nav/playback_power.c` (the listen), `patch/scrobble.c` (the upload, demo's libcurl) | `q2scrobd`: a Q2PLAY/1 watcher writing the same `.scrobbler.log`; the upload a second step | nothing | **yes: built** ([below](#scrobbling)) |
| LocalSend | `patch/localsend_core.c` (protocol), `localsend.c`, `localsend_ui.c` (demo's side) | `q2sendd`: the core as is, the UI's questions as Q2SYS/1-style notices | TLS (`q2-mbedtls` is in the image), a notice and confirmation channel in `q2ui` | the receive side, once `q2ui` can ask |
| AirPlay sender | the `q2-airplay` card app (cliairplay) and the PEQ tap `/tmp/q2cast` | the card app as a `service` package; `q2player` writes the tap, `q2playd` is its host | nothing in this repository: the app's release with an API 1 manifest | **yes: built** ([q2playd: AirPlay](playd.md#airplay)) |
| Guest Play | `q2-guest` (BlueZ D-Bus, a second bluealsa), `patch/guest.c` (pausing the owner's music) | `q2-guest` as a service, its pause and resume through Q2PLAY/1 | Bluetooth from source (BlueZ 5.88, bluez-alsa: `feat/bluez-own`, `feat/bluealsa-own`) | no: M2 |
| DLNA/UPnP renderer | stock's DMR inside `demo` (`dmrAction*`) | `q2dlnad`: a UPnP MediaRenderer (SSDP, the three services' SOAP) handing a URI to `q2playd` as a source | nothing: Q2PLAY/1's `source` and `focus` are built | **yes: built** ([q2dlnad](dlnad.md)) |

## Rules for all five

- **A client, never a peer of the player.** Each reaches the music through Q2PLAY/1 alone (`watch` for what plays, the transport verbs), the network through Q2NET/1 (`hold S` while it works: Smart Wi-Fi turns the radio off otherwise), and never opens the DAC, the framebuffer or evdev.
- **Asked, not assumed.** A service that needs the owner's answer (LocalSend's accept, Guest Play's who-may) asks through `q2ui`; until `q2ui` has a notice and confirmation channel ([roadmap: Power, notices](vamp-os.md#the-api)) those services do not run.
- **Its own state, its own folder.** Settings and accounts stay where today's Vamp keeps them (`.scrobble.ini`, `.localsend/`), read in place, so a switch either way loses nothing ([roadmap: User data](vamp-os.md#user-data)).
- **Off by default where it listens on the network.** LocalSend and DLNA bind ports; each starts only when its setting is on, as today.

## Scrobbling

`q2scrobd` (`scrobd/q2scrobd.c`, the `q2scrobd` service) keeps the listen log today's Vamp keeps, from Q2PLAY/1 instead of demo's queue:

- It subscribes (`watch`) and follows each `now` line: a song is its `id`; the seconds heard count while `state=1` and `pos` moves forward by 1 to 3 s at a time (a seek or a pause adds nothing), as `playback_power.c` counts them.
- **A listen** is half the song or 240 s, whichever is less, for a song over 30 s (`LISTEN_MIN_S`, `LISTEN_MAX_S`); with `[SCROBBLE] PERCENT=N` in `/mnt/mmc/.scrobble.ini`, N % of it instead. One a play: a song started again is a new play.
- **The line** is today's: Rockbox's AudioScrobbler 1.1 format in `/mnt/mmc/.scrobbler.log`, the header (`#AUDIOSCROBBLER/1.1`, `#TZ/UTC`, `#CLIENT/Vamp`) first in a new file; artist, album, title, the track number (empty for 0), the length, `L`, the start (now less the seconds heard) and an empty MusicBrainz id, tab-separated, tabs and line ends in tags made spaces. A song with no artist, or a clock before 2020, is not logged. The track number and the album artist (`[SCROBBLE] ALBUM_ARTIST=1`) come from the library row (`library.db`, Library 1); the tags from the `now` line, percent-decoded.
- **Not yet**: the upload to ListenBrainz and Last.fm (`scrobble.c`'s, which needs libcurl or the image's mbed TLS and Q2NET/1's hold) and the automatic upload's timing. Until then the log is what any `.scrobbler.log` uploader on a computer takes, as today.

Its permissions as a package would be `playback,library,background` (and `network-hold` for the upload).

**Tests.** `python3 test/scrobd.py` (pytest) builds it for this machine and runs it against a stand-in Q2PLAY/1 sending `now` lines: half of a 40 s song logged once, with the header, the library row's track number, the decoded album and a start time the seconds heard back; a 1000 s song at 240 s; a seek and a paused scrub not counted; a 30 s song and an artist-less one not logged; `.scrobble.ini`'s `ALBUM_ARTIST` and `PERCENT`; a song played again logged again. Copies that count a seek, drop the 240 s cap, miss a replay or leave out the track number each fail it (`Q2SCROBD_SOURCE`). `test/os_image.py` checks the image's service list holds it; the emulator's songs end at once (no sound card), so no listen is heard there.

## LocalSend

The protocol core (`patch/localsend_core.c`) is already free of demo; `localsend.c` is demo's glue (Smart Wi-Fi's hold, the accept dialog, the toasts). In Vamp OS: `q2sendd` runs the core, holds the radio over Q2NET/1 while discoverable, and asks `q2ui` to accept each incoming transfer. Files land where today's do. Blocked on `q2ui`'s notice channel; nothing else is missing.

## AirPlay sender

The card app needs a copy of what the Q2 plays. `q2player`'s DSP chain (`player/dsp.c`) already writes it into the shared tap `/tmp/q2cast` while the app beats, as today's Vamp does, so nothing in `player/` changes; `demo`'s half of the tap (the status, the volume, the track's tags, the volume's swap to the speaker) is `q2playd`'s ([q2playd: AirPlay](playd.md#airplay)). The app is a `service` package (`permissions=playback,network-hold,background`) run by `/etc/sv/q2-airplay` once added; its row on the ⋯ page is `q2ui`'s to declare ([extension points](vamp-os.md#extension-points)).

## Guest Play

`q2-guest` already talks to BlueZ and bluealsa over D-Bus and decides on its own; demo's part is pausing the owner's music while a guest plays and resuming it after, which is Q2PLAY/1's `pause` and `resume`. It waits for Bluetooth from source (M2), because Vamp OS will not carry stock's bluetoothd.

## DLNA/UPnP renderer

Stock's renderer lives in demo, so Vamp OS has its own, `q2dlnad` ([q2dlnad](dlnad.md)): SSDP announcements and the MediaRenderer's AVTransport, RenderingControl and ConnectionManager services, written against the UPnP AV specification with no library. A `SetAVTransportURI` and `Play` become a Q2PLAY/1 source ([Audio focus](platform/playback.md#audio-focus)): the URL with its DIDL-Lite title, artist and album, the Library's queue put aside and given back paused. Its permissions as a package would be `playback,network-hold,background`; it listens only while stock's `[SYSSET] DLNA` switch is on.
