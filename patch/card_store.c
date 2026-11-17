/* Card-backed records (docs/internals.md#where-q2-muse-writes): files written often enough to wear the
 * flash live on the card, in CARD_STORE_DIR, and the Q2 works on without a card in the meantime. Only
 * stdio, rename, mkdir and the heap: a separate program can link this file too.
 *
 * A blob is a file written whole (blob_io's temp, sync and rename). With the card away, a write keeps
 * the newest copy in RAM (bounded by the blob's cap) and reads return it; card_blob_flush writes it once
 * the card is back. A blob that names the flash file it replaces reads that file until the card has a
 * copy, and copies it there once: the flash file is left as it was, never written again.
 *
 * A log is appended to. With the card away, lines go to a RAM ring of cap bytes, the oldest whole lines
 * dropped to fit; card_log_flush appends the ring, in order, before the next line, and notes how many
 * lines were dropped. */
#include "peq.h"
#include "card_store.h"

static void cs_dir(void) { mkdir(CARD_STORE_DIR, 0755); /* EEXIST once made */ }

/* path's whole contents into buf (cap bytes at most): the length, or -1 for no file */
static int cs_load(const char *path, void *buf, unsigned cap) {
    void *f = fopen(path, "rb");
    if (!f) return -1;
    int n = (int)fread(buf, 1, cap, f);
    fclose(f);
    return n;
}

/* buf written whole to path through path.tmp, synced and renamed: 1 when the card took it */
static int cs_save(const char *path, const void *buf, unsigned n) {
    char tmp[160];
    unsigned len = strlen(path);
    if (len + 5 > sizeof tmp) return 0;
    memcpy(tmp, path, len);
    memcpy(tmp + len, ".tmp", 5);
    cs_dir();
    void *f = fopen(tmp, "wb");
    if (!f) return 0;
    int ok = !n || fwrite(buf, n, 1, f) == 1;
    if (fflush(f) || fsync(fileno(f))) ok = 0;
    if (fclose(f) || !ok) return 0;
    return !rename(tmp, path);
}

/* The RAM copy set to buf; 0 when the heap or the cap refuses it, the copy before kept. */
static int cs_hold(card_blob *b, const void *buf, unsigned n) {
    if (n > b->cap) return 0;
    if (!b->ram || n > b->room) {
        void *ram = calloc(1, n ? n : 1);
        if (!ram) return 0;
        free(b->ram);
        b->ram = ram, b->room = n;
    }
    memcpy(b->ram, buf, n);
    b->len = n, b->held = 1;
    return 1;
}

int card_blob_write(card_blob *b, const void *buf, unsigned n) {
    if (card_mounted() && cs_save(b->card, buf, n)) {
        b->held = 0, b->moved = 1;
        return 1;
    }
    return cs_hold(b, buf, n);
}

int card_blob_flush(card_blob *b) {
    if (!b->held || !card_mounted()) return !b->held;
    if (!cs_save(b->card, b->ram, b->len)) return 0;
    b->held = 0, b->moved = 1;
    return 1;
}

int card_blob_read(card_blob *b, void *buf, unsigned cap) {
    if (b->held) {
        unsigned n = b->len < cap ? b->len : cap;
        memcpy(buf, b->ram, n);
        return (int)n;
    }
    int n = card_mounted() ? cs_load(b->card, buf, cap) : -1;
    if (n >= 0 || !b->flash) return n;
    /* No card copy yet: the flash file a release before this one wrote, copied to the card once. */
    n = cs_load(b->flash, buf, cap);
    if (n >= 0 && card_mounted() && !b->moved) b->moved = cs_save(b->card, buf, (unsigned)n);
    return n;
}

void card_blob_drop(card_blob *b) {
    b->held = 0;
    if (card_mounted()) unlink(b->card);
}

/* The ring's oldest whole lines dropped until n more bytes fit. */
static void cs_make_room(card_log *l, unsigned n) {
    unsigned cut = 0;
    while (l->len - cut + n > l->cap && cut < l->len) {
        while (cut < l->len && l->ring[cut] != '\n') ++cut;
        cut += cut < l->len; /* the newline too */
        ++l->dropped;
    }
    memmove(l->ring, l->ring + cut, l->len - cut);
    l->len -= cut;
}

static int cs_append(const char *path, const char *s, unsigned n) {
    cs_dir();
    void *f = fopen(path, "ab");
    if (!f) return 0;
    int ok = fwrite(s, n, 1, f) == 1;
    if (fflush(f) || fsync(fileno(f))) ok = 0;
    return !fclose(f) && ok;
}

int card_log_flush(card_log *l) {
    if (!l->len && !l->dropped) return 1;
    if (!card_mounted()) return 0;
    char note[64];
    if (l->dropped) {
        int k = snprintf(note, sizeof note, "== %u lines lost while the card was away\n", l->dropped);
        if (!cs_append(l->card, note, (unsigned)k)) return 0;
        l->dropped = 0;
    }
    if (l->len && !cs_append(l->card, l->ring, l->len)) return 0;
    l->len = 0;
    return 1;
}

int card_log_append(card_log *l, const char *s, unsigned n) {
    /* card_mounted first: with the card away /mnt/mmc is the bare mount point, on the root file system */
    if (card_mounted() && card_log_flush(l) && cs_append(l->card, s, n)) return 1;
    if (n > l->cap) return 0; /* longer than the ring: never kept */
    if (!l->ring && !(l->ring = calloc(1, l->cap))) return 0;
    cs_make_room(l, n);
    memcpy(l->ring + l->len, s, n);
    l->len += n;
    return 1;
}
