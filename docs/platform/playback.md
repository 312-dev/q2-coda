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
| `caps` | `caps play toggle pause resume next previous seek volume stop now watch` |

**`now`** is `now` and these fields, in this order: `title`, `artist`, `album` (free text, percent-encoded), `state` (0 stopped, 1 playing, 2 paused), `pos` (seconds), `len` (seconds, 0 until the player knows), `volume`, `id` (the song's library id, 0 for a song played from an app's list of paths). Song ids are [the library's](library.md): a card song's from `songtable`, a server song's from `servertable` (ids from `0x40000000`), which plays through its app ([q2playd: Server songs](../playd.md#server-songs)).

```conformance
> Q2PLAY/1
< Q2PLAY/1 ok
> caps
< caps play toggle pause resume next previous seek volume stop now watch
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
