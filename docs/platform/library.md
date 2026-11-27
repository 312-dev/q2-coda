# Library 1: the media library

`q2libd`'s two databases ([q2libd](../libd.md)), read-only to everyone else: open them with SQLite's read-only flag, and read again when they change (each is written whole beside itself and renamed over it, so a reader never sees half a scan; a reader holding the old file open keeps reading the old one). One change is made in place: `servermeta`'s `local`, in one transaction, as an app's Stream Cache gains or loses a copy. A socket for queries, search and art (Q2LIB/1) is a later slice; this is what Library 1 is until then.

| File | Tables |
| --- | --- |
| `/mnt/data/vamp/library.db` | the card's songs, their covers, the card's playlists |
| `/mnt/mmc/.vamp/catalogue.db` | the signed-in servers' songs, less those that are card songs, and their offline copies |

**Ids.** A card song's id is below `0x40000000` and stays while its path does. A server song's is `0x40000000 + slot * 0x01000000 + n`, slot 0 to 3 its app's place among the signed-in apps by name: stable while the app's catalogue is, not across a sign-out. [Q2PLAY/1](playback.md) plays either kind by id.

**Paths.** `fileurl` is the path as the Q2 names it (`/mnt/mmc/...`). A server row's is `/mnt/mmc/.<app>/cat/<album id>/<sid>.<suffix>`, a name and not a file: the app fetches it ([server-library](../server-library.md)). A cover's `file` is a JPEG or PNG on the card.

**Offline copies.** `servermeta` has a row for each `servertable` row: its `id`, its app's `app` id, the server's `sid`, and `local`, the path of a whole copy on the card (the app's Stream Cache) or NULL. A server song with a copy plays from it when its app does not answer ([q2playd: Offline copies](../playd.md#offline-copies)).

**Playlists.** `playlist` holds a header row for each (`id` and `cd_num` its number, 1 to 100, `name` its name) and its songs (`id` from 101 in order, `cd_num` the playlist's, the other columns the song's `songtable` row).

## Conformance

The tables and their columns, in order, as a scan writes them; `test/libd.py`'s `test_conformance_library` scans a stand-in card with a signed-in app and compares each file's tables and columns with these blocks, so a column cannot change without this page. Columns beyond these may come in a later Library 1, never fewer.

```schema library.db
songtable: id filename fileurl album songer style name composer albumsonger year track cd_num time time_end time_create Initial bitdepth samprate
vamp_files: fileurl size mtime id
vamp_cover: id file
playlist: id filename fileurl album songer style name composer albumsonger year track cd_num time time_end time_create Initial bitdepth samprate
```

```schema catalogue.db
servertable: id filename fileurl album songer style name composer albumsonger year track cd_num time time_end time_create Initial bitdepth samprate
serverdup: id fileurl
servermeta: id app sid local
```
