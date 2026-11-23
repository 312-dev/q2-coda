/* Split's slideshow of library covers (docs/ipod.md#home): the picks, off the UI thread. An iPod
 * classic's main menu shows the library's album art in turn beside the list, each cover drifting
 * and zooming slowly and fading into the next; coverflow.c draws and times the slides, and asks
 * here for the next cover. A pick is a random song of the library (a rowid at random, so no sort
 * of the whole table), from the card's songtable or a signed-in server's servertable (catalogue.c)
 * in proportion to their rows; a card song's album's embedded art, else its folder image, a server
 * song's the cover its app keeps for the album (catalogue_cover_large, its 512 px one, else
 * catalogue_cover's 160 px one), each at SLIDE_ART_PX square
 * through stock's own readers under both art locks, written to SLIDE_FILE; else the album's
 * Coverflow thumbnail, softer at that size; else another song, up to SLIDE_TRIES. Albums shown
 * lately (SLIDE_RECENT) are passed over but for the last tries, so a small library still turns.
 * ponytail: songs, not albums, are drawn at random, so an album's chance is its share of the
 * songs; an embedded picture stock decodes whole in demo (a large PNG) costs that here too, as on
 * Now Playing. */
#include "coverflow.h"

extern int era_sql(const char *sql, int (*row)(void *, int, char **, char **), void *ctx);

#define SLIDE_FILE PEQ_ROOT "/tmp/q2-slide.jpg"
#define SLIDE_TMP SLIDE_FILE ".tmp"
/* One song of table at a random rowid or after it, with an album, in the music the library scope
 * shows (MUSIC_WHERE). Each table named in full, so era_sql makes no union of it. */
#define SLIDE_SQL(table)                                                                           \
    "select fileurl,songer,album from " table " where rowid>=(select abs(random())%"              \
    "(max(rowid)+1) from " table ") and ifnull(album,'')<>'' and " MUSIC_WHERE " limit 1"
/* Each table's highest rowid, about its rows, and a seed for the choice between them: the
 * server's while its catalogue is attached (era_sql attaches cat for a query naming it), else the
 * card's alone. */
#define SLIDE_COUNT "select ifnull((select max(rowid) from main.songtable),0),abs(random())"
#define SLIDE_COUNT_CAT SLIDE_COUNT ",ifnull((select max(rowid) from cat.servertable),0)"

enum { PICK_IDLE, PICK_RUNNING, PICK_ANSWERED, PICK_READY };
/* state: PICK_*; kind: the answer, 0 none, 1 SLIDE_FILE, 2 the Coverflow thumbnail of key;
 * recent: the albums lately shown (their names' FNV), next the slot to fill; failed: when a pick
 * last found nothing, | 1, so the next waits SLIDE_RETRY_MS */
static struct {
    unsigned long thread;
    volatile int state;
    int kind;
    unsigned key, recent[SLIDE_RECENT], next, failed;
} sp __attribute__((section(".scratch")));

typedef struct {
    char path[512], artist[256], album[256];
    int got;
} song_t;

static int song_row(void *ctx, int n, char **v, char **c) {
    song_t *s = ctx;
    if (n == 3 && v[0] && v[2]) {
        tk_snprintf(s->path, sizeof s->path, "%s", v[0]);
        tk_snprintf(s->artist, sizeof s->artist, "%s", v[1] ? v[1] : "");
        tk_snprintf(s->album, sizeof s->album, "%s", v[2]);
        s->got = 1;
    }
    return 0;
}

/* SLIDE_COUNT's row: the card's rows, the seed (its digits hashed: a 63-bit number), the
 * server's (0 when not asked). */
static int count_row(void *ctx, int n, char **v, char **c) {
    unsigned *k = ctx;
    for (int i = 0; i < n && i < 3; ++i)
        k[i] = !v[i] ? 0 : i == 1 ? fnv(FNV_SEED, (const unsigned char *)v[i]) : (unsigned)atoi(v[i]);
    return 0;
}

static int shown_lately(unsigned album) {
    for (int i = 0; i < SLIDE_RECENT; ++i)
        if (sp.recent[i] == album) return 1;
    return 0;
}

/* One song's album art at SLIDE_FILE (1), else its Coverflow thumbnail's key in *key (2), else 0.
 * Stock's readers share /tmp/.tmp_picture, which its callers guard with either lock: both held,
 * in album_art's order. */
