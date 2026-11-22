/* The playing song's cover for a car over Bluetooth (docs/internals.md#bluetooth-cover-art): at
 * each track change, before the song's tags go to bluetoothd, BT_COVER_FILE is rewritten as the
 * cover's image handle (seven digits), the cover's path, where it came from (coverflow, app,
 * folder or own) and the song's path, a line each, or removed when the song has no cover.
 * bluetoothd's GetElementAttributes reads the handle (tools/bt_meta.c, AVRCP's attribute 8) and
 * q2-bip the path (patch/q2bip.c), so both speak of this song. The cover is the first there of:
 * Cover Flow's cached copy for the song's artist and album; for a server song, the cover its app
 * keeps (catalogue.c catalogue_cover, what Now Playing shows); cover.jpg, folder.jpg or front.jpg
 * beside the song, as iPhone mode looks (patch/q2_iap.c io_artwork); and the song's own picture (a
 * FLAC's PICTURE, an MP3's APIC, an M4A's covr: bt_own_picture), the song file itself, which
 * q2-bip's ffmpeg reads the attached picture of. The handle is a file's key, so it changes when the
 * cover does and songs of one album share it (a song's own picture is keyed by its album): a car
 * that has the album's cover fetches nothing at the next song. */
#include "navigation.h"

#define BT_COVER_FILE "/tmp/q2-bip.now" /* BT_COVER_FILE in tools/build.py */
#define BT_COVER_HELD BT_COVER_FILE ".held"

extern unsigned tags_key(const char *artist, const char *album);
extern char *art_path(char *out, unsigned key, const char *suffix);

/* A song file's own picture, its headers alone read (each block, frame or box skipped by its
 * length, never the audio): a FLAC's PICTURE block among its first BT_OWN_STEPS metadata blocks,
 * an ID3v2 tag's APIC frame (PIC in 2.2), or an MP4's moov/udta/meta/ilst/covr, the moov before or
 * after the audio. */
#define BT_OWN_STEPS 256
static unsigned own_be(const unsigned char *b, unsigned n) {
    unsigned v = 0;
    for (unsigned i = 0; i < n; ++i) v = v << 8 | b[i];
    return v;
}
static unsigned own_syncsafe(const unsigned char *b) {
    return (unsigned)(b[0] & 127) << 21 | (b[1] & 127) << 14 | (b[2] & 127) << 7 | (b[3] & 127);
}
static int own_flac(void *f) {
    unsigned char h[4];
    for (unsigned i = 0; i < BT_OWN_STEPS && fread(h, 1, 4, f) == 4; ++i) {
        if ((h[0] & 127) == 6) return 1;
        if (h[0] & 128 || fseek(f, (long)own_be(h + 1, 3), 1)) break; /* the last block, or none */
    }
    return 0;
}
/* h: the tag's ten-byte header, read */
static int own_id3(void *f, const unsigned char *h) {
    unsigned v = h[3], size = own_syncsafe(h + 6), at = 10, head = v == 2 ? 6 : 10;
    unsigned char fr[10];
    if (v < 2 || v > 4) return 0;
    if (v > 2 && h[5] & 0x40) { /* an extended header: 2.4 counts itself, 2.3 does not */
        if (fseek(f, 10, 0) || fread(fr, 1, 4, f) != 4) return 0;
        at += v == 4 ? own_syncsafe(fr) : own_be(fr, 4) + 4;
    }
    for (unsigned i = 0; i < BT_OWN_STEPS && at + head <= 10 + size; ++i) {
        if (fseek(f, (long)at, 0) || fread(fr, 1, head, f) != head || !fr[0]) break; /* padding */
        if (!memcmp(fr, v == 2 ? "PIC" : "APIC", v == 2 ? 3 : 4)) return 1;
        at += head + (v == 2   ? own_be(fr + 3, 3)
                      : v == 4 ? own_syncsafe(fr + 4)
                               : own_be(fr + 4, 4));
    }
    return 0;
}
static int own_mp4(void *f) {
    static const char path[][5] = { "moov", "udta", "meta", "ilst", "covr" };
    unsigned char h[16];
    unsigned depth = 0;
    long at = 0, end = fseek(f, 0, 2) ? 0 : ftell(f);
    for (unsigned i = 0; i < BT_OWN_STEPS && at + 8 <= end; ++i) {
        if (fseek(f, at, 0) || fread(h, 1, 8, f) != 8) break;
        long size = (long)own_be(h, 4), head = 8;
        if (size == 1) { /* a 64-bit size: one past 2 GB is no song's */
            if (fread(h + 8, 1, 8, f) != 8 || own_be(h + 8, 4) || own_be(h + 12, 4) > 0x7fffffffu)
                break;
            size = (long)own_be(h + 12, 4), head = 16;
        } else if (!size)
            size = end - at; /* to the end of its parent */
        if (size < head || size > end - at) break;
        if (memcmp(h + 4, path[depth], 4)) {
            at += size;
            continue;
        }
        if (++depth == sizeof path / sizeof *path) return 1;
        end = at + size,
        at += head + (depth == 3 ? 4 : 0); /* meta is a full box: its version and flags */
    }
    return 0;
}
int bt_own_picture(const char *path) {
    unsigned char h[10];
    void *f = fopen(path, "rb");
    int found = 0;
    if (f && fread(h, 1, 10, f) == 10) {
        if (!memcmp(h, "fLaC", 4))
            found = !fseek(f, 4, 0) && own_flac(f);
        else if (!memcmp(h, "ID3", 3))
            found = own_id3(f, h);
        else if (!memcmp(h + 4, "ftyp", 4))
            found = own_mp4(f);
    }
    if (f) fclose(f);
    return found;
}

