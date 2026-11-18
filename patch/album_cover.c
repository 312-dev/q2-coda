/* An album's cover for a list that names the album by one of its songs: the album page's header
 * (library_album.c) and Up Next's rows (upnext.c) (docs/internals.md#album-covers). Subsonic's
 * saved cover of a server album (catalogue_cover), else Cover Flow's cached cover (art_path), else
 * the cover Now Playing shows when the song playing is of that album (now_cover: the player's file
 * of that very song, so never another album's), else nothing yet: a thread fills Cover Flow's cache
 * for the album from a copy of one of its songs on the card (the song's own file, its Library row's
 * offline copy, or a download of it in its app's stream cache), and the list asks again once
 * album_cover_gen moves. A Local Only album, or a server album Subsonic has not fetched a cover of,
 * was grey until Cover Flow drew it (the user's, 2026-11-17: "Idle Moments"). */
#include "navigation.h"

extern unsigned album_key(void *r), tags_key(const char *artist, const char *album);
extern char *art_path(char *out, unsigned key, const char *suffix);
extern int album_art(const char *track, const char *dst, int shared);
extern volatile unsigned ff_kills;
extern unsigned char now_cover(void *r, const char **file);

#define AC_TRIES 8 /* songs of the album a fetch looks for a copy of */
#define AC_NONE 4  /* albums a fetch found no copy of, not asked again */

static struct {
    volatile unsigned busy, gen; /* a fetch runs; fetches done that wrote a cover */
    unsigned none[AC_NONE], nnone;
} ac __attribute__((section(".scratch")));

unsigned album_cover_gen(void) { return ac.gen; }
int album_cover_busy(void) { return ac.busy != 0; }

typedef struct {
    unsigned key, n;
    char track[AC_TRIES][512];
} ac_job;

/* In dir and the three folder levels under it (<account>/<quality>/<shard>: q2-subsonic's
 * cache.go), a file named "... [sid].<ext>": 1 and its path in out. */
static int ac_find(const char *dir, const char *sid, int depth, char *out) {
    void *d = opendir(dir);
    int found = 0;
    char path[512];
    unsigned k = strlen(sid);
    for (struct dirent *e; d && !found && (e = readdir(d));) {
        const char *n = e->d_name, *at = strrchr(n, '[');
        if (n[0] == '.') continue;
        if (tk_snprintf(path, sizeof path, "%s/%s", dir, n) >= (int)sizeof path) continue;
        if (at && !strncmp(at + 1, sid, k) && at[1 + k] == ']' && at[2 + k] == '.' &&
            !access(path, 4))
            found = tk_snprintf(out, 512, "%s", path) < 512;
        else if (depth < 3)
            found = ac_find(path, sid, depth + 1, out);
    }
    if (d) closedir(d);
    return found;
}

/* A server row's path (/mnt/mmc/.<id>/cat/<album>/<sid>.<suffix>): a download of it in its app's
 * stream cache, into out */
static int ac_stream_copy(const char *row, char *out) {
    const char *cat = strstr(row + 10, "/cat/"), *name = strrchr(row, '/'), *dot;
    char dir[160], sid[64];
    if (!cat || !name || !(dot = strrchr(name, '.')) || dot - name - 1 <= 0 ||
        dot - name - 1 >= (int)sizeof sid || cat - row + 8 >= (int)sizeof dir)
        return 0;
    tk_snprintf(sid, (unsigned)(dot - name), "%s", name + 1);
    tk_snprintf(dir, sizeof dir, "%.*s/stream", (int)(cat - row), row);
    return ac_find(dir, sid, 0, out);
}

/* The fetch: the first song with a copy on the card gives the album its cover in Cover Flow's
 * cache, as build_art gives it (an empty file when that song has none, which Refresh Coverflow
 * clears); an album with no copy at all is noted, so its lists don't ask again. */
