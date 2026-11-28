# Q2RADIO/1: the radio tuner

**Status: a design, not served.** Radio is Home's row under the Library in today's Vamp ([internals: Radio (Home)](../internals.md#radio-home)): the FM stations that reach the Q2's place, laid out at their real frequencies on a dial, each played from its internet stream through a simulated FM receiver ([internals: Radio static](../internals.md#radio-static)). This page is what `q2tuner`, VampOS's radio ([roadmap: Scope](../vamp-os.md#scope)), would serve and what `q2ui`'s Radio page asks. Stock's Internet Radio and today's Vamp's directory browser on the Streaming row (`patch/radio.c`, radio-browser.info) are not carried ([roadmap: What is dropped](../vamp-os.md#scope)); a `favourites.m3u` the owner keeps in `/mnt/mmc/Radio/` stays on the card untouched.

## The socket

`/run/vamp/radio.sock`, in [the common shape](README.md#the-common-shape): the client's first line `Q2RADIO/1`, answered `Q2RADIO/1 ok`. Permission (planned): `playback` ([Packages: Permissions](packages.md#permissions)), as the tuner is a playback source.

**What `q2tuner` owns.** The station list: it runs `q2radiofetch` (`patch/radiofetch.c`, a program already, [internals: Local radio fetch](../internals.md#local-radio-fetch)) with `--out` and, when the owner chose one, `--zip`, and keeps its FM rows (64 at most, 87.9 to 107.9 MHz; AM stays in the data and off the dial, as today). The dial's place, saved where today's Vamp saves it (`/mnt/mmc/Radio/.local/freq` and `.local/zip`), so the owner finds the station they left in either system. The receiver (`patch/fmrx.c`, `patch/tuner_core.c`): the hiss between stations, a station swelling out of the noise, a dead stream started again after 1, 2, 4 and 8 s and then every 15 s. It takes the player as a [Q2PLAY/1 audio focus](playback.md#audio-focus) holder, so the Library's Play takes it back and `focus lost` ends it.

**What `q2ui` owns.** Every drawing and every input rule: the fine steps, the detents and their hold, the fast spin, the Centre tap's volume band and hold's list, the words' fade-in, the "No Wi-Fi" toast (from [Q2NET/1](network.md)), and the dial's click, which is the Clicker's (`click wheel` on Q2PLAY/1, [q2playd: Key Tone](../playd.md#key-tone)). The tuner is told where the needle is; it never steps the dial itself, except `seek`.

## Commands

Frequencies are in 10 kHz, as today's `rt->freq` (9990 is 99.9 MHz), so a fine step of 0.02 MHz is 2.

| Command | Answer |
| --- | --- |
| `caps` | `caps status stations place refresh tune seek pause resume stop watch` |
| `status` | `status state=S freq=F call=C name=N city=T title=X zip=Z near=T` (below) |
| `stations` | a `station freq=F band=FM call=C name=N city=T km=K db=D stream=0\|1` line each, in frequency order, then `ok`; `err none` before the first list has come. `name` is the call sign when no source named it; `stream` 0 for a station no source gave a stream (the needle on it hears hiss) |
| `place zip Z` | `ok`: Z (five digits) saved, the list fetched again for it; `err zip` for anything else |
| `place here` | `ok`: the saved ZIP cleared, the list fetched again for where the network says the Q2 is |
| `refresh` | `ok`: the list fetched again; the cached one stays until the new one comes |
| `tune F` | `ok`, the needle at F (saved): the station within 0.04 MHz of F plays (its stream connects, then sounds), anywhere else is hiss; `err tune` outside 8790 to 10790. The first `tune` starts the receiver and takes the player |
| `seek up`, `seek down` | `tuned freq=F`, the next station up or down with a stream, round the band's ends; `err none` with no such station |
| `pause`, `resume` | `ok`: the receiver silent, its stream kept; or sounding again |
| `stop` | `ok`: the receiver ended and the player given back (Q2PLAY/1's `focus release`) |
| `watch` | `ok`; then `status ...` whenever a field changes, and `list state=loading\|ready\|failed stations=N why=W` as a fetch starts and ends |

**`state`**: `idle` (no receiver), `loading` (no list yet), `hiss` (the needle on no station, or on one with no stream), `connecting`, `playing` (the station's sound), `stalled` (its stream ran dry: the carrier drops, as a lost signal), `paused`, or `error` (a stream that will not open: it is tried again as above while the needle stays). `call`, `name` and `city` are the station the needle is on, empty on none; `title` is the song as the stream names it (iHeart's `title=..,artist=..` made "Artist - Song" as `tuner_title` does), empty until it comes. `zip` is the saved ZIP, empty for `place here`; `near` the nearest station's city of licence (the status row's "Near ..."). The UI holds the words back until `playing` and a title, or 4 s of `playing` with none, as today.

**`why`** on a failed fetch: `offline` (no network: the UI's "No internet connection"), `place` (no location found and no ZIP), `empty` (nothing reaches the place).

## Transcript

The stand-ins a suite would use: a list of two stations, 99.9 `WXYZ` (Example 99, Springfield, a stream) and 101.1 `WABC` (no stream), the ZIP 12345.

```text
> Q2RADIO/1
< Q2RADIO/1 ok
> caps
< caps status stations place refresh tune seek pause resume stop watch
> watch
< ok
> stations
< station freq=9990 band=FM call=WXYZ name=Example%2099 city=Springfield km=12 db=55 stream=1
< station freq=10110 band=FM call=WABC name=WABC city=Springfield km=30 db=41 stream=0
< ok
> tune 9992
< ok
< status state=connecting freq=9992 call=WXYZ name=Example%2099 city=Springfield title= zip=12345 near=Springfield
< status state=playing freq=9992 call=WXYZ name=Example%2099 city=Springfield title= zip=12345 near=Springfield
< status state=playing freq=9992 call=WXYZ name=Example%2099 city=Springfield title=Ann%20-%20Alpha zip=12345 near=Springfield
> seek up
< tuned freq=9990
< status state=playing freq=9990 call=WXYZ name=Example%2099 city=Springfield title=Ann%20-%20Alpha zip=12345 near=Springfield
> tune 10050
< ok
< status state=hiss freq=10050 call= name= city= title= zip=12345 near=Springfield
> place zip 1234
< err zip
> place zip 54321
< ok
< list state=loading stations=2 why=
< list state=ready stations=2 why=
> stop
< ok
< status state=idle freq=10050 call= name= city= title= zip=54321 near=Springfield
```

(`seek up` from 99.92 wraps past 101.1, which has no stream, round the top of the band to 99.9.)

## What it takes

- **The receiver's sound.** Today `q2video -t` plays `fmrx.c`'s output on ALSA itself. In VampOS `q2player` holds the DAC, so either `q2player` takes a PCM input from `q2tuner` (a FIFO source beside its URL sources) or `q2tuner` opens the device while it holds Q2PLAY/1's focus and `q2player` has let it go. The contract above is the same either way.
- **The song title.** `q2player` does not read a stream's ICY metadata (the `StreamTitle` today's `q2video` parses from ffmpeg); until it does, `title` stays empty and the words appear after 4 s of sound, as today for a station with no title.
- **The package.** `q2radiofetch` and `fmrx.c` build for the image as they are (plain C, libcurl and the CA file already in it); the FCC and Census tables (`assets/radio/`) go with it.
