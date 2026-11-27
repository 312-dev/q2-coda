# Packages: the app manifest

An app is a folder `.<id>` on the card (`<id>` of `a-z`, `0-9` and `-`) holding a `q2app` manifest and its files, installed from a zip that a signed list pins by SHA-256 ([internals: Q2 Shelf](../internals.md), [roadmap: App model and packages](../vamp-os.md#app-model-and-packages)). `q2app` is `key=value` lines (a trailing `\r` is dropped; `#` lines are comments). `q2pkgd app-check DIR` checks a folder against this page and says what the app is and may do, or the first thing wrong; it installs nothing.

## The manifest

| Key | API | What |
| --- | --- | --- |
| `name=` | 0, 1 | The app's name as rows show it. Required |
| `api=` | 1 | The lowest Vamp API it needs, a number. This Vamp OS serves API 1; an app asking for more is refused, not run half-working |
| `version=` | 1 | Dotted numbers (`1.4.2`) |
| `kind=` | 1 | `browse` (serves pages the system draws), `toolkit` (draws with `libvampui`), `game` (takes the canvas), `service` (runs in the background), `theme`, `language` (data alone), `provider` (lyrics, covers, scrobbling answers) |
| `entry=` | 1 | The program, a file in the folder, then plain arguments, run with no shell: no `/` at its start, no `..`, none of `` \| & ; < > $ ` \ " ' * ? ( ) { } ``. Every kind but `theme` and `language` needs one; those two have none |
| `permissions=` | 1 | Comma-separated, from the table below |

A manifest with none of `api=`, `version=`, `kind=`, `entry=` and `permissions=` is an **API 0** app: today's card apps (`catalogue=1`, `covers=1`, `lyrics=1`, `resume=1`, `more=`, and the rest of their words, [internals: Card apps](../internals.md)), run as today. API 0's own keys are not checked here.

## Permissions

| Permission | Lets the app | Its service |
| --- | --- | --- |
| `playback` | Read Now Playing, transport, the queue, the volume ([Q2PLAY/1](playback.md)) | `q2playd` |
| `library` | Read the library ([Library 1](library.md)) | `q2libd` |
| `library-write` | Add to playlists, ratings (a later Library) | `q2libd` |
| `settings` | Read and set the screen, brightness, USB mode, standby ([Q2SYS/1](system.md)) | `q2sysd` |
| `network-hold` | Hold the radio up while it works, and read Wi-Fi's state ([Q2NET/1](network.md)) | `q2netd` |
| `screen` | Take the framebuffer and raw input while in front: a `game`'s alone | `q2ui` |
| `music-files` | Read and write the card's music folders | the card |
| `background` | Run when it is not in front, under the supervisor: every `service` needs it | `q2pkgd` |

**What is enforced today: nothing at run time.** Every program on Vamp OS is still root and every socket is root's mode 0600. The plan ([roadmap: Permissions and isolation](../vamp-os.md#permissions-and-isolation)): at install `q2pkgd` makes a user for the package and adds it to a group per permission it names; each socket is the group's (`0660`), and each service checks a caller's groups by `SO_PEERCRED` per call, logging a refusal. That needs `/etc/passwd` and `/etc/group` on writable storage (the image's `/etc` is read-only squashfs) and the services' checks, neither done; and whether the Q2's kernel offers what would hold an app further (mount and network namespaces, cgroups, seccomp) is a question for the Q2 ([vamp-os-base: Needs a device](../vamp-os-base.md)). Until then a manifest's permissions are what the store shows the user before installing, not a boundary.

## Examples

Each block below is checked by `test/apps.py`: its first line names the folder, `ok` or `refused`, and the files beside the manifest.

```q2app .radio ok q2-radio
name=Radio
api=1
version=1.2.0
kind=browse
entry=q2-radio --quiet
permissions=playback,network-hold
```

```q2app .scrobbler ok q2-scrobbler
name=Scrobbler
api=1
version=0.3
kind=service
entry=q2-scrobbler
permissions=playback,network-hold,background
```

```q2app .dusk ok
name=Dusk
api=1
version=1
kind=theme
```

```q2app .subsonic ok q2-subsonic run
name=Subsonic
catalogue=1
covers=1
```

```q2app .tetris refused q2-tetris
name=Tetris
api=1
version=2.0
kind=toolkit
entry=q2-tetris
permissions=screen
```