static void *ac_worker(void *arg) {
    ac_job *j = arg;
    char track[512], dst[512], tmp[512];
    int tried = 0;
    for (unsigned i = 0; i < j->n && !tried; ++i) {
        const char *t = j->track[i];
        if (catalogue_path(t) ? !ac_stream_copy(t, track) : access(t, 4)) continue;
        if (!catalogue_path(t)) tk_snprintf(track, sizeof track, "%s", t);
        tried = 1;
        art_path(dst, j->key, "");
        art_path(tmp, j->key, ".ac");
        unsigned kills = ff_kills;
        if (album_art(track, tmp, 0) && !rename(tmp, dst)) {
            __atomic_fetch_add(&ac.gen, 1, __ATOMIC_RELAXED);
            break;
        }
        unlink(tmp);
        /* Cover Flow's note of no art, as build_art leaves it; not for a server album, whose
         * cover Subsonic may still fetch and Cover Flow then builds */
        void *f =
            kills == ff_kills && !catalogue_path(t) && !access(track, 0) ? fopen(dst, "w") : 0;
        if (f) fclose(f);
    }
    if (!tried) ac.none[ac.nnone++ % AC_NONE] = j->key;
    free(j);
    __atomic_store_n(&ac.busy, 0, __ATOMIC_RELEASE);
    return 0;
}

/* A fetch of key's cover from songs[0..n) (records), unless one runs, the album is already
 * known to have no copy, or no song has anything to look for. */
static void ac_fetch(unsigned key, void *const *songs, unsigned n) {
    for (unsigned i = 0; i < AC_NONE && i < ac.nnone; ++i)
        if (ac.none[i] == key) return;
    char dir[512];
    *strrchr(art_path(dir, key, ""), '/') = 0; /* Cover Flow's folder */
    if (ac.busy || !card_space(dir)) return;
    ac_job *j = calloc(1, sizeof *j);
    for (unsigned i = 0; j && i < n && j->n < AC_TRIES; ++i) {
        const char *p = songs[i] ? P(songs[i], REC_PATH) : 0;
        if (p && *p && strlen(p) < sizeof j->track[0])
            tk_snprintf(j->track[j->n++], sizeof j->track[0], "%s", p);
    }
    if (!j || !j->n) {
        free(j);
        return;
    }
    j->key = key;
    unsigned long t = 0;
    ac.busy = 1;
    if (pthread_create(&t, 0, ac_worker, j) || !t) {
        ac.busy = 0;
        free(j);
        return;
    }
    pthread_detach(t);
}

/* The cover of the album whose songs are songs[0..n) (records; songs[0] names it), as a file://
 * URL in url (size bytes); 0, url empty, for none yet. */
int album_cover_url(void *const *songs, unsigned n, char *url, unsigned size) {
    void *r = n ? songs[0] : 0;
    const char *path = r ? P(r, REC_PATH) : 0, *file = 0;
    char art[512];
    unsigned pos, count, key;
    *url = 0;
    if (!path || size < 8) return 0;
    tk_snprintf(url, 8, "file://");
    if (catalogue_cover(path, url + 7, size - 7)) return 1;
    key = album_key(r);
    long have = file_size(art_path(art, key, ""));
    if (have > 0 && strlen(art) < size - 7) {
        tk_snprintf(url + 7, size - 7, "%s", art);
        return 1;
    }
    void *now = queue_now(&pos, &count);
    if (now && tags_key(now_tag(now, REC_ARTIST), now_tag(now, REC_ALBUM)) == key &&
        now_cover(now, &file) && file && *file && strlen(file) < size) {
        /* the player writes every song's cover to one file: AWTK's bitmap of the last one goes */
        image_manager_unload_bitmap_by_name(image_manager(), file);
        tk_snprintf(url, size, "%s", file);
        return 1;
    }
    if (have < 0) ac_fetch(key, songs, n); /* 0: Cover Flow's note of no art */
    *url = 0;
    return 0;
}
