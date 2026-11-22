/* The playing song's cover for a car over Bluetooth (docs/internals.md#bluetooth-cover-art): at
 * each track change, before the song's tags go to bluetoothd, BT_COVER_FILE is rewritten as the
 * cover's image handle (seven digits), a newline, the cover's path and a newline, or removed when
 * the song has no cover. bluetoothd's GetElementAttributes reads the handle (tools/bt_meta.c,
 * AVRCP's attribute 8) and q2-bip the path (patch/q2bip.c), so both speak of this song. The cover
 * is the first there of: Cover Flow's cached copy for the song's artist and album; for a server
 * song, the cover its app keeps (catalogue.c catalogue_cover, what Now Playing shows); cover.jpg,
 * folder.jpg or front.jpg beside the song, as iPhone mode looks (patch/q2_iap.c io_artwork); and
 * a FLAC's own picture, the song file itself, which q2-bip's ffmpeg reads the attached picture of.
 * The handle is a file's key, so it changes when the cover does and songs of one album share it
 * (a song's own picture is keyed by its album): a car that has the album's cover fetches nothing
 * at the next song. ponytail: an MP3's or M4A's own picture is not looked for; a sniff of ID3v2's
 * APIC or MP4's covr would add them. */
#include "navigation.h"

#define BT_COVER_FILE "/tmp/q2-bip.now" /* BT_COVER_FILE in tools/build.py */
#define BT_COVER_HELD BT_COVER_FILE ".held"

extern unsigned tags_key(const char *artist, const char *album);
extern char *art_path(char *out, unsigned key, const char *suffix);

/* Whether path is a FLAC with a PICTURE block among its first BT_FLAC_BLOCKS metadata blocks: the
 * headers alone are read, each block's length skipped, never the audio. */
#define BT_FLAC_BLOCKS 64
static int flac_picture(const char *path) {
    unsigned char h[4];
    void *f = fopen(path, "rb");
    int found = 0;
    if (f && fread(h, 1, 4, f) == 4 && !memcmp(h, "fLaC", 4))
        for (unsigned i = 0; i < BT_FLAC_BLOCKS && fread(h, 1, 4, f) == 4; ++i) {
            if ((h[0] & 127) == 6) {
                found = 1;
                break;
            }
            if (h[0] & 128 || fseek(f, (long)(h[1] << 16 | h[2] << 8 | h[3]), 1))
                break; /* the last block, or none to seek to */
        }
    if (f) fclose(f);
    return found;
}

/* id3: the playing song's tags (ID3_ARTIST, ID3_ALBUM), path its file */
void bt_cover_note(const char *id3, const char *path) {
    static const char *const beside[] = { "cover.jpg", "folder.jpg", "front.jpg" };
    char cover[1040], line[1060];
    unsigned key = tags_key(id3 + ID3_ARTIST, id3 + ID3_ALBUM), have;
    const char *slash = strrchr(path, '/');
    have = id3[ID3_ALBUM] && !access(art_path(cover, key, ""), 0);
    if (!have && (have = catalogue_cover(path, cover, sizeof cover))) key = tags_key("", cover);
    for (unsigned i = 0; !have && slash && i < sizeof beside / sizeof *beside; ++i) {
        tk_snprintf(cover, sizeof cover, "%.*s/%s", (int)(slash - path), path, beside[i]);
        have = !access(cover, 0);
        key = tags_key("", cover); /* no album's key: the file's own */
    }
    if (!have && (have = flac_picture(path))) {
        tk_snprintf(cover, sizeof cover, "%s", path);
        key = id3[ID3_ALBUM] ? tags_key(id3 + ID3_ARTIST, id3 + ID3_ALBUM) : tags_key("", path);
    }
    void *f = have ? fopen(BT_COVER_FILE ".tmp", "wb") : 0;
    if (!f) {
        unlink(BT_COVER_FILE);
        return;
    }
    int n = tk_snprintf(line, sizeof line, "%07u\n%s\n", key % 10000000u, cover);
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
