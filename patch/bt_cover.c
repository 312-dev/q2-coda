/* The playing song's cover for a car over Bluetooth (docs/internals.md#bluetooth-cover-art): at
 * each track change, before the song's tags go to bluetoothd, BT_COVER_FILE is rewritten as the
 * cover's image handle (seven digits), a newline, the cover's path and a newline, or removed when
 * the song has no cover. bluetoothd's GetElementAttributes reads the handle (tools/bt_meta.c,
 * AVRCP's attribute 8) and q2-bip the path (patch/q2bip.c), so both speak of this song. The cover
 * is the one iPhone mode sends (tools/iap_probe/q2-iap-probe.c io_artwork): Cover Flow's cached
 * copy for the song's artist and album, else cover.jpg, folder.jpg or front.jpg beside the song.
 * The handle is that file's key, so it changes when the cover does and songs of one album share it:
 * a car that has the album's cover fetches nothing at the next song. ponytail: a server song whose
 * cover only its app has shows none; album_cover.c's catalogue_cover would name it. */
#include "navigation.h"

#define BT_COVER_FILE "/tmp/q2-bip.now" /* BT_COVER_FILE in tools/build.py */
#define BT_COVER_HELD BT_COVER_FILE ".held"

extern unsigned tags_key(const char *artist, const char *album);
extern char *art_path(char *out, unsigned key, const char *suffix);

/* id3: the playing song's tags (ID3_ARTIST, ID3_ALBUM), path its file */
void bt_cover_note(const char *id3, const char *path) {
    static const char *const beside[] = { "cover.jpg", "folder.jpg", "front.jpg" };
    char cover[1040], line[1060];
    unsigned key = tags_key(id3 + ID3_ARTIST, id3 + ID3_ALBUM), have;
    const char *slash = strrchr(path, '/');
    have = id3[ID3_ALBUM] && !access(art_path(cover, key, ""), 0);
    for (unsigned i = 0; !have && slash && i < sizeof beside / sizeof *beside; ++i) {
        tk_snprintf(cover, sizeof cover, "%.*s/%s", (int)(slash - path), path, beside[i]);
        have = !access(cover, 0);
        key = tags_key("", cover); /* no album's key: the file's own */
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
