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
| `bluetooth` | Read Bluetooth's state, turn it on and off, scan, pair, connect, choose the codec ([Q2BT/1](bt.md)) | `q2btd` |
| `screen` | Take the framebuffer and raw input while in front: a `game`'s alone | `q2ui` |
| `music-files` | Read and write the card's music folders | the card |
| `background` | Run when it is not in front, under the supervisor: every `service` needs it | `q2pkgd` |
| `notices` | Show a toast ([Q2UI/1 notices](notices.md)) | `q2ui` |
| `ask` | Ask the owner a question on a confirmation card ([Q2UI/1 notices](notices.md)) | `q2ui` |

## Users and groups

Each permission is a group of that name (gids 2000 to 2009, `perm/q2perm.h`). `q2pkgd app-add DIR` gives an API 1 package a user, `app-<id>` (uids from 10000; the id at most 27 bytes, so the name fits 32), in its own group and in the group of each permission its manifest names, and a data folder `/mnt/data/vamp/app/<id>` (mode 0700, its own); it is recorded in `/mnt/data/vamp/apps`, `<id> <uid> <permissions>` a line. Added again (an update), it keeps its uid and takes the manifest's permissions as they now are. `app-remove ID` keeps its line, marked `-`: the uid and the data folder stay that package's, and no later package is given them. An API 0 app gets no user and runs as today.

The image's `/etc` is read-only, so `/etc/passwd` and `/etc/group` are links to `/run/vamp/passwd` and `/run/vamp/group` (`os/board/q2/post-fakeroot.sh`; the image's own users, Buildroot's, are `/etc/passwd.base` and `/etc/group.base`). rcS copies the `.base` files there at its start and, once `/mnt/data` is mounted, runs `q2pkgd accounts`, which writes both again with each package's user and each permission's group listing its members.

`q2pkgd app-run DIR` starts an added package's entry as its user: the groups of the permissions recorded at `app-add` (what the owner approved, not what the manifest says now), then its gid and uid, in its folder with `HOME` its data folder; if any of those fails, or uid 0 could be taken back, it runs nothing.

