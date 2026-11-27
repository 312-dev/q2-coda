# Q2PLAY/1: playback

`q2playd`'s socket, `/run/vamp/play.sock` ([q2playd](../playd.md)), in [the common shape](README.md#the-common-shape). What plays, the queue and the volume; what it sends the player for each is in [q2playd: Commands](../playd.md#commands).

| Command | Answer |
| --- | --- |
| `play AT SHUFFLE N`, then N lines of one song id each | `ok` once the N ids are read (`err play` for an N outside 1 to 1000 or an AT outside the list). A new queue from the song at AT; SHUFFLE 1 plays that one first and the rest in a random order, each once |
| `toggle`, `pause`, `resume` | `ok` |
| `next`, `previous` | `ok`; `previous` more than 3 s into a song starts it again |
| `seek S` | `ok`, to S seconds |
| `volume V` | `ok` for 0 to 100 (capped at the Q2's Max Volume, saved), else `err volume V` |
| `stop` | `ok`; nothing plays |
| `now` | a `now` line |
| `watch` | `ok`; then a `now` line at each change, at most twice a second |
| `source URL [title=T] [artist=A] [album=B]` | `ok`, or `err source` for a URL that is not `http://` or `https://` or holds a byte below `0x21`, `\|` or `0x7f` once decoded. Plays the URL with [audio focus](#audio-focus), the queue put aside; the URL and the fields are free text, percent-encoded (a URL's `=` and `%` too), a field this service does not know passed over |
| `focus` | `focus source=S held=H`: S 1 while a source plays, H 1 when it is this connection's |
| `focus release` | `ok`, the source stopped and the queue given back; `err focus release` from a connection that holds none |
| `output` | `output dac`, `output bluetooth` or `output bluetooth soft`: where the music goes |
| `output dac`, `output bluetooth`, `output bluetooth soft` | `ok`. The DAC, or a Bluetooth headset through bluealsa at unity (its own level is the volume: q2btd's absolute volume) or at the player's gain (`soft`). A song loaded starts again where it is heard; each output keeps its own volume. Sent by [q2btd](bt.md), not meant for apps |
| `rating ID` | `rating id=ID stars=S liked=L`: song ID's stars (0 to 5) and like (0 or 1), ID 0 the song playing (its id then, 0 for one played as a path); `err rating` for no such song, or with ID 0 nothing that can be rated playing (a source's URL) |
| `rate N ID` | `ok`: N stars for song ID (0 the song playing), 0 taking them out; `err rate` for N past 5, no such song, the table full or not written |
| `like L ID` | `ok`: song ID (0 the song playing) liked (L 1) or not (L 0); asking for what it is already is `ok`; `err like` otherwise |
| `album N ID` | `ok`: song ID's album made a favourite (N 5), which likes its songs, or not (N 0), which takes them out; `err album` for another N or no such song |
| `caps` | `caps play toggle pause resume next previous seek volume stop now watch source focus output rating rate like album` |

## Ratings

A song's stars and its like are apart, as on today's Vamp ([q2playd: Ratings and likes](../playd.md#ratings-and-likes)): stars are set only by `rate`, five stars like nothing, and a like sets no stars. An album has hearts of its own, given only as a favourite (`album 5`) or taken (`album 0`), and its favourite is its songs' like ([Albums' hearts](../playd.md#albums-hearts)). Each change reaches the servers and the card's copy for a computer as one from the Q2's own pages does. Song ids are [the library's](library.md); an album is named by any of its songs (Q2LIB/1's `albums` gives one as `first`).

```conformance
> Q2PLAY/1
< Q2PLAY/1 ok
> play 0 0 2
> 1
> 2
< ok
> rating 0
< rating id=1 stars=0 liked=0
> rate 4 0
< ok
> like 1 0
< ok
> rating 1
< rating id=1 stars=4 liked=1
> like 1 1
< ok
> rate 3 2
< ok
> rating 2
< rating id=2 stars=3 liked=0
> like 0 1
< ok
> rate 0 1
< ok
> rating 0
< rating id=1 stars=0 liked=0
> rate 6 1
< err rate
> like 2 1
< err like
> rating 999
< err rating
> album 3 1
< err album
```

## Audio focus

What plays is the queue's or one source's. A `source` takes the player for its connection, the **holder**: the song playing is cut, and the queue is put aside as it is (its songs, its place, the position). While the source plays, `pause`, `resume`, `toggle`, `seek`, `volume` and `stop` act on it and `next` and `previous` are refused; `now` shows its fields (no title: the URL's last part) with `id=0`. Another `source` from the holder moves it to that URL.

The holder is told, by a line between answers, when it loses the player for any reason but its own `focus release`: `focus lost why=W`, W `end` (the URL played out), `error` (it did not open, or the player went away), `stop` (a `stop` from anyone) or `taken` (a `play` from anyone, or another connection's `source`). On `end`, `error` and `focus release`, and when the holder's connection closes, the queue comes back **paused** where it was put aside (stopped, if it was), and `resume` plays it from there: it never starts again on its own. On `stop` it comes back stopped at its song; on `taken` by a `play` it is dropped, and by another connection's `source` it stays put aside for that one.

**`now`** is `now` and these fields, in this order: `title`, `artist`, `album` (free text, percent-encoded), `state` (0 stopped, 1 playing, 2 paused), `pos` (seconds), `len` (seconds, 0 until the player knows), `volume`, `id` (the song's library id, 0 for a song played from an app's list of paths). Song ids are [the library's](library.md): a card song's from `songtable`, a server song's from `servertable` (ids from `0x40000000`), which plays through its app ([q2playd: Server songs](../playd.md#server-songs)).

```conformance
> Q2PLAY/1
< Q2PLAY/1 ok
> caps
< caps play toggle pause resume next previous seek volume stop now watch source focus output rating rate like album
> now
< ~ now title= artist= album= state=0 pos=0 len=0 volume=\d+ id=0
> play 1 0 3
> 1
> 2
> 3
< ok
> now
< ~ now title=Beta artist=Bob album=First state=1 pos=0 len=0 volume=\d+ id=2
> pause
< ok
> toggle
< ok
> resume
< ok
> next
< ok
> previous
< ok
> seek 0
< ok
> volume 30
< ok
> volume 101
< err volume 101
> output
< output dac
> output bluetooth
< ok
> output
< output bluetooth
> output dac
< ok
> watch
< ok
> stop
<* ~ now .*
< ok
< ~ now title=\S* artist=\S* album=\S* state=0 pos=\d+ len=\d+ volume=30 id=\d+
> bogus
< err bogus
```

```conformance
> Q2PLAY/2
< err version
```

A renderer plays a URL from a phone and gives the queue back (`=` in the URL encoded as `%3D`):

```conformance
> Q2PLAY/1
< Q2PLAY/1 ok
> play 0 0 2
> 1
> 2
< ok
> source http://192.168.1.20:8200/MediaItems/22.mp3?q%3D1 title=Delta artist=Dee album=Phone
< ok
> now
< ~ now title=Delta artist=Dee album=Phone state=1 pos=0 len=0 volume=\d+ id=0
> focus
< focus source=1 held=1
> next
< err next
> source ftp://192.168.1.20/22.mp3
< err source
> focus release
< ok
> now
< ~ now title=Alpha artist=Ann album=First state=2 pos=0 len=0 volume=\d+ id=1
> focus release
< err focus release
```

A stop from anyone ends the source; its holder is told:

```conformance
> Q2PLAY/1
< Q2PLAY/1 ok
> source http://192.168.1.20:8200/MediaItems/23.flac
< ok
> stop
< focus lost why=stop
< ok
> focus
< focus source=0 held=0
```
