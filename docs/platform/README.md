# Vamp API 1

The API Vamp OS offers a program that is not part of it ([roadmap: Platform](../vamp-os.md#platform)): service sockets, each with its protocol here, and a conformance test that runs the real service on a laptop and replays this folder's transcripts against it. It is **experimental** until two apps written outside the project ship on it ([roadmap: The API](../vamp-os.md#the-api)).

| Area | Service | Socket | Protocol | Permission (planned) |
| --- | --- | --- | --- | --- |
| Playback | `q2playd` | `/run/vamp/play.sock` | [Q2PLAY/1](playback.md) | `playback` |
| System: screen, battery, USB, standby | `q2sysd` | `/run/vamp/sys.sock` | [Q2SYS/1](system.md) | `settings`, `screen` |
| Network | `q2netd` | `/run/vamp/net.sock` | [Q2NET/1](network.md) | `network-hold` |
| Media library | `q2libd` | two SQLite files, read-only | [Library 1](library.md) | `library` |
| Packages | `q2pkgd` | the card | [Packages](packages.md) | |
| Notices and questions (designed, not served) | `q2ui` | `/run/vamp/ui.sock` | [Q2UI/1 notices](notices.md) | `notices`, `ask` |

Not in API 1 yet: the declarative UI (Q2SPOT/1 stays API 0, served by today's Vamp; Vamp OS has no shim for it yet), `libvampui`, the canvas, input, a settings namespace, storage folders, power holds and notices, and the checks of the permissions no socket guards yet ([Packages: Users and groups](packages.md#users-and-groups)).

## The common shape

Every API 1 socket is the same ([roadmap: Process model](../vamp-os.md#process-model)):

- **Lines.** Text, one command or answer a line, ended by `\n`. A client's line is at most 512 bytes; a service serves at most 8 clients at once.
- **Hello.** The client's first line is the protocol's name and major version (`Q2PLAY/1`); the service answers it with ` ok` added, or with `err version` and closes the connection.
- **An answer to every command**: `ok`, `err WHY` (the command, or a word naming what failed), or the command's own line (`now ...`, `status ...`). A command with several lines of answer ends them with `ok`.
- **Subscriptions, not polling.** `watch` answers `ok`; from then on the service writes its event lines as things change, between answers to commands.
- **Fields.** An answer's values are `key=value` words separated by single spaces. Free text (titles, SSIDs, the kernel's battery text) is percent-encoded: every byte below `0x21`, `%`, `"`, `=` and `0x7f` is `%` and two upper-case hex digits, every other byte (UTF-8 included) as it is. A client splits on spaces, each field at its first `=`, and URL-decodes the value. Numbers are plain decimal. Where a command carries free text (Q2NET/1's SSIDs and passphrases) it is encoded the same way.
- **Permissions.** A service whose socket a permission guards answers the hello of a caller without it `err permission` and closes the connection; each such socket is mode 0660 and its permission's group's ([Packages: Users and groups](packages.md#users-and-groups)).

## Capabilities

`caps` answers `caps` and the service's capability words, one per command family it serves, in a fixed order; a word with a dash names a command's first words (`usb-dac` is `usb dac`). Within a major version changes are additive: a new command comes with a new word, so a client asks `caps` and uses what is there, and an older service, which lacks a word, is handled without an error. A word is never removed within a major version; a protocol that must change incompatibly is a new major version (`Q2PLAY/2`), served beside the old one while an app in the signed list uses it.

## Naming

The roadmap's sketch named protocols `VAMP/1 playback`; the services already speak `Q2PLAY/1`, `Q2SYS/1` and `Q2NET/1`, which `q2ui`, the tests and `q2sysd`'s own calls to the others use, so those names are API 1's. A package's manifest names the API it needs as `api=1` ([Packages](packages.md#the-manifest)).

## Conformance

Each protocol page holds `conformance` blocks: `> line` is what the client sends, `< line` the answer expected byte for byte, `< ~ regex` an answer that must match the regular expression whole. Each service's suite replays its page's blocks against the real service, built for the laptop, on a fresh stand-in Q2 for each block, and checks that every word `caps` names is sent as a command by some block, so a command cannot be added without its page:

```
python3 test/api.py          # every protocol page against its service
```

(the Library's page is checked against a scan instead: [Library 1](library.md#conformance)). The examples' data is the suites' stand-ins: songs 1, 2 and 3 (`Alpha` by `Ann`, `Beta` by `Bob`, `Gamma` by `Cy`) for Q2PLAY/1, a battery at 73 % and 3912 mV for Q2SYS/1, a saved network `Home` for Q2NET/1.