**Enforced at run time**: Q2PLAY/1 (`playback`), Q2SYS/1 (`settings`) and Q2NET/1 (`network-hold`). Each answers the hello of a caller that is not root and whose user `/etc/group` does not list in its group with `err permission`, closes the connection, and logs `refused uid N (app-x): no playback permission`. The caller's uid is `SO_PEERCRED`'s, fixed when it connected; its user and groups are read from the files, not from the process, so a package cannot give itself a group. Each socket is mode 0660 and its group's. **Not yet enforced**: `library` and `library-write` (the library is two SQLite files any process can read, until they are the group's and 0640), `music-files` (the card is FAT or exFAT, with no owners: all or nothing, and today all), `screen`, `notices` and `ask` (`q2ui` serves no socket yet), and `background` (`q2pkgd` starts packages under the supervisor, [below](#starting-and-stopping-q2pkg1), but nothing yet ends an app without it when it leaves the front: that waits on `q2ui` asking `q2pkgd` to open and close foreground apps). Whether the Q2's kernel offers what would hold an app further (mount and network namespaces, cgroups, seccomp) is a question for the Q2 ([vamp-os-base: Needs a device](../vamp-os-base.md#needs-a-device)); without network namespaces or netfilter's owner match, network access cannot be withheld.

**Tests.** `python3 test/perm.py` (pytest): each of the three services, built for this machine, refusing and logging a caller whose user lacks its group or whose uid has no user, and letting a member in; and, with a service in a user namespace of its own (`bwrap --unshare-user --uid N`, where the suite's connections arrive as uid N), root let in with no groups and each package's uid, from `q2pkgd app-add`'s files, reaching only the services it was given, and a removed one's refused. Of `q2pkgd`: the users, groups and registry `app-add` writes byte for byte, the data folder's mode, the same uid on a second add, a removed package's uid kept from the next, an API 0 app and a bad manifest given none, `accounts` with no packages, and `app-run` starting nothing when it cannot become the package's user. Copies without root's exception, without the members' check, with no members written, with a removed uid given away, with `app-run` going on past a failed `setgroups`, with a data folder others can read, with one service's check left out or checking the wrong group each fail it (`Q2PERM_DIR` for a copy of `perm/`, and each service's `*_SOURCE`). On this machine a service and its client have one real uid between them, so no test here tells `SO_PEERCRED`'s uid from the service's own: a Q2, where the services are root and a package is not, does ([vamp-os-base: Needs a device](../vamp-os-base.md#needs-a-device)).

## Starting and stopping: Q2PKG/1

`q2pkgd` is the launcher: it owns the packages, their users and groups and their permissions, so it alone starts and stops them (the owner's ruling, 2026-11-27). `q2pkgd serve`, the image's `q2pkgd` service, makes each app it starts a runit service of its own, `/run/vamp/sv/<id>` (its `runsv` started by `q2pkgd`; `sv status /run/vamp/sv/<id>` from a shell), whose `run` is `q2pkgd app-run /mnt/mmc/.<id>`:

- **An API 1 app** runs as its user ([Users and groups](#users-and-groups)). A `service` is kept up (`runsv` starts it again when it ends); any other kind runs once, and its own exit stands.
- **An API 0 app** runs as today, as root with no user of its own: its `run` script, which starts the app's loop in the background, leaves the loop's pid in `/tmp/q2-<id>` and returns; `app-run` then watches that pid, so runit sees the app running while its loop lives, and a stop is a TERM to the loop, as today's Stop is. Before each start `q2pkgd` writes `/tmp/q2-addon.caps`, the capability line an API 0 app reads once when it starts ([internals: Card apps](../internals.md)), with the words this image serves: `catalogue` (`q2libd` reads the app's `catalogue.tsv`, and `q2playd` hands it the Library's plays of its songs) `playlist` (`q2playd` plays the m3u its `.play` names) and `ahead` (`q2playd` writes Download Ahead's `.ahead`, [q2playd: Download Ahead](../playd.md#download-ahead); the app's `.stream` is the UI's to show). Today's Vamp's other words are its UI's, Q2SPOT/1's pages, which Vamp OS does not serve yet.

`q2playd` asks it to start a stopped app before a server play ([q2playd: Server songs](../playd.md#server-songs)); `q2ui` will ask it to open a foreground app.

The socket is `/run/vamp/pkg.sock`, in [the common shape](README.md#the-common-shape), mode 0666: no permission guards the hello, and each command is checked against the caller's uid (`SO_PEERCRED`'s). Root, which the system's services are, may start, stop and ask about any app; a package's own uid (from `/mnt/data/vamp/apps`) may ask its own `status` and `stop` itself; anything else is answered `err permission` and logged `refused uid N (app-x): start navi`.

| Command | Answer |
| --- | --- |
| `start ID` | `ok` once runit runs it, or when it runs already; `err app` (no `.<id>` on the card with a manifest this page accepts, or one with nothing to run: a theme, a language, an API 0 app with no `run`), `err not-added` (an API 1 app that `app-add` has not given a user, or one removed), `err start` (its `runsv` not up within 2 s) |
| `stop ID` | `ok` once it has ended, at most 5 s; `err stop` when it has not |
| `status ID` | `status ID running` or `status ID stopped` (an API 0 app also runs while the pid in `/tmp/q2-<id>` lives, however it was started); `err app` |
| `caps` | `caps start stop status` |

The examples' card: `.navi`, an API 0 app with a `catalogue=1` manifest and a `run` script as q2-subsonic's; `.radio`, the API 1 app [below](#examples), not added; `.dusk`, a theme.

```conformance
> Q2PKG/1
< Q2PKG/1 ok
> caps
< caps start stop status
> status navi
< status navi stopped
> start navi
< ok
> status navi
< status navi running
> start navi
< ok
> stop navi
< ok
> status navi
< status navi stopped
> start radio
< err not-added
> start dusk
< err app
> start nobody
< err app
> status ../navi
< err app
> launch navi
< err command
```

```conformance
> Q2PKG/2
< err version
```

**Tests.** `python3 test/launch.py` (pytest): `q2pkgd serve` built for this machine, with BusyBox 1.37.0's own `runsv` (the image's, built for this machine from the same source) and a stand-in for q2-subsonic, replays these transcripts as root (`q2pkgd` in a user namespace where the suite's connections arrive as uid 0); checks the caps file's words, the API 0 loop ended by `stop` and by `sv down`, an API 1 app started through `app-run` as its user (here it cannot become that user, and runs nothing), a `service` started again by `runsv`; a package's uid allowed only its own `status` and `stop`; and `q2playd` and `q2pkgd` together: a server play of a stopped app starts it and plays the m3u it answers. Copies of `q2pkgd.c` without the caps line, letting any uid in, keeping every app up, not passing a TERM on to an API 0 loop, or taking an API 1 app for an API 0 one each fail it (`Q2PKGD_SOURCE`); copies of `q2playd.c` writing the catplay before the socket is there, not asking `q2pkgd`, or with no 15 s limit fail `test/playd.py`'s server songs and this suite's server play (`Q2PLAYD_SOURCE`). `test/os_image.py`'s boot starts and stops an API 0 stand-in over Q2PKG/1 under the image's own `runsv` and checks its caps line; `--perturb pkgd-off` fails it.

`app-run` makes itself the API 0 loop's subreaper (`PR_SET_CHILD_SUBREAPER`, Linux 3.4's), so the loop, orphaned when its `run` script returns, is its child to wait for. QEMU's user mode refuses that call (`EINVAL`, QEMU 8.2), so under the emulator the loop is watched by its pid every 500 ms and init reaps it; whether Shanling's 5.10 kernel takes it is a Q2 question ([vamp-os-base: Needs a device](../vamp-os-base.md#needs-a-device)).

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

```q2app .airplay ok q2-airplay
name=AirPlay
api=1
version=2.0
kind=service
entry=q2-airplay
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