static int song_art(const song_t *s, unsigned *key) {
    char art[512];
    pthread_mutex_lock((void *)parse_cover_mutex);
    pthread_mutex_lock((void *)g_playcover_mutex);
    /* a server row has no file: its app's cover of the album, the 512 px one when the card has it
     * (the 160 px row cover, scaled up to SLIDE_ART_PX, was blocky: the owner's report, 2026-11-23) */
    int ok = catalogue_path(s->path)
                 ? (catalogue_cover_large(s->path, art, sizeof art) ||
                    catalogue_cover(s->path, art, sizeof art)) &&
                       toolsThumbSpecCover(art, SLIDE_TMP, SLIDE_ART_PX, SLIDE_ART_PX) == 1
                 : toolsGetAlbumCover(s->path, SLIDE_TMP, SLIDE_ART_PX, SLIDE_ART_PX) == 1 ||
                       toolsGetExternCover(s->path, SLIDE_TMP, SLIDE_ART_PX, SLIDE_ART_PX) == 1;
    pthread_mutex_unlock((void *)g_playcover_mutex);
    pthread_mutex_unlock((void *)parse_cover_mutex);
    if (ok && !rename(SLIDE_TMP, SLIDE_FILE)) return 1;
    unlink(SLIDE_TMP);
    *key = tags_key(s->artist, s->album);
    return file_size(art_path(art, *key, "")) > 0 ? 2 : 0;
}

static void *pick_worker(void *unused) {
    song_t *s = calloc(1, sizeof *s);
    int kind = 0;
    unsigned key = 0, album = 0, rows[3] = { 0 };
    thread_lower(0); /* not idle: it takes stock's art locks, which the UI thread waits on */
    if (!era_sql(SLIDE_COUNT_CAT, count_row, rows)) era_sql(SLIDE_COUNT, count_row, rows);
    for (int i = 0; s && i < SLIDE_TRIES && !kind; ++i) {
        /* the card's table or the server's, each in proportion to its rows (an LCG on the seed) */
        rows[1] = rows[1] * 1103515245u + 12345u;
        int server = rows[2] && (rows[1] >> 8) % (rows[0] + rows[2]) >= rows[0];
        s->got = 0;
        if (!era_sql(server ? SLIDE_SQL("cat.servertable") : SLIDE_SQL("main.songtable"), song_row,
                     s) ||
            !s->got)
            continue;
        album = fnv(FNV_SEED, (const unsigned char *)s->album);
        if (i < SLIDE_TRIES - 2 && shown_lately(album)) continue;
        kind = song_art(s, &key);
    }
    free(s);
    if (kind) sp.recent[sp.next++ % SLIDE_RECENT] = album;
    sp.kind = kind, sp.key = key;
    __atomic_store_n(&sp.state, PICK_ANSWERED, __ATOMIC_RELEASE);
    return 0;
}

/* The next cover: 1 with its url in url (at least 600 bytes, "file://...") once a pick is ready
 * and take is set, which takes it; else 0, starting a pick if none runs or waits, so a call
 * without take fetches ahead. A pick that found nothing, a scan running or a thread refused waits
 * SLIDE_RETRY_MS before the next. UI thread. */
int slide_pick(char *url, int take) {
    if (__atomic_load_n(&sp.state, __ATOMIC_ACQUIRE) == PICK_ANSWERED) {
        pthread_join(sp.thread, 0);
        sp.state = sp.kind ? PICK_READY : PICK_IDLE;
        if (!sp.kind) sp.failed = time_now_ms() | 1;
    }
    if (sp.state == PICK_READY) {
        if (!take) return 0;
        sp.state = PICK_IDLE;
        if (sp.kind == 1)
            tk_snprintf(url, 600, "file://%s", SLIDE_FILE);
        else
            art_path(url + tk_snprintf(url, 600, "file://"), sp.key, "");
        return 1;
    }
    if (sp.state != PICK_IDLE || (sp.failed && (int)(time_now_ms() - sp.failed) < SLIDE_RETRY_MS))
        return 0;
    sp.failed = 0;
    if (library_scanning())
        sp.failed = time_now_ms() | 1;
    else if (!pthread_create(&sp.thread, 0, pick_worker, 0))
        sp.state = PICK_RUNNING;
    else
        sp.failed = time_now_ms() | 1;
    return 0;
}