/* id3: the playing song's tags (ID3_ARTIST, ID3_ALBUM), path its file */
void bt_cover_note(const char *id3, const char *path) {
    static const char *const beside[] = { "cover.jpg", "folder.jpg", "front.jpg" };
    char cover[1040], line[2200];
    unsigned key = tags_key(id3 + ID3_ARTIST, id3 + ID3_ALBUM), have;
    const char *slash = strrchr(path, '/'), *source = "coverflow";
    /* an empty copy is Cover Flow's "no art" marker (coverflow.c), not a cover */
    have = id3[ID3_ALBUM] && file_size(art_path(cover, key, "")) > 0;
    if (!have && (have = catalogue_cover(path, cover, sizeof cover)))
        key = tags_key("", cover), source = "app";
    for (unsigned i = 0; !have && slash && i < sizeof beside / sizeof *beside; ++i) {
        tk_snprintf(cover, sizeof cover, "%.*s/%s", (int)(slash - path), path, beside[i]);
        have = !access(cover, 0);
        key = tags_key("", cover); /* no album's key: the file's own */
        source = "folder";
    }
    if (!have && (have = bt_own_picture(path))) {
        tk_snprintf(cover, sizeof cover, "%s", path);
        key = id3[ID3_ALBUM] ? tags_key(id3 + ID3_ARTIST, id3 + ID3_ALBUM) : tags_key("", path);
        source = "own";
    }
    void *f = have ? fopen(BT_COVER_FILE ".tmp", "wb") : 0;
    if (!f) {
        unlink(BT_COVER_FILE);
        return;
    }
    /* then where the cover came from and the song, which q2-bip's log names (patch/q2bip.c note) */
    int n =
        tk_snprintf(line, sizeof line, "%07u\n%s\n%s\n%s\n", key % 10000000u, cover, source, path);
    fwrite(line, 1, (unsigned)n, f);
    fclose(f);
    rename(BT_COVER_FILE ".tmp", BT_COVER_FILE); /* whole, or the song before's */
}

/* Guest Play: while a guest's song is the one the car is told of, the owner's cover is set aside,
 * and put back when the owner's song is again. */
void bt_cover_hold(int held) {
    if (!held) {
        if (!access(BT_COVER_HELD, 0)) rename(BT_COVER_HELD, BT_COVER_FILE);
        return;
    }
    unlink(BT_COVER_HELD);
    if (!access(BT_COVER_FILE, 0)) rename(BT_COVER_FILE, BT_COVER_HELD);
}
