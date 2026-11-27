# Q2UI/1 notices: a design for the UI agent

**Status: a design, not served.** LocalSend's receive side, Guest Play's who-may question and an app's own questions need the owner's answer on the screen ([vamp-os-services](../vamp-os-services.md)). This page is what a service would speak; `q2ui` (`ui/`, the UI agent's) implements it. Nothing here edits `ui/`.

## The socket

`/run/vamp/ui.sock`, in [the common shape](README.md#the-common-shape): the client's first line `Q2UI/1`, answered `Q2UI/1 ok`; percent-encoded free text both ways; `caps` answers `caps toast ask watch`. Permission (planned): `notices` for `toast`, `ask` for `ask` ([Packages: Permissions](packages.md#permissions)).

| Command | Answer |
| --- | --- |
| `toast TEXT` | `ok`: the pill over the page ([oled-design: Dialogs and toasts](../oled-design.md#dialogs-and-toasts)), 2.5 s, queued behind one showing |
| `ask ID TITLE BODY CHOICE1 [CHOICE2 [CHOICE3]]` | `ok` at once (or `err busy` when 4 asks wait): the confirmation card ([oled-design: Confirmation card](../oled-design.md#confirmation-card)), first choice focused; ID is the client's word (`a-z0-9-`, 32 at most) |
| (later, on the same connection) | `answer ID N`: the choice taken, from 1; `answer ID 0`: dismissed (Back, the screen off with it open, or `TIMEOUT_S` 60 s); `answer ID gone`: the connection ends or `withdraw` came first |
| `withdraw ID` | `ok`: the card closes, `answer ID gone` |
| `watch` | `ok`; then `front PAGE` lines as the page in front changes (an app knows whether its question can be seen) |

## Rules

- **One card at a time**, in the order asked; a card over a playing page leaves the music alone.
- **The screen**: an `ask` with the screen off lights it, as a wake would (Q2SYS/1 `screen on`); a `toast` never does.
- **No answer is not yes.** A timeout, Back or a dropped connection is never a choice; a client treats 0 and `gone` as "no".
- **Text**: TITLE at most 40 bytes, BODY at most 160, a choice at most 16, after decoding; longer is `err text`. The UI wraps BODY to the card's width.
- **Attribution**: the card names the asking package's `name=` from its manifest (from the caller's uid once packages have users), so an app cannot pose as the system.

## Conformance (for when it is served)

```
> Q2UI/1
< Q2UI/1 ok
> caps
< caps toast ask watch
> toast Saved
< ok
> ask recv-1 Receive%20files? Pixel%208%20sends%203%20files Accept Decline
< ok
> withdraw recv-1
< ok
< answer recv-1 gone
```

LocalSend's use: `ask recv-<n> "Receive files?" "<alias> sends <n> files (<size>)" Accept Decline`, the transfer accepted only on `answer recv-<n> 1`, refused (HTTP 403, as the protocol's decline) on anything else.
