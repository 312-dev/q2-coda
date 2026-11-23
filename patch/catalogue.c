/* The server library (docs/internals.md#server-library, docs/server-library.md): a card app that
 * knows a music server (q2app catalogue=1) writes the server's songs and playlists to
 * /mnt/mmc/.<id>/catalogue.tsv, and while the app is signed in (its credentials file on the card)
 * they join the Library's lists beside the card's; there is no setting (the user's, 2026-11-19).
 * Signed out, or the file gone, a job takes the app's rows out. The firmware never speaks to the
 * server.
 *
 * Where they are kept: /mnt/mmc/.q2pod/catalogue.db, attached as schema cat to stock's shared
 * connection (stock_db_open) and to Era's, so library_query's union reads servertable there.
 * /mnt/data is too small for a large server's rows. A job on a pthread (cat_worker) writes a whole
 * new file beside it, catalogue.db.new, from the old one and the app's file; the UI thread then
 * detaches the old, renames the new over it and attaches again (cat_swap), so no reader ever waits
 * on a long write and none sees half of one.
 *
 * A job imports an app's catalogue when its seq changes, or matches again when the card's library
 * changed (library_gen moved: Update Local Music, a scan after files arrived). Matching hides a
 * server song that is already a card file (cat_match: MusicBrainz id, then the same file, then the
 * same tags) and links its sid to that file. Then the job builds the in-memory index the UI reads
 * (cat_ix: by the path's 64-bit FNV-1a, each server row's or linked card file's sid, seconds and
 * offline copy), which the swap takes.
 *
 * On the UI thread: availability (an offline copy, or online: Wi-Fi up, or Smart Wi-Fi bringing it
 * back for a press, and the app's server=up),
 * catalogue.avail's changes to it, greyed rows, the play hand-off (catplay, its app started when
 * it isn't running: addon.c addon_wake), the offline queue, and the ratings channel (ratings.out,
 * ratings.in). */
#include "navigation.h"
#include "stream_path.h"

#define CAT_DIR "/mnt/mmc/.q2pod"
#define CAT_DB CAT_DIR "/catalogue.db"
#define CAT_NEW CAT_DIR "/catalogue.db.new"
#define CAT_APPS 4
#define CAT_ID_BASE 0x40000000 /* server rows' ids: above any songtable id stock gives */
#define CAT_APP_SPAN 0x01000000
#define CAT_POLL_MS 1000
#define CAT_DARK_MS 10000 /* the poll's pace while dark, nothing playing and no catplay waiting */
#define CAT_SCAN_MS 10000 /* the card's apps looked for again this often */
#define CAT_LINE 8192     /* a catalogue line read this far; a longer one is skipped */
#define CAT_BATCH 64      /* rows per insert statement */
#define CAT_SQL (CAT_BATCH * 2600)
#define CATPLAY_WAIT_MS 4000
#define CATPLAY_START_MS 15000 /* a catalogue app started for a catplay has this long to listen */
#define CATPLAY_SHOW_MS 60000  /* Now Playing shows a pressed server song this long at most */
#define CAT_GREY 0x666666      /* an unavailable row's text */
#define CAT_DIM 0xb3           /* on the selection: its own ink at this alpha (70%) */
#define CAT_GREY_ROWS 48       /* bound rows of stock's pooled tables, more than a screen holds */
#define CAT_PLAYLISTS 100      /* stock's playlist headers are ids 0 to 99 */
#define CAT_ART_PX 18          /* OLED's cloud and arrow (tools/art.py MARK_PX) */

typedef struct {
    unsigned long long key; /* the path's FNV-1a 64: a server row's fileurl, a linked card file's */
    unsigned sid, local;    /* offsets in the index's strings, 0 for none */
    unsigned path;          /* the path itself */
    int sec;                /* the catalogue's seconds */
    /* a server row's album, artist, album artist and genre, each its group_hash; 0 for a linked
     * card file */
    unsigned grp[4];
    unsigned char app, dup; /* the app's slot; dup: a card file the match linked */
} cat_ent;

/* The groups a Library row can stand for (an album's, an artist's or a genre's row): a server
 * row's names in each, as cat_ent.grp holds them (an artist both its artist and album artist). */
enum { G_ALBUM, G_ARTIST, G_GENRE, G_KINDS };
typedef struct {
    unsigned *h; /* group_hash values, sorted, each once */
    unsigned n;
} cat_set;

typedef struct {
    cat_ent *e;
    unsigned n;
    char *str; /* the strings, from offset 1 */
    unsigned len, cap;
    unsigned *by_sid;         /* e's indices, by sid_key */
    cat_set cards[G_KINDS];   /* every card song's names, by group */
    cat_set servers[G_KINDS]; /* every server row's */
    unsigned apps;            /* a bit per app slot with rows in it (cat_ent.app) */
} cat_ix;

struct cat_copy {
    unsigned long long key; /* the copy's path, fnv64 */
    unsigned at;            /* its entry in cat.ix */
};
struct cat_copies {
    unsigned n, gen; /* gen: the local_gen it was made for */
    struct cat_copy c[];
};

typedef struct {
    char id[16];
    int ratings;   /* q2app ratings=: 1 the server's hearts in, 2 also stars and likes out */
    char dl[32];   /* q2app downloads=: the card folder it downloads songs to, "" for none */
    char seq[24];  /* the catalogue's seq taken last */
    long avail_at; /* catalogue.avail read this far */
    int down;      /* its state file's server=down */
    unsigned play; /* its .play, hashed, when a catplay went */
} cat_app_t;

static struct {
    cat_app_t app[CAT_APPS];
    int napps;
    unsigned scanned, polled;
    int online;
    /* the job: its thread, running, done (set by the thread), and what it was given and found */
    unsigned long thread;
    int running;
    volatile int done;
    int job_app; /* the app imported, -1 for a match alone */
    char job_seq[24];
    char job_why[96]; /* its error, empty when it went well */
    unsigned job_rows;
    cat_ix job_ix; /* the index it built */
    unsigned gen;  /* library_gen the last job matched against */
    int pending;   /* a match is due once no scan runs */
    cat_ix ix;
    /* the server rows' names with an offline copy, by group (group_unavailable); built again when
     * local_gen has moved since (an import, catalogue.avail) */
    cat_set local[G_KINDS];
    unsigned local_gen, local_built;
    /* the entries with an offline copy by the copy's path hash (cat_copy_of), made on the UI
     * thread each time local_gen moves (copies_build); stock's cover thread reads it too, so a new
     * one is published whole and the one before freed only at the next build */
    struct cat_copies *copies, *copies_old;
    void **stock_slot; /* stock's handle's global (stock_db_open's a1) */
    void *attached;    /* the stock handle cat is attached to */
    void *grey[CAT_GREY_ROWS];
    unsigned
        grey_ink[CAT_GREY_ROWS]; /* each greyed row's text colour as last painted, for its mark */
    unsigned char grey_mark[CAT_GREY_ROWS]; /* each row's MK_* */
    unsigned noff;                          /* how many of them are MK_OFFLINE's */
    unsigned ngrey;
    int fitted; /* a greyed row's labels were narrowed (catalogue_fit) since the Q2 started */
    /* a catplay sent: its app, when, and the list to play offline if no answer comes; cp_wake:
     * when its app was started for it, the catplay not written until the app listens; cp_link:
     * Smart Wi-Fi's link awaited first (smart_wifi_wait), the app not started until it is up */
    int cp_app, cp_link;
    unsigned cp_at, cp_seq, cp_wake;
    void *cp_list;
    int cp_start;
    /* Now Playing's song from a press until the player parses one (catalogue_np_now): its record
     * (one copied into np_list), its place in its list, the parse count and time it began at, and
     * the title and artist labels' text as set last, hashed; np_back: shown no more, the player's
     * own title and artist still to be written (catalogue_np_text) */
    void *np_list;
    unsigned np_at, np_n, np_parses, np_since;
    unsigned np_text[2];
    int np_back;
    char np_cover[160]; /* its art: "file://" and the app's cover, or stock's placeholder */
    /* cat_row_of's last answer, on the UI thread: the path's fnv64, local_gen plus 1 when it was
     * found, and the entry's index plus 1 (0 for none). Now Playing's heart asks for the playing
     * copy twice a second (ratings.c rate_heart). */
    unsigned long long row_path;
    unsigned row_gen, row_at;
    /* catalogue_row_fav's last answer: the row asked (row_at), local_gen plus 1 and fav_gen when
     * it was read, and whether the row's path was in myfav. fav_gen moves with each My Fav change
     * the Q2 makes and each Now Playing open (catalogue_fav_changed). */
    unsigned fav_row, fav_local, fav_seen, fav_gen;
    int fav_on;
} cat __attribute__((section(".scratch")));

static unsigned long long fnv64(const char *s) {
    unsigned long long h = 0xcbf29ce484222325ull;
    while (s && *s) h = (h ^ (unsigned char)*s++) * 0x100000001b3ull;
    return h;
}

/* ---- The index (built on the job's thread, read on the UI thread once swapped in) ---- */

static void ix_free(cat_ix *x) {
    free(x->e), free(x->str), free(x->by_sid);
    for (int k = 0; k < G_KINDS; ++k) free(x->cards[k].h), free(x->servers[k].h);
    memset(x, 0, sizeof *x);
}
static unsigned ix_put(cat_ix *x, const char *s) {
    if (!s || !*s) return 0;
    unsigned k = strlen(s) + 1;
    if (!x->cap) x->len = 1; /* offset 0 is none */
    if (x->len + k > x->cap) {
        unsigned cap = (x->cap + k) * 2;
        char *str = realloc(x->str, cap);
        if (!str) return 0;
        x->str = str, x->cap = cap;
    }
    memcpy(x->str + x->len, s, k);
    x->len += k;
    return x->len - k;
}
static int ent_cmp(const void *a, const void *b) {
    unsigned long long x = ((const cat_ent *)a)->key, y = ((const cat_ent *)b)->key;
    return x < y ? -1 : x > y;
}
static cat_ent *ix_find(const cat_ix *x, unsigned long long key) {
    unsigned lo = 0, hi = x->n;
    while (lo < hi) {
        unsigned mid = (lo + hi) / 2;
        if (x->e[mid].key < key)
            lo = mid + 1;
        else
            hi = mid;
    }
    return lo < x->n && x->e[lo].key == key ? &x->e[lo] : 0;
}
static const char *ix_str(const cat_ix *x, unsigned off) { return off ? x->str + off : ""; }
static const cat_ix *sid_ix;
static int sid_cmp(const void *a, const void *b) {
    const cat_ent *x = &sid_ix->e[*(const unsigned *)a], *y = &sid_ix->e[*(const unsigned *)b];
    if (x->app != y->app) return x->app < y->app ? -1 : 1;
    return strcmp(ix_str(sid_ix, x->sid), ix_str(sid_ix, y->sid));
}
/* app's sid's entry, or 0: a server row's, else a linked card file's. */
static cat_ent *ix_sid(const cat_ix *x, int app, const char *sid) {
    unsigned lo = 0, hi = x->n;
    while (lo < hi && x->by_sid) {
        unsigned mid = (lo + hi) / 2;
        const cat_ent *e = &x->e[x->by_sid[mid]];
        int c = e->app != app ? (e->app < app ? -1 : 1) : strcmp(ix_str(x, e->sid), sid);
        if (c < 0)
            lo = mid + 1;
        else
            hi = mid;
    }
    cat_ent *e = lo < x->n && x->by_sid ? &x->e[x->by_sid[lo]] : 0;
    return e && e->app == app && !strcmp(ix_str(x, e->sid), sid) ? e : 0;
}
static int u32_cmp(const void *a, const void *b) {
    unsigned x = *(const unsigned *)a, y = *(const unsigned *)b;
    return x < y ? -1 : x > y;
}
/* h into set, unsorted (set_sort sorts it); 0 when out of memory. */
static int set_add(cat_set *set, unsigned h) {
    if (!h) return 1;
    if (!(set->n & 1023)) {
        unsigned *a = realloc(set->h, (set->n + 1024) * sizeof *a);
        if (!a) return 0;
        set->h = a;
    }
    set->h[set->n++] = h;
    return 1;
}
static void set_sort(cat_set *set) {
    if (set->n) qsort(set->h, set->n, sizeof *set->h, u32_cmp);
    unsigned k = 0;
    for (unsigned i = 0; i < set->n; ++i)
        if (!k || set->h[k - 1] != set->h[i]) set->h[k++] = set->h[i];
    set->n = k;
}
static int set_has(const cat_set *set, unsigned h) {
    unsigned lo = 0, hi = set->n;
    while (lo < hi) {
        unsigned mid = (lo + hi) / 2;
        if (set->h[mid] < h)
            lo = mid + 1;
        else
            hi = mid;
    }
    return lo < set->n && set->h[lo] == h;
}
/* A server row's names into sets, by group: its artist and album artist both the artist's. */
static int ent_groups(const cat_ent *e, cat_set *sets) {
    return set_add(&sets[G_ALBUM], e->grp[0]) && set_add(&sets[G_ARTIST], e->grp[1]) &&
           set_add(&sets[G_ARTIST], e->grp[2]) && set_add(&sets[G_GENRE], e->grp[3]);
}
static int ix_sort(cat_ix *x) {
    qsort(x->e, x->n, sizeof *x->e, ent_cmp);
    if (!(x->by_sid = calloc(x->n + 1, sizeof *x->by_sid))) return 0;
    for (unsigned i = 0; i < x->n; ++i) x->by_sid[i] = i;
    sid_ix = x; /* the job's thread alone sorts */
    qsort(x->by_sid, x->n, sizeof *x->by_sid, sid_cmp);
    for (unsigned i = 0; i < x->n; ++i)
        if (!x->e[i].dup && !ent_groups(&x->e[i], x->servers)) return 0;
    for (int k = 0; k < G_KINDS; ++k) set_sort(&x->cards[k]), set_sort(&x->servers[k]);
    return 1;
}
/* A name as the Library's lists group it (COLLATE NOCASE: ASCII letters folded): its FNV-1a 32,
 * 0 for none. */
static unsigned group_hash(const char *s) {
    unsigned h = FNV_SEED;
    if (!s || !*s) return 0;
    for (; *s; ++s) h = (h ^ (unsigned char)(*s >= 'A' && *s <= 'Z' ? *s + 32 : *s)) * 16777619u;
    return h ? h : 1;
}

/* ---- Normalised tags, for the match (docs/server-library.md#duplicates) ---- */

/* An edition note in brackets or after " - ": cut when it names one of these, unless it says
 * live (a live take is another recording). */
static int edition_note(const char *s, unsigned n) {
    static const char *const words[] = { "remaster", "mix",    "edition", "version",    "mono",
                                         "stereo",   "deluxe", "bonus",   "anniversary" };
    char low[96];
    unsigned k = n < sizeof low - 1 ? n : sizeof low - 1;
    for (unsigned i = 0; i < k; ++i) low[i] = (char)(s[i] >= 'A' && s[i] <= 'Z' ? s[i] + 32 : s[i]);
    low[k] = 0;
    if (strstr(low, "live")) return 0;
    for (unsigned i = 0; i < sizeof words / sizeof *words; ++i)
        if (strstr(low, words[i])) return 1;
    return 0;
}
/* s normalised into out: a "feat." part and trailing edition notes cut, then lower case, letters
 * and digits only (UTF-8's other bytes kept). Its FNV-1a 32, 0 for an empty one. */
unsigned cat_norm(const char *s, char *out, unsigned size) {
    unsigned n = s ? strlen(s) : 0, k = 0;
    char low[512];
    if (n >= sizeof low) n = sizeof low - 1;
    for (unsigned i = 0; i < n; ++i) low[i] = (char)(s[i] >= 'A' && s[i] <= 'Z' ? s[i] + 32 : s[i]);
    low[n] = 0;
    static const char *const feats[] = { " feat.", " feat ", " ft.",       "(feat.",
                                         "[feat.", "(ft.",   " featuring " };
    for (unsigned i = 0; i < sizeof feats / sizeof *feats; ++i) {
        char *f = strstr(low, feats[i]);
        if (f && f > low) *f = 0, n = (unsigned)(f - low);
    }
    for (int again = 1; again && n;) { /* trailing notes, the last first */
        again = 0;
        while (n && low[n - 1] == ' ') low[--n] = 0;
        char close = n ? low[n - 1] : 0, open = close == ')' ? '(' : close == ']' ? '[' : 0;
        if (open) {
            unsigned i = n - 1;
            while (i && low[i] != open) --i;
            if (low[i] == open && i && edition_note(low + i, n - i)) low[n = i] = 0, again = 1;
        } else {
            char *d = 0;
            for (char *p = strstr(low, " - "); p; p = strstr(p + 1, " - ")) d = p;
            if (d && d > low && edition_note(d + 3, n - (unsigned)(d - low) - 3))
                *d = 0, n = (unsigned)(d - low), again = 1;
        }
    }
    unsigned h = FNV_SEED;
    for (unsigned i = 0; i < n; ++i) {
        unsigned char c = (unsigned char)low[i];
        if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c >= 0x80)) continue;
        if (out && k + 1 < size) out[k] = (char)c;
        ++k;
        h = (h ^ c) * 16777619u;
    }
    if (out && size) out[k < size ? k : size - 1] = 0;
    return k ? h | !h : 0;
}

/* ---- Card files, read only for a match's candidates ---- */

/* The file's MusicBrainz recording id into out (37 bytes), empty if none: a Vorbis comment's
 * MUSICBRAINZ_TRACKID (FLAC, Ogg), or ID3's UFID frame owned by http://musicbrainz.org. Only the
 * first CAT_TAGS bytes: tags come first, before pictures as Picard writes them.
 * ponytail: a FLAC whose pictures come before its comments is not read past CAT_TAGS. */
#define CAT_TAGS (128 << 10)
void cat_mbid(const char *path, char *out) {
    out[0] = 0;
    void *f = fopen(path, "rb");
    char *b = f ? calloc(1, CAT_TAGS + 1) : 0;
    unsigned n = b ? fread(b, 1, CAT_TAGS, f) : 0;
    if (f) fclose(f);
    if (!b) return;
    static const char vorbis[] = "musicbrainz_trackid=", ufid[] = "UFID";
    static const char owner[] = "http://musicbrainz.org";
    for (unsigned i = 0; i + sizeof vorbis - 1 + 36 <= n && !out[0]; ++i) {
        if ((b[i] | 32) == 'm' && !strncasecmp(b + i, vorbis, sizeof vorbis - 1)) {
            memcpy(out, b + i + sizeof vorbis - 1, 36), out[36] = 0;
        } else if (b[i] == 'U' && !memcmp(b + i, ufid, 4) && i + 10 + sizeof owner + 36 <= n &&
                   !memcmp(b + i + 10, owner, sizeof owner)) { /* the frame's owner, its NUL */
            memcpy(out, b + i + 10 + sizeof owner, 36), out[36] = 0;
        }
    }
    for (unsigned i = 0; out[i]; ++i) /* an id is hex and dashes; anything else is not one */
        if (!((out[i] >= '0' && out[i] <= '9') || ((out[i] | 32) >= 'a' && (out[i] | 32) <= 'f') ||
              out[i] == '-'))
            out[0] = 0;
    free(b);
}
static long cat_size(const char *path) {
    unsigned sx[64]; /* struct statx: stx_size at byte 40 */
    if (ringnav_statx(-100, path, 0, 0x201 /* TYPE, SIZE */, sx) || (sx[7] & 0170000) != 0100000)
        return -1;
    return (long)sx[10];
}
static int cat_seconds(const char *path) {
    void *f = taglib_file_new(path, 1);
    void *p = f ? taglib_file_audioproperties(f) : (void *)0;
    int sec = p ? ((int (*)(void *))(*(void ***)p)[3])(p) : 0; /* AudioProperties::length() */
    if (f) taglib_file_free(f);
    return sec > 0 ? sec : -1;
}

/* ---- The job (its own connection; touches only files, the database and its own fields) ---- */

typedef struct {
    char *path;
    unsigned title, artist, aa, album; /* cat_norm's hashes */
    int track;
    long size; /* -2 not read yet */
    int sec;   /* -2 not read yet */
    char mbid[37];
    int mbid_read;
} card_t;
typedef struct {
    void *db;
    char *sql;
    unsigned len;
    int rows, bad;
    card_t *card;
    unsigned ncard, cap;
    unsigned *by_size, nsize;  /* untagged card songs, by size */
    unsigned *dup, ndup, dcap; /* matched: a server row's id and its card row, in pairs */
    cat_ix *ix;
} job_t;
static job_t jb __attribute__((section(".scratch")));

/* SQL is built piece by piece into a buffer whose end is CAT_SLACK short of its size: a piece
 * that does not fit leaves o at end or past it (a separator's byte or two lands in the slack), and
 * a statement whose o is not short of end is never run, so a cut one (a delete without its where)
 * can't be. */
#define CAT_SLACK 64
static unsigned room(const char *o, const char *end) { return o < end ? (unsigned)(end - o) : 0; }
static char *clip(char *o, char *end, int n) {
    return o >= end ? o : n >= 0 && (unsigned)n < room(o, end) ? o + n : end;
}
#define PUT(o, end, ...) ((o) = clip((o), (end), tk_snprintf((o), room((o), (end)), __VA_ARGS__)))
/* s quoted for SQL into o, as far as end, unterminated: the caller ends the string (a PUT after
 * it, or *o = 0). */
static char *sq(char *o, char *end, const char *s) {
    if (o + 2 >= end) return o > end ? o : end;
    *o++ = '\'';
    for (; s && *s; ++s) {
        if (o + 3 >= end) return end;
        if (*s == '\'') *o++ = '\'';
        *o++ = *s;
    }
    *o++ = '\'';
    return o;
}
#define JEND (jb.sql + CAT_SQL - CAT_SLACK)
static int job_exec(const char *sql) {
    return sqlite3_exec(jb.db, sql, 0, 0, 0) ? (jb.bad = 1) : 0;
}
/* jb.sql as built up to o, run when it fit. */
static int job_run(char *o) {
    if (o >= JEND) return jb.bad = 1;
    *o = 0;
    return job_exec(jb.sql);
}

static int card_row(void *ctx, int n, char **v, char **c) {
    if (jb.ncard == jb.cap) {
        unsigned cap = jb.cap ? jb.cap * 2 : 1024;
        card_t *m = realloc(jb.card, cap * sizeof *m);
        if (!m) return jb.bad = 1;
        jb.card = m, jb.cap = cap;
    }
    card_t *k = &jb.card[jb.ncard];
    memset(k, 0, sizeof *k);
    if (!v[0] || !(k->path = strdup(v[0]))) return 0;
    k->title = cat_norm(v[1], 0, 0), k->artist = cat_norm(v[2], 0, 0);
    k->aa = cat_norm(v[3], 0, 0), k->album = cat_norm(v[4], 0, 0);
    k->track = v[5] ? atoi(v[5]) : 0;
    k->size = k->sec = -2;
    ++jb.ncard;
    return 0;
}
static int card_cmp(const void *a, const void *b) {
    unsigned x = ((const card_t *)a)->title, y = ((const card_t *)b)->title;
    return x < y ? -1 : x > y;
}
static int size_cmp(const void *a, const void *b) {
    long x = jb.card[*(const unsigned *)a].size, y = jb.card[*(const unsigned *)b].size;
    return x < y ? -1 : x > y;
}
static void cards_free(void) {
    for (unsigned i = 0; i < jb.ncard; ++i) free(jb.card[i].path);
    free(jb.card), free(jb.by_size), free(jb.dup);
    jb.card = 0, jb.by_size = jb.dup = 0;
    jb.ncard = jb.cap = jb.nsize = jb.ndup = jb.dcap = 0;
}
/* The card's songs, by normalised title; the untagged (no title) by size as well. */
static int cards_read(void) {
    if (sqlite3_exec(jb.db, "select fileurl,name,songer,albumsonger,album,track from songtable",
                     card_row, 0, 0)) {
        jb.ncard = 0; /* no songtable before the first scan: nothing on the card to match */
        return !jb.bad;
    }
    qsort(jb.card, jb.ncard, sizeof *jb.card, card_cmp);
    unsigned untagged = 0;
    for (unsigned i = 0; i < jb.ncard; ++i) untagged += !jb.card[i].title;
    if (untagged && !(jb.by_size = calloc(untagged, sizeof *jb.by_size))) return 0;
    for (unsigned i = 0; i < jb.ncard; ++i)
        if (!jb.card[i].title && (jb.card[i].size = cat_size(jb.card[i].path)) > 0)
            jb.by_size[jb.nsize++] = i;
    qsort(jb.by_size, jb.nsize, sizeof *jb.by_size, size_cmp);
    return !jb.bad;
}

enum { M_NONE, M_MBID, M_FILE, M_TAGS };
/* Whether server song s (servermeta's columns, as cat_server_row has them) is card song k, and
 * by which layer. A layer that disagrees stops the weaker ones (docs/server-library.md). */
typedef struct {
    unsigned title, artist, aa, album;
    int track, sec;
    long size;
    const char *suffix, *mbid;
} srv_t;
int cat_same(const srv_t *s, card_t *k) {
    if (s->mbid && *s->mbid) {
        if (!k->mbid_read) cat_mbid(k->path, k->mbid), k->mbid_read = 1;
        if (k->mbid[0]) return strcasecmp(s->mbid, k->mbid) ? M_NONE : M_MBID;
    }
    if (k->size == -2) k->size = cat_size(k->path);
    if (k->sec == -2) k->sec = cat_seconds(k->path);
    const char *ext = strrchr(k->path, '.');
    int suffix = ext && s->suffix && !strcasecmp(ext + 1, s->suffix);
    int d = k->sec < 0 || s->sec <= 0 ? 1 << 30
            : k->sec > s->sec         ? k->sec - s->sec
                                      : s->sec - k->sec;
    if (suffix && k->size > 0 && k->size == s->size && d <= 1) return M_FILE;
    /* a size that differs is no disagreement (a retagged copy, new art); a length more than 2 s off
     * is, and fails the tags too: a live take beside the studio one */
    int tags = s->title && k->title == s->title && k->album == s->album &&
               (k->artist == s->artist || (k->aa && k->aa == s->aa)) && k->track == s->track;
    return tags && d <= 2 ? M_TAGS : M_NONE;
}
static void dup_add(unsigned id, unsigned card) {
    if (jb.ndup + 2 > jb.dcap) {
        unsigned cap = jb.dcap ? jb.dcap * 2 : 256;
        unsigned *d = realloc(jb.dup, cap * sizeof *d);
        if (!d) {
            jb.bad = 1;
            return;
        }
        jb.dup = d, jb.dcap = cap;
    }
    jb.dup[jb.ndup++] = id, jb.dup[jb.ndup++] = card;
}
/* servermeta's id,title,artist,aa,album,track,seconds,size,suffix,mbid */
static int cat_server_row(void *ctx, int n, char **v, char **c) {
    srv_t s = { (unsigned)atoi(v[1] ?: "0"),
                (unsigned)atoi(v[2] ?: "0"),
                (unsigned)atoi(v[3] ?: "0"),
                (unsigned)atoi(v[4] ?: "0"),
                atoi(v[5] ?: "0"),
                atoi(v[6] ?: "0"),
                atoi(v[7] ?: "0"),
                v[8],
                v[9] };
    unsigned id = (unsigned)atoi(v[0]);
    unsigned lo = 0, hi = jb.ncard;
    while (lo < hi) /* the first of that title */
        if (jb.card[(lo + hi) / 2].title < s.title)
            lo = (lo + hi) / 2 + 1;
        else
            hi = (lo + hi) / 2;
    for (unsigned i = lo; s.title && i < jb.ncard && jb.card[i].title == s.title; ++i)
        if (cat_same(&s, &jb.card[i])) {
            dup_add(id, i);
            return jb.bad;
        }
    lo = 0, hi = jb.nsize; /* the untagged: the same file only */
    while (lo < hi)
        if (jb.card[jb.by_size[(lo + hi) / 2]].size < s.size)
            lo = (lo + hi) / 2 + 1;
        else
            hi = (lo + hi) / 2;
    for (unsigned i = lo; i < jb.nsize && jb.card[jb.by_size[i]].size == s.size; ++i)
        if (cat_same(&s, &jb.card[jb.by_size[i]]) == M_FILE) {
            dup_add(id, jb.by_size[i]);
            break;
        }
    return jb.bad;
}

/* Each server song in nw matched against the card; dup set on those that are card files, and
 * servertable rebuilt from serverall without them. */
static void cat_match(void) {
    if (!cards_read() ||
        sqlite3_exec(jb.db,
                     "select id,ntitle,nartist,naa,nalbum,track,seconds,size,suffix,mbid from "
                     "nw.servermeta",
                     cat_server_row, 0, 0))
        jb.bad = 1;
    job_exec("update nw.servermeta set dup=null");
    for (unsigned i = 0; !jb.bad && i < jb.ndup; i += 2) {
        char *o = jb.sql;
        PUT(o, JEND, "update nw.servermeta set dup=");
        o = sq(o, JEND, jb.card[jb.dup[i + 1]].path);
        PUT(o, JEND, " where id=%u", jb.dup[i]);
        job_run(o);
    }
    job_exec("delete from nw.servertable;insert into nw.servertable select s.* from nw.serverall s "
             "join nw.servermeta m on m.id=s.id where m.dup is null");
    cards_free();
}

/* The percent escapes of the card-app channel undone in place (docs/librespot.md). */
static int hexv(char c) {
    return c >= '0' && c <= '9'                 ? c - '0'
           : (c | 32) >= 'a' && (c | 32) <= 'f' ? (c | 32) - 'a' + 10
                                                : -1;
}
static void unescape(char *s) {
    char *o = s;
    for (; *s; ++s)
        if (*s == '%' && hexv(s[1]) >= 0 && hexv(s[2]) >= 0)
            *o++ = (char)(hexv(s[1]) << 4 | hexv(s[2])), s += 2;
        else
            *o++ = *s;
    *o = 0;
}
/* line split at tabs into f (at most max), unescaped; how many. Shared with storage.c. */
int tsv_split(char *line, char **f, int max) {
    int n = 0;
    for (char *p = line; n < max;) {
        f[n++] = p;
        char *t = strstr(p, "\t");
        if (!t) break;
        *t = 0, p = t + 1;
    }
    for (int i = 0; i < n; ++i) unescape(f[i]);
    return n;
}

/* The letter stock files a title under (songtable's Initial): its first letter upper-cased, else
 * '#'. ponytail: stock gives a CJK title its pinyin's letter; here those are '#'. */
static int initial(const char *t) {
    unsigned char c = t ? (unsigned char)*t : 0;
    if (c >= 'a' && c <= 'z') c -= 32;
    return c >= 'A' && c <= 'Z' ? c : '#';
}

typedef struct {
    int slot;
    const char *id;
    unsigned next; /* the next row's id */
    unsigned pending;
    char *o;    /* the open insert's end */
    char *meta; /* servermeta's insert, built beside it */
    char *mo;
    unsigned lists; /* catalogue playlists, for the playlist pass */
} imp_t;

#define MEND(im) ((im)->meta + CAT_SQL - CAT_SLACK)
/* The open inserts run, if any: each whole, or the import fails (job_run). */
static void imp_flush(imp_t *im) {
    if (!im->pending) return;
    im->pending = 0;
    if (im->mo >= MEND(im)) {
        jb.bad = 1;
        return;
    }
    *im->mo = 0;
    if (!job_run(im->o)) job_exec(im->meta);
}
/* An s row: serverall's row with songtable's columns, servermeta's with the rest. */
static void imp_song(imp_t *im, char **f, int n) {
    /* s sid title artist albumartist album albumid year disc track seconds genre suffix local
     * size mbid: the last three may be missing (an app before them) */
    if (n < 13 || !*f[1] || !*f[6] || !*f[12] || strstr(f[1], "/") || strstr(f[6], "/") ||
        strstr(f[12], "/") || !strcmp(f[1], "..") || !strcmp(f[6], ".."))
        return; /* no sid, album id or suffix, or one that is not a plain name */
    /* a row is at most a line's fields, each quote doubled, and the url and name made from them */
    if (im->pending &&
        (room(im->o, JEND) < 2 * CAT_LINE + 2048 || room(im->mo, MEND(im)) < 2 * CAT_LINE + 2048))
        imp_flush(im);
    char *o = im->o, *end = JEND, *mend = MEND(im);
    if (!im->pending) {
        o = jb.sql, im->mo = im->meta;
        PUT(o, end, "insert into nw.serverall values ");
        PUT(im->mo, mend, "insert into nw.servermeta values ");
    } else
        *o++ = ',', *im->mo++ = ',';
    char url[512], name[600];
    tk_snprintf(url, sizeof url, "/mnt/mmc/.%s/cat/%s/%s.%s", im->id, f[6], f[1], f[12]);
    /* filename: the title, which stock's Search lists a song by (a card file's name; a server row
     * has no file, and "<title>.<suffix>" read as one) */
    tk_snprintf(name, sizeof name, "%s", *f[2] ? f[2] : f[1]);
    const char *local = n > 13 && card_path(f[13]) ? f[13] : "";
    unsigned id = im->next++;
    PUT(o, end, "(%u,", id);
    o = sq(o, end, name), *o++ = ',';
    o = sq(o, end, url), *o++ = ',';
    o = sq(o, end, f[5]), *o++ = ',';  /* album */
    o = sq(o, end, f[3]), *o++ = ',';  /* songer: the artist */
    o = sq(o, end, f[11]), *o++ = ','; /* style: the genre */
    o = sq(o, end, f[2]), *o++ = ',';  /* name: the title */
    o = sq(o, end, ""), *o++ = ',';    /* composer */
    o = sq(o, end, f[4]), *o++ = ',';  /* albumsonger */
    PUT(o, end, "%d,%d,%d,0,0,0,%d,0,0)", atoi(f[7]), atoi(f[9]), atoi(f[8]), initial(f[2]));
    im->o = o;
    o = im->mo;
    PUT(o, mend, "(%u,", id);
    o = sq(o, mend, url), *o++ = ',';
    o = sq(o, mend, im->id), *o++ = ',';
    o = sq(o, mend, f[1]), *o++ = ',';
    if (*local)
        o = sq(o, mend, local);
    else
        PUT(o, mend, "null");
    PUT(o, mend, ",%d,%d,", atoi(f[10]), n > 14 ? atoi(f[14]) : 0);
    o = sq(o, mend, n > 15 ? f[15] : ""), *o++ = ',';
    o = sq(o, mend, f[12]);
    /* the normalised tags' hashes, as signed ints: SQLite's and atoi's range */
    PUT(o, mend, ",null,%d,%d,%d,%d,%d)", (int)cat_norm(f[2], 0, 0), (int)cat_norm(f[3], 0, 0),
        (int)cat_norm(f[4], 0, 0), (int)cat_norm(f[5], 0, 0), atoi(f[9]));
    im->mo = o;
    if (++im->pending == CAT_BATCH) imp_flush(im);
}

/* A p or i row, kept for the playlist pass: nw.serverpl(app, pid, name) and serveritem(app, pid,
 * sid, path), in the file's order; path a synced song's card file, which has no s row. */
static void imp_list(imp_t *im, char **f, int n) {
    char *o = jb.sql;
    if (n < 3) return;
    imp_flush(im);
    if (f[0][0] == 'p') {
        PUT(o, JEND, "insert into nw.serverpl values (");
        o = sq(o, JEND, im->id), *o++ = ',', o = sq(o, JEND, f[1]), *o++ = ',';
        o = sq(o, JEND, f[2]);
        ++im->lists;
    } else {
        PUT(o, JEND, "insert into nw.serveritem values (");
        o = sq(o, JEND, im->id), *o++ = ',', o = sq(o, JEND, f[1]), *o++ = ',';
        o = sq(o, JEND, f[2]), *o++ = ',';
        o = sq(o, JEND, n > 3 && card_path(f[3]) ? f[3] : "");
    }
    PUT(o, JEND, ")");
    job_run(o);
}

static const char *const SCHEMA =
    "create table if not exists nw.serverall(id integer,filename,fileurl,album,songer,style,name,"
    "composer,albumsonger,year integer,track integer,cd_num integer,time integer,time_end "
    "integer,time_create integer,Initial integer,bitdepth integer,samprate integer);"
    "create table if not exists nw.servertable as select * from nw.serverall where 0;"
    "create table if not exists nw.servermeta(id integer primary key,fileurl,app,sid,local,"
    "seconds integer,size integer,mbid,suffix,dup,ntitle integer,nartist integer,naa integer,"
    "nalbum integer,track integer);"
    "create table if not exists nw.serverpl(app,pid,name);"
    "create table if not exists nw.serveritem(app,pid,sid,path);"
    "create table if not exists nw.serverlists(app,pid,list integer,twin integer);"
    "create index if not exists nw.servertable_album on servertable(album collate nocase);"
    "create index if not exists nw.servermeta_url on servermeta(fileurl);"
    /* the UI's lookups by a row's path: Up Next's tags, Now Playing's info, My Fav's insert */
    "create index if not exists nw.serverall_url on serverall(fileurl);"
    "create index if not exists nw.servertable_url on servertable(fileurl);";

/* The job's copy of the old file's tables into nw, less app's rows when it is imported again
 * (serverlists kept: which stock playlist is the app's stays known across imports). */
static void copy_old(const char *skip) {
    if (sqlite3_exec(jb.db, "select 1 from cat.servermeta limit 0", 0, 0, 0)) return; /* none */
    static const char *const tables[] = { "serverall", "servermeta", "serverpl", "serveritem",
                                          "serverlists" };
    for (unsigned i = 0; i < sizeof tables / sizeof *tables; ++i) {
        char *o = jb.sql;
        PUT(o, JEND, "insert into nw.%s select * from cat.%s", tables[i], tables[i]);
        if (skip && i == 0)
            PUT(o, JEND, " where id not in (select id from cat.servermeta where app="),
                o = sq(o, JEND, skip), PUT(o, JEND, ")");
        else if (skip && i < 4)
            PUT(o, JEND, " where app<>"), o = sq(o, JEND, skip);
        job_run(o);
    }
}

/* The app's catalogue.tsv read into nw, streamed a line at a time. 0 and why when it is not one;
 * a seq other than job_seq (rewritten since it was seen) is an error too: the next poll sees it.
 * The s, p and i lines read (jb.rows, a row skipped as too long or not well formed included) must
 * be the header's rows=, so a file cut short is not taken for a smaller library. */
static int import_file(int slot, const char *id, const char *seq, char *why, unsigned size) {
    char path[64], *line = calloc(1, CAT_LINE + 1), *f[20];
    tk_snprintf(path, sizeof path, "/mnt/mmc/.%s/catalogue.tsv", id);
    void *in = line ? fopen(path, "rb") : 0;
    imp_t im = { slot, id, CAT_ID_BASE + (unsigned)slot * CAT_APP_SPAN, 0, 0, calloc(1, CAT_SQL),
                 0,    0 };
    int ok = in && im.meta, first = 1, skipping = 0, rows = -1, end = 0;
    if (!ok) tk_snprintf(why, size, "Couldn't read the catalog");
    unsigned k = 0, got = 0;
    char buf[4096];
    while (ok && !end) {
        got = (unsigned)fread(buf, 1, sizeof buf, in);
        end = !got;
        for (unsigned i = 0; ok && i < (end ? 1 : got); ++i) {
            if (end && !k) break; /* the file ended with its newline */
            if (!end && buf[i] != '\n') {
                if (k < CAT_LINE)
                    line[k++] = buf[i];
                else
                    skipping = 1;
                continue;
            }
            if (k && line[k - 1] == '\r') --k;
            line[k] = 0;
            int long_line = skipping;
            k = 0, skipping = 0;
            if (first) {
                first = 0;
                char want[40];
                tk_snprintf(want, sizeof want, "\tseq=%s\t", seq);
                const char *r = strstr(line, "\trows=");
                rows = r && r[6] >= '0' && r[6] <= '9' ? atoi(r + 6) : -1;
                if (long_line || !tk_str_start_with(line, "#Q2CAT/1\t") || !strstr(line, want) ||
                    rows < 0) {
                    tk_snprintf(why, size, "Not a Q2CAT/1 catalogue with seq %s", seq);
                    ok = 0;
                }
                continue;
            }
            if ((line[0] == 's' || line[0] == 'p' || line[0] == 'i') && line[1] == '\t') ++jb.rows;
            if (long_line) continue;
            int n = tsv_split(line, f, 20);
            if (!strcmp(f[0], "s"))
                imp_song(&im, f, n);
            else if (!strcmp(f[0], "p") || !strcmp(f[0], "i"))
                imp_list(&im, f, n);
            if (im.next - CAT_ID_BASE - (unsigned)slot * CAT_APP_SPAN >= CAT_APP_SPAN) {
                tk_snprintf(why, size, "Too many songs");
                ok = 0;
            }
            if (jb.bad) tk_snprintf(why, size, "Couldn't write the catalog"), ok = 0;
        }
    }
    if (ok && first) tk_snprintf(why, size, "Empty file"), ok = 0;
    if (ok && jb.rows != rows) tk_snprintf(why, size, "Read %d of %d rows", jb.rows, rows), ok = 0;
    if (ok) imp_flush(&im);
    if (in) fclose(in);
    free(line), free(im.meta);
    return ok && !jb.bad;
}

/* ---- Playlists: each catalogue playlist is a stock playlist (playlist's rows: a header id 0-99
 * whose cd_num is itself, its items' cd_num that id), so Library's Playlists lists them. A server
 * row's item is its servertable row; a linked song's, its card file's. A playlist gone from the
 * catalogue goes from the Q2 too. A playlist the app also synced to the card as an .m3u (imported
 * by the library extension, so a stock playlist of that name holding those card files in that
 * order) is its twin: the catalogue's takes the twin's place rather than showing beside it, and is
 * only unlinked, not deleted, when it leaves the catalogue. A playlist of the user's with that name
 * and other songs keeps the name, and the catalogue's is left out. ---- */

static int one_int(void *ctx, int n, char **v, char **c) {
    *(int *)ctx = v[0] ? atoi(v[0]) : -1;
    return 0;
}
static int pl_used(void *ctx, int n, char **v, char **c) {
    int id = v[0] ? atoi(v[0]) : -1;
    if (id >= 0 && id < CAT_PLAYLISTS) ((unsigned char *)ctx)[id] = 1;
    return 0;
}
typedef struct {
    char app[16], pid[128], name[128];
} pl_t;
static int pl_row(void *ctx, int n, char **v, char **c) {
    pl_t **p = ctx;
    if (!v[0] || !v[1] || !v[2]) return 0;
    tk_snprintf((*p)->app, sizeof(*p)->app, "%s", v[0]);
    tk_snprintf((*p)->pid, sizeof(*p)->pid, "%s", v[1]);
    tk_snprintf((*p)->name, sizeof(*p)->name, "%s", v[2]);
    ++*p;
    return 0;
}
/* jb.sql up to o run for its one number, into *v (left as it is when there is none). */
static void job_int(char *o, int *v) {
    if (o >= JEND) return;
    *o = 0;
    sqlite3_exec(jb.db, jb.sql, one_int, v, 0);
}
/* A catalogue item's card file: a synced song's path, or the card file the match linked. */
#define PL_CARD "coalesce(nullif(i.path,''),m.dup)"
/* Whether stock playlist list is p's twin: its items, in order, p's card files in order. */
static int pl_twin(const pl_t *p, int list) {
    int same = 0;
    char *o = jb.sql;
    PUT(o, JEND,
        "select a is not null and a=b from (select (select group_concat(fileurl,char(10)) from "
        "(select fileurl from playlist where id>100 and cd_num=%d order by id)) as a,(select "
        "group_concat(f,char(10)) from (select " PL_CARD " as f from nw.serveritem i left join "
        "nw.servermeta m on m.app=i.app and m.sid=i.sid where " PL_CARD " is not null and i.app=",
        list);
    o = sq(o, JEND, p->app), PUT(o, JEND, " and i.pid="), o = sq(o, JEND, p->pid);
    PUT(o, JEND, " order by i.rowid)) as b)");
    job_int(o, &same);
    return same == 1;
}
/* p's header made in a free id, or -1 when stock's 100 are taken. */
static int pl_create(const pl_t *p, unsigned char *used) {
    int list = 0;
    while (list < CAT_PLAYLISTS && used[list]) ++list;
    if (list == CAT_PLAYLISTS) return -1;
    used[list] = 1;
    char *o = jb.sql;
    PUT(o, JEND, "insert into playlist values (%d,", list);
    o = sq(o, JEND, p->name);
    PUT(o, JEND, ",'','','','',");
    o = sq(o, JEND, p->name);
    PUT(o, JEND, ",'','',0,0,%d,0,0,0,0,0,0)", list);
    return job_run(o) ? -1 : list;
}
/* list's items again, in the file's order (serveritem's rowid): a card song as its card row */
static void pl_items(const pl_t *p, int list) {
    int top = 100;
    sqlite3_exec(jb.db, "select max(100,ifnull(max(id),100)) from playlist", one_int, &top, 0);
    char *o = jb.sql;
    PUT(o, JEND,
        "delete from playlist where id>100 and cd_num=%d;insert into playlist select %d+n,"
        "filename,fileurl,album,songer,style,name,composer,albumsonger,year,track,%d,time,"
        "time_end,time_create,Initial,bitdepth,samprate from (select i.rowid as n,"
        "coalesce(c.filename,s.filename) as filename,coalesce(c.fileurl,s.fileurl) as fileurl,"
        "coalesce(c.album,s.album) as album,coalesce(c.songer,s.songer) as songer,"
        "coalesce(c.style,s.style) as style,coalesce(c.name,s.name) as name,'' as composer,"
        "coalesce(c.albumsonger,s.albumsonger) as albumsonger,coalesce(c.year,s.year) as year,"
        "coalesce(c.track,s.track) as track,coalesce(c.time,0) as time,coalesce(c.time_end,0) as "
        "time_end,coalesce(c.time_create,0) as time_create,coalesce(c.Initial,s.Initial) as "
        "Initial,coalesce(c.bitdepth,0) as bitdepth,coalesce(c.samprate,0) as samprate from "
        "nw.serveritem i left join nw.servermeta m on m.app=i.app and m.sid=i.sid left join "
        "nw.serverall s on s.id=m.id left join songtable c on c.fileurl=" PL_CARD " where (c.id "
        "is not null or s.id is not null) and i.app=",
        list, top, list);
    o = sq(o, JEND, p->app), PUT(o, JEND, " and i.pid="), o = sq(o, JEND, p->pid);
    PUT(o, JEND, " order by i.rowid)");
    job_run(o);
}
static void cat_playlists(const char *app) {
    pl_t *lists = calloc(CAT_PLAYLISTS + 1, sizeof *lists), *at = lists;
    unsigned char used[CAT_PLAYLISTS] = { 0 };
    char *o = jb.sql;
    if (!lists) return;
    PUT(o, JEND, "select app,pid,name from nw.serverpl where app=");
    o = sq(o, JEND, app), PUT(o, JEND, " limit %d", CAT_PLAYLISTS);
    if (o < JEND) *o = 0, sqlite3_exec(jb.db, jb.sql, pl_row, &at, 0);
    if (job_exec("begin immediate")) {
        free(lists);
        jb.bad = 0;
        return;
    }
    /* gone from the catalogue: the link, and the playlist unless it is a twin */
    o = jb.sql;
    PUT(o, JEND,
        "delete from playlist where cd_num in (select list from nw.serverlists where "
        "twin is not 1 and app=");
    o = sq(o, JEND, app);
    PUT(o, JEND, " and pid not in (select pid from nw.serverpl where app=");
    o = sq(o, JEND, app), PUT(o, JEND, "));delete from nw.serverlists where app=");
    o = sq(o, JEND, app);
    PUT(o, JEND, " and pid not in (select pid from nw.serverpl where app=");
    o = sq(o, JEND, app), PUT(o, JEND, ")");
    job_run(o);
    sqlite3_exec(jb.db, "select id from playlist where id < 100", pl_used, used, 0);
    for (pl_t *p = lists; !jb.bad && p < at; ++p) {
        int list = -1, twin = 0, taken = -1;
        o = jb.sql, PUT(o, JEND, "select list from nw.serverlists where app=");
        o = sq(o, JEND, p->app), PUT(o, JEND, " and pid="), o = sq(o, JEND, p->pid);
        job_int(o, &list);
        if (list < 0 || list >= CAT_PLAYLISTS || !used[list]) { /* new, or deleted on the Q2 */
            o = jb.sql, PUT(o, JEND, "select id from playlist where id<100 and name=");
            o = sq(o, JEND, p->name), PUT(o, JEND, " limit 1");
            job_int(o, &taken);
            if (taken >= 0 && taken < CAT_PLAYLISTS) {
                if (!pl_twin(p, taken)) continue; /* the user's own of that name */
                list = taken, twin = 1;
            } else if ((list = pl_create(p, used)) < 0)
                break;
            o = jb.sql, PUT(o, JEND, "delete from nw.serverlists where app=");
            o = sq(o, JEND, p->app), PUT(o, JEND, " and pid="), o = sq(o, JEND, p->pid);
            PUT(o, JEND, ";insert into nw.serverlists values (");
            o = sq(o, JEND, p->app), *o++ = ',', o = sq(o, JEND, p->pid);
            PUT(o, JEND, ",%d,%d)", list, twin);
            job_run(o);
        }
        pl_items(p, list);
    }
    /* a failure here leaves the import as it is: the playlists are tried again at the next job */
    if (jb.bad || job_exec("commit")) sqlite3_exec(jb.db, "rollback", 0, 0, 0);
    jb.bad = 0;
    free(lists);
}

/* The index of nw: every server row by its fileurl, every linked card file by its path. */
static int ix_row(void *ctx, int n, char **v, char **c) {
    cat_ix *x = jb.ix;
    if (!(x->n & 1023)) {
        cat_ent *e = realloc(x->e, (x->n + 1024) * sizeof *e);
        if (!e) return jb.bad = 1;
        x->e = e;
    }
    cat_ent *e = &x->e[x->n++];
    e->dup = v[4] != 0;
    e->key = fnv64(e->dup ? v[4] : v[0]);
    e->path = ix_put(x, e->dup ? v[4] : v[0]);
    e->sid = ix_put(x, v[1]), e->local = e->dup ? 0 : ix_put(x, v[2]);
    e->sec = v[3] ? atoi(v[3]) : 0;
    e->app = (unsigned char)(v[5] ? atoi(v[5]) : 0);
    x->apps |= 1u << e->app;
    for (int k = 0; k < 4; ++k) e->grp[k] = e->dup ? 0 : group_hash(v[6 + k]);
    return 0;
}
/* A card song's name in a group (ctx the set), for group_unavailable: the card's songs always
 * play. */
static int card_group_row(void *ctx, int n, char **v, char **c) {
    return !set_add(ctx, group_hash(v[0])) && (jb.bad = 1);
}
static void ix_build(void) {
    jb.ix = &cat.job_ix;
    ix_free(jb.ix);
    /* the app's slot from its id's first appearance: the UI's slots (cat.app) by id */
    char *o = jb.sql;
    PUT(o, JEND, "select m.fileurl,m.sid,m.local,m.seconds,m.dup,case m.app");
    for (int i = 0; i < cat.napps; ++i) {
        PUT(o, JEND, " when ");
        o = sq(o, JEND, cat.app[i].id);
        PUT(o, JEND, " then %d", i);
    }
    PUT(o, JEND,
        " else %d end,a.album,a.songer,a.albumsonger,a.style from nw.serverall a,nw.servermeta m"
        " where m.id=a.id",
        CAT_APPS);
    if (o >= JEND) jb.bad = 1;
    cat_set *cards = jb.ix->cards;
    if (jb.bad || sqlite3_exec(jb.db, jb.sql, ix_row, 0, 0) || jb.bad ||
        sqlite3_exec(jb.db, "select distinct album from main.songtable", card_group_row,
                     &cards[G_ALBUM], 0) ||
        sqlite3_exec(jb.db, "select distinct songer from main.songtable", card_group_row,
                     &cards[G_ARTIST], 0) ||
        sqlite3_exec(jb.db, "select distinct albumsonger from main.songtable", card_group_row,
                     &cards[G_ARTIST], 0) ||
        sqlite3_exec(jb.db, "select distinct style from main.songtable", card_group_row,
                     &cards[G_GENRE], 0) ||
        jb.bad || !ix_sort(jb.ix))
        ix_free(jb.ix);
}

static void *cat_worker(void *unused) {
    char why[96] = "";
    int slot = cat.job_app;
    memset(&jb, 0, sizeof jb);
    unlink(CAT_NEW), unlink(CAT_NEW "-journal");
    mkdir(CAT_DIR, 0755);
    if (!(jb.sql = calloc(1, CAT_SQL)) || sqlite3_open("/mnt/data/database.db", &jb.db) ||
        sqlite3_exec(jb.db, "PRAGMA busy_timeout=2000;PRAGMA cache_size=256;PRAGMA temp_store=FILE",
                     0, 0, 0) ||
        sqlite3_exec(jb.db, "attach '" CAT_NEW "' as nw", 0, 0, 0) ||
        sqlite3_exec(jb.db, "PRAGMA nw.journal_mode=OFF;PRAGMA nw.synchronous=OFF", 0, 0, 0))
        tk_snprintf(why, sizeof why, "Couldn't open the database");
    else {
        if (!access(CAT_DB, 0)) sqlite3_exec(jb.db, "attach '" CAT_DB "' as cat", 0, 0, 0);
        job_exec("begin");
        job_exec(SCHEMA);
        copy_old(slot >= 0 ? cat.app[slot].id : 0);
        /* a job with no seq only takes the app's rows out (signed out, its file gone) */
        if (slot >= 0 && cat.job_seq[0] && !jb.bad)
            import_file(slot, cat.app[slot].id, cat.job_seq, why, sizeof why);
        if (!why[0]) cat_match();
        if (!jb.bad && job_exec("commit")) tk_snprintf(why, sizeof why, "Couldn't save");
        if (!why[0] && jb.bad) tk_snprintf(why, sizeof why, "Couldn't write the catalog");
        sqlite3_exec(jb.db, "detach cat", 0, 0, 0);
        /* every app's: a match moves songs between server rows and card files */
        for (int i = 0; !why[0] && i < cat.napps; ++i) cat_playlists(cat.app[i].id);
        if (!why[0]) ix_build();
    }
    if (jb.db) sqlite3_close(jb.db);
    free(jb.sql);
    if (why[0]) unlink(CAT_NEW);
    tk_snprintf(cat.job_why, sizeof cat.job_why, "%s", why);
    cat.job_rows = (unsigned)jb.rows;
    __atomic_store_n(&cat.done, 1, __ATOMIC_RELEASE);
    return 0;
}

/* ---- The UI thread ---- */

/* Stock's shared connection (opened again after any error) and each other one stock_db_open
 * opens: cat attached while a signed-in app's rows are indexed and the file is there.
 * stock_db_open runs on stock's scan thread too; cat_live attaches later where this could not. */
void catalogue_attach(void **slot) {
    /* stock's own global: in demo's image, below the payload (0xb00000, tools/build.py BASE) */
    if ((unsigned long)slot >= 0x400000 && (unsigned long)slot < 0xb00000) cat.stock_slot = slot;
    if (slot == cat.stock_slot) cat.attached = 0;
    if (!cat.ix.n || access(CAT_DB, 0) || !*slot) return;
    if (!sqlite3_exec(*slot, "attach '" CAT_DB "' as cat", 0, 0, 0) && slot == cat.stock_slot)
        cat.attached = *slot;
}
static void cat_detach(void) {
    void *db = cat.stock_slot ? *cat.stock_slot : 0;
    if (db && cat.attached == db) sqlite3_exec(db, "detach cat", 0, 0, 0);
    cat.attached = 0;
}
/* Whether stock's Library queries may read servertable now: an index built and the file attached
 * to its live handle. Run on stock's scan thread too. */
static int cat_live(void) {
    if (!cat.ix.n || !cat.stock_slot) return 0;
    void *db = *cat.stock_slot;
    if (db && cat.attached != db && !access(CAT_DB, 0) &&
        !sqlite3_exec(db, "attach '" CAT_DB "' as cat", 0, 0, 0))
        cat.attached = db;
    return db && cat.attached == db;
}

/* Era's and the counts' worker threads ask too: an index built and the file there. */
static int cat_ready(void) { return cat.ix.n && !access(CAT_DB, 0); }
/* library_query's: the server's rows beside songtable's, while stock's connection has them. */
const char *catalogue_rows(void) {
    return cat_live() ? " union all select * from servertable" : "";
}
/* sql with each " from songtable" reading the union of the card's and the server's songs, or 0
 * (no catalogue, no memory). The caller frees it. */
char *catalogue_union(const char *sql) {
    static const char from[] = " from songtable",
                      with[] =
                          " from (select * from songtable union all select * from servertable)";
    if (!sql || !strstr(sql, from) || !cat_ready()) return 0;
    unsigned n = 1, k = strlen(sql);
    for (const char *p = strstr(sql, from); p; p = strstr(p + 1, from)) ++n;
    char *out = calloc(1, k + n * sizeof with), *o = out;
    for (const char *p = sql, *q; out && (q = strstr(p, from)); p = q + sizeof from - 1) {
        memcpy(o, p, (unsigned)(q - p)), o += q - p;
        memcpy(o, with, sizeof with - 1), o += sizeof with - 1;
        sql = q + sizeof from - 1;
    }
    if (out) memcpy(o, sql, strlen(sql) + 1);
    return out;
}
/* Stock's Search (SearchMusicByName and its kin, each "... from songtable where <field> like
 * '%<text>%'", run outside a music load): sql reading the union, or 0 (not a search, or no union
 * now). The caller frees it. */
char *catalogue_search(const char *sql) {
    return sql && strstr(sql, " like '%") && cat_live() ? catalogue_union(sql) : 0;
}
/* Era's own connections: cat attached when the union reads it. */
void catalogue_attach_db(void *db) {
    if (cat_ready()) sqlite3_exec(db, "attach '" CAT_DB "' as cat", 0, 0, 0);
}

static const cat_ent *cat_lookup(const char *path) {
    if (!path || !cat.ix.n || !tk_str_start_with(path, "/mnt/mmc/")) return 0;
    return ix_find(&cat.ix, fnv64(path));
}
/* A server row's path: under a catalogue app's cat folder (known or not: a playlist's song kept
 * from an earlier catalogue is one too). */
int catalogue_path(const char *path) {
    const char *p = path && tk_str_start_with(path, "/mnt/mmc/.") ? path + 10 : 0;
    while (p && *p && *p != '/') ++p;
    return p && p > path + 10 && tk_str_start_with(p, "/cat/");
}
/* r's offline copy, or 0. */
static const char *cat_local(const char *path) {
    const cat_ent *e = cat_lookup(path);
    return e && !e->dup && e->local ? ix_str(&cat.ix, e->local) : 0;
}
static int cat_online(void) { return cat.online; }
/* Whether path, a server row's, cannot play now: no offline copy while offline. Any other path
 * can. */
static int path_unavailable(const char *path) {
    if (!catalogue_path(path)) return 0;
    return !cat_local(path) && !(cat_online() && cat_lookup(path));
}
int catalogue_unavailable(void *r) { return path_unavailable(r ? P(r, REC_PATH) : 0); }
/* Stock's play check on a Library row (tools/build.py CATALOGUE_ACCESS): a server row that can play
 * now passes, and ringnav_load (catalogue_load) streams it or plays its copy; one that can't fails
 * as a missing file does, so stock says File Not Found and opens no Now Playing. ponytail: stock's
 * words, not "Not available offline": each of the eight callers shows its own toast on a failure,
 * and the page after a pass. */
int catalogue_access(const char *path, int mode) {
    return catalogue_path(path) ? -path_unavailable(path) : access(path, mode);
}
/* A list's check before it plays from path, its first or pressed song: whether path is a card
 * file that isn't there (the card away). A server row has no file of its own and passes, so
 * ringnav_load (catalogue_load) plays it, or says why it can't. */
int catalogue_missing(const char *path) { return !catalogue_path(path) && access(path, 0); }
/* A server row's seconds for durations.c, -1 for any other path. */
int catalogue_seconds(const char *path) {
    const cat_ent *e = catalogue_path(path) ? cat_lookup(path) : 0;
    return e ? e->sec : -1;
}

static int cat_app_of(const char *path);
/* The server row whose offline copy path is, or 0: a Stream Cache file the app played it from,
 * named in catalogue.avail, or a song it streams, whose download in its stream cache only the sid
 * in the file's name ties to the row (stream_path.h; the device, 2026-11-16: no cover in Control
 * Center or the car for one). Every UI lookup from a playing path to its server row comes here:
 * its cover (catalogue_cover: the player's cover thread, Up Next, the modal header), its play key
 * and hearts (cat_row_of) and its genre and year (catalogue_np_info). */
static int copy_cmp(const void *a, const void *b) {
    unsigned long long x = ((const struct cat_copy *)a)->key, y = ((const struct cat_copy *)b)->key;
    return x < y ? -1 : x > y;
}
/* The offline copies' index for the index as it is now. UI thread. */
static void copies_build(void) {
    unsigned n = 0;
    for (unsigned i = 0; i < cat.ix.n; ++i) n += cat.ix.e[i].local != 0;
    struct cat_copies *c = calloc(1, sizeof *c + n * sizeof *c->c);
    if (c) {
        for (unsigned i = 0; i < cat.ix.n; ++i)
            if (cat.ix.e[i].local)
                c->c[c->n++] = (struct cat_copy){ fnv64(ix_str(&cat.ix, cat.ix.e[i].local)), i };
        if (c->n) qsort(c->c, c->n, sizeof *c->c, copy_cmp);
        c->gen = cat.local_gen;
    }
    free(cat.copies_old);
    cat.copies_old = cat.copies;
    __atomic_store_n(&cat.copies, c, __ATOMIC_RELEASE);
}
/* The entry whose offline copy is path; 0 for none, or with no index for the copies as they are,
 * -1 (walk the entries). */
static const cat_ent *copy_find(const char *path, int *walk) {
    const struct cat_copies *c = __atomic_load_n(&cat.copies, __ATOMIC_ACQUIRE);
    *walk = !c || c->gen != cat.local_gen;
    if (*walk) return 0;
    unsigned long long key = fnv64(path);
    unsigned lo = 0, hi = c->n;
    while (lo < hi)
        if (c->c[(lo + hi) / 2].key < key)
            lo = (lo + hi) / 2 + 1;
        else
            hi = (lo + hi) / 2;
    for (; lo < c->n && c->c[lo].key == key; ++lo) {
        const cat_ent *e = c->c[lo].at < cat.ix.n ? &cat.ix.e[c->c[lo].at] : 0;
        if (e && e->local && !strcmp(ix_str(&cat.ix, e->local), path)) return e;
    }
    return 0;
}
static const cat_ent *cat_copy_of(const char *path) {
    if (!path || !tk_str_start_with(path, "/mnt/mmc/.")) return 0;
    int walk;
    const cat_ent *found = copy_find(path, &walk);
    if (found) return found;
    for (unsigned i = 0; walk && i < cat.ix.n; ++i)
        if (cat.ix.e[i].local && !strcmp(ix_str(&cat.ix, cat.ix.e[i].local), path))
            return &cat.ix.e[i];
    const char *app, *sid;
    int appn, sidn, slot = -1;
    char key[64];
    if (!stream_path_sid(path, &app, &appn, &sid, &sidn) || sidn >= (int)sizeof key ||
        (slot = cat_app_of(path)) < 0)
        return 0;
    memcpy(key, sid, (unsigned)sidn), key[sidn] = 0;
    return ix_sid(&cat.ix, slot, key);
}
/* The server's length of the song a Stream Cache copy plays, in seconds; -1 when no row names it
 * or the server gave none. */
int catalogue_copy_seconds(const char *path) {
    const cat_ent *e = cat_copy_of(path);
    return e && e->sec > 0 ? e->sec : -1;
}
/* The server row r plays as: r's own path is a Stream Cache copy of it (not a linked card file's
 * row, which plays from the card). 0 for any other record. On the UI thread only (stock's cover
 * thread asks cat_copy_of itself): the answer is kept for the last path until an import or a
 * catalogue.avail line moves local_gen. */
static const cat_ent *cat_row_of(void *r) {
    const char *path = r && cat.ix.n ? P(r, REC_PATH) : 0;
    if (!path || catalogue_path(path)) return 0;
    unsigned long long key = fnv64(path);
    if (cat.row_gen != cat.local_gen + 1 || cat.row_path != key) {
        const cat_ent *e = cat_copy_of(path);
        cat.row_path = key, cat.row_gen = cat.local_gen + 1;
        cat.row_at = e && !e->dup ? (unsigned)(e - cat.ix.e) + 1 : 0;
    }
    return cat.row_at ? &cat.ix.e[cat.row_at - 1] : 0;
}
/* The fileurl of the server row r plays as (cat_row_of), for song_rows.c's song_playing: a song
 * list names a server song by its row, the queue by the copy that plays it. */
const char *catalogue_row_path(void *r) {
    const cat_ent *e = cat_row_of(r);
    return e ? ix_str(&cat.ix, e->path) : 0;
}
/* listen_key's, for a server row's record: its fileurl's (a server row has no CUE start). */
static unsigned row_key(const cat_ent *e) {
    unsigned row = fnv(FNV_SEED, (const unsigned char *)ix_str(&cat.ix, e->path));
    return row | !row;
}
/* The key a song's plays and hearts are kept under, for r (key, its listen_key): a server song
 * playing from its offline copy, a Stream Cache file no Library row has, is its server row, so
 * Most Played, Cover Flow's album plays and the Ratings lists follow the song, and its hearts
 * reach the server (docs/server-library.md: play counts and ratings key on the row's fileurl).
 * key for any other. */
unsigned catalogue_song_key(void *r, unsigned key) {
    const cat_ent *e = cat_row_of(r);
    return e ? row_key(e) : key;
}
typedef struct {
    unsigned long long key; /* the path's fnv64 */
    int known;
    char genre[96];
    int year;
} np_t;
static int np_row(void *ctx, int n, char **v, char **c) {
    np_t *np = ctx;
    tk_snprintf(np->genre, sizeof np->genre, "%s", v[0] ? v[0] : "");
    np->year = v[1] ? atoi(v[1]) : 0;
    return 0;
}
/* Now Playing's info page (navigation.c ringnav_np_info) for the playing path: a server row's, or
 * its offline copy's, a Stream Cache file no songtable row describes, so stock finds no genre or
 * year for it. The catalogue's, into genre (96 bytes) and *year (0 for none); 0 for any other
 * path. Kept for the last path asked: the page asks every 250 ms. */
int catalogue_np_info(const char *path, char *genre, int *year) {
    static np_t *np __attribute__((section(".scratch")));
    if (!path || !cat.ix.n || !tk_str_start_with(path, "/mnt/mmc/.")) return 0;
    if (!np && !(np = calloc(1, sizeof *np))) return 0;
    unsigned long long key = fnv64(path);
    if (np->key != key) {
        const cat_ent *e = catalogue_path(path) ? cat_lookup(path) : cat_copy_of(path);
        char sql[640], *o = sql;
        np->key = key, np->known = 0, np->genre[0] = 0, np->year = 0;
        if (e && !e->dup && cat_live()) {
            PUT(o, sql + sizeof sql - CAT_SLACK,
                "select style,year from cat.serverall where fileurl=");
            o = sq(o, sql + sizeof sql - CAT_SLACK, ix_str(&cat.ix, e->path));
            if (o < sql + sizeof sql - CAT_SLACK) /* sq leaves the end to its caller */
                *o = 0, np->known = !sqlite3_exec(*cat.stock_slot, sql, np_row, np, 0);
        }
    }
    if (!np->known) return 0;
    tk_snprintf(genre, 96, "%s", np->genre);
    *year = np->year;
    return 1;
}

/* The server row's name, album, songer and albumsonger into a record's empty fields (ctx[0] the
 * record, ctx[1] set once a row came), for catalogue_copy_tags. */
static int tags_row(void *ctx, int n, char **v, char **c) {
    static const int to[] = { REC_TITLE, REC_ALBUM, REC_ARTIST, REC_ALBUM_ARTIST };
    void *r = ((void **)ctx)[0];
    (void)c;
    ((void **)ctx)[1] = ctx;
    char *old[4];
    for (int k = 0; k < 4; ++k) old[k] = P(r, to[k]); /* all read before any is written */
    for (int k = 0; k < n && k < 4; ++k) {
        char *t;
        if ((old[k] && *old[k]) || !v[k] || !*v[k] || !(t = strdup(v[k]))) continue;
        free(old[k]), P(r, to[k]) = t;
    }
    return 1; /* one row */
}
/* r's title, album, artist and album artist from the server row its path is a Stream Cache copy
 * of (cat_copy_of), for a song not yet played (tags.c card_record_tags); 1 when a row named it. 0
 * with no server signed in or before an import, when there is no index. */
int catalogue_copy_tags(void *r) {
    const char *path = r && cat.ix.n ? P(r, REC_PATH) : 0;
    const cat_ent *e = path && !catalogue_path(path) ? cat_copy_of(path) : 0;
    if (!e || e->dup || !cat_live()) return 0;
    char sql[640], *o = sql;
    void *ctx[2] = { r, 0 };
    PUT(o, sql + sizeof sql - CAT_SLACK,
        "select name,album,songer,albumsonger from cat.serverall where fileurl=");
    o = sq(o, sql + sizeof sql - CAT_SLACK, ix_str(&cat.ix, e->path));
    if (o >= sql + sizeof sql - CAT_SLACK) return 0; /* cut: never run */
    *o = 0;
    sqlite3_exec(*cat.stock_slot, sql, tags_row, ctx, 0);
    return ctx[1] != 0;
}

/* Whether paths a and b play one song: the same path, the same server row (its own path, or a
 * Stream Cache copy of it, cat_copy_of), or two downloads of one server song (stream_path.h: the
 * same app and sid), such as an app's radio naming the song playing again (song_radio.c). */
int catalogue_same_song(const char *a, const char *b) {
    if (!a || !b) return 0;
    if (!strcmp(a, b)) return 1;
    const cat_ent *x = catalogue_path(a) ? cat_lookup(a) : cat_copy_of(a),
                  *y = catalogue_path(b) ? cat_lookup(b) : cat_copy_of(b);
    if (x && x == y) return 1;
    const char *app[2], *sid[2];
    int appn[2], sidn[2];
    return stream_path_sid(a, &app[0], &appn[0], &sid[0], &sidn[0]) &&
           stream_path_sid(b, &app[1], &appn[1], &sid[1], &sidn[1]) && appn[0] == appn[1] &&
           sidn[0] == sidn[1] && !memcmp(app[0], app[1], (unsigned)appn[0]) &&
           !memcmp(sid[0], sid[1], (unsigned)sidn[0]);
}

/* The album cover the app keeps for path's album (docs/server-library.md#covers): path a server
 * row's (/mnt/mmc/.<id>/cat/<album id>/<sid>.<suffix>) or its offline copy's, the cover
 * /mnt/mmc/.<id>/covers/<album id>.jpg, into out, when it is there. 0 for any other path, or none
 * fetched yet. */
int catalogue_cover(const char *path, char *out, unsigned size) {
    const cat_ent *e = path && !catalogue_path(path) ? cat_copy_of(path) : 0;
    const char *row = e && !e->dup ? ix_str(&cat.ix, e->path) : path;
    if (!catalogue_path(row)) return 0;
    const char *cat_at = row + 10, *album, *end;
    while (*cat_at != '/') ++cat_at; /* catalogue_path: the app's id, then /cat/ */
    for (album = end = cat_at + 5; *end && *end != '/'; ++end);
    if (!*end || end == album) return 0;
    int n = tk_snprintf(out, size, "%.*s/covers/%.*s.jpg", (int)(cat_at - row), row,
                        (int)(end - album), album);
    return n > 0 && (unsigned)n < size && !access(out, 4);
}

/* The app's larger cover of path's album, covers/512/<album>.jpg (q2-subsonic saves 500 px there,
 * beside the 160 px list-row one catalogue_cover names), when the card has it: for art drawn
 * larger than a row, Home's slideshow (slideshow.c). 0, out unspecified, when it is not there. */
int catalogue_cover_large(const char *path, char *out, unsigned size) {
    if (!catalogue_cover(path, out, size)) return 0;
    char *name = strrchr(out, '/');
    unsigned k = (unsigned)strlen(out);
    if (!name || k + 5 > size) return 0;
    memmove(name + 5, name + 1, k - (unsigned)(name + 1 - out) + 1);
    memcpy(name + 1, "512/", 4);
    return !access(out, 4);
}

/* ---- Greyed and marked rows: stock's pooled table rows as they are bound (ringnav_table_load),
 * the payload's own lists' rows as they are made (catalogue_mark). ringnav_style_color asks
 * catalogue_greyed for each text colour. A row that can't play now is greyed with the offline
 * mark; under OLED a song row that can says where it lives (docs/oled-design.md#song-rows): a
 * server row that streams has the cloud, a card file in a catalogue app's downloads folder (q2app
 * downloads=) the arrow, and the user's own file nothing. ---- */
enum { MK_NONE, MK_OFFLINE, MK_CLOUD, MK_DOWN };

static void grey_set(void *row, int mark);
static int grey_gone(void *row, void *event) {
    grey_set(row, MK_NONE);
    return 0;
}
/* A row marked is forgotten when it is destroyed (its page closed), so a widget made later at its
 * address is not marked with it. */
static void grey_set(void *row, int mark) {
    unsigned i = 0;
    while (i < cat.ngrey && cat.grey[i] != row) ++i;
    if (mark && i == cat.ngrey && cat.ngrey < CAT_GREY_ROWS) {
        cat.grey_ink[cat.ngrey] = 0, cat.grey_mark[cat.ngrey] = MK_NONE,
        cat.grey[cat.ngrey++] = row;
        if (!widget_get_prop_int(row, "_catgrey", 0) && widget_on(row, EVT_DESTROY, grey_gone, row))
            widget_set_prop_int(row, "_catgrey", 1);
    }
    if (i < cat.ngrey) cat.noff -= cat.grey_mark[i] == MK_OFFLINE;
    if (mark && i < cat.ngrey)
        cat.grey_mark[i] = (unsigned char)mark, cat.noff += mark == MK_OFFLINE;
    if (!mark && i < cat.ngrey) {
        --cat.ngrey;
        cat.grey[i] = cat.grey[cat.ngrey], cat.grey_ink[i] = cat.grey_ink[cat.ngrey];
        cat.grey_mark[i] = cat.grey_mark[cat.ngrey];
    }
}
/* A song record's mark: offline when it can't play now; else where it lives. */
static int song_mark(void *r) {
    const char *p = r ? P(r, REC_PATH) : 0;
    if (path_unavailable(p)) return MK_OFFLINE;
    if (!p) return MK_NONE;
    if (catalogue_path(p)) return MK_CLOUD;
    for (int i = 0; i < cat.napps; ++i) {
        unsigned k = strlen(cat.app[i].dl);
        if (k && tk_str_start_with(p, "/mnt/mmc/") && !strncmp(p + 9, cat.app[i].dl, k) &&
            p[9 + k] == '/')
            return MK_DOWN;
    }
    return MK_NONE;
}
static int search_group_unavailable(void *r, int tab);
/* A list's row as it paints (navigation.c grey_rows): its record a song's, or, tab 1 to 3, a row
 * of Search's Artists, Albums or Genres tab. */
void catalogue_mark(void *row, void *r, int tab) {
    grey_set(row, tab > 0 ? search_group_unavailable(r, tab) : song_mark(r));
}
void catalogue_forget(void *row) { grey_set(row, MK_NONE); }
/* w's text greyed (on) as a row's that can't play, or not: Cover Flow's captions. */
void catalogue_grey(void *w, int on) { grey_set(w, on ? MK_OFFLINE : MK_NONE); }
/* The server rows' names with an offline copy, by group, as they are now. */
static void local_sets(void) {
    if (cat.local_built == cat.local_gen) return;
    cat.local_built = cat.local_gen;
    for (int k = 0; k < G_KINDS; ++k) cat.local[k].n = 0;
    for (unsigned i = 0; i < cat.ix.n; ++i)
        if (cat.ix.e[i].local && !cat.ix.e[i].dup && !ent_groups(&cat.ix.e[i], cat.local)) {
            cat.local_built = cat.local_gen - 1; /* out of memory: none counted, tried again */
            for (int k = 0; k < G_KINDS; ++k) cat.local[k].n = 0;
            return;
        }
    for (int k = 0; k < G_KINDS; ++k) set_sort(&cat.local[k]);
}
/* Whether a group's row (its name's group_hash h) can't play now: offline, no card song has that
 * name and no server row of it has an offline copy. A name the catalogue doesn't know is the
 * card's. By name, as the Library's lists group them: ponytail: a server-only album that shares
 * its name with a card album (another artist's "Greatest Hits"), or a server-only artist named as
 * a card song's album artist, is not greyed; never the other way round. */
static int group_unavailable(int kind, unsigned h) {
    if (!h || !cat.ix.n || cat_online()) return 0;
    if (set_has(&cat.ix.cards[kind], h) || !set_has(&cat.ix.servers[kind], h)) return 0;
    local_sets();
    return !set_has(&cat.local[kind], h);
}
/* Whether an album row (r, of an album list) can't play now (group_unavailable). */
int catalogue_album_unavailable(void *r) {
    return group_unavailable(G_ALBUM, r && I(r, REC_ID) >= 0 ? group_hash(P(r, REC_ALBUM)) : 0);
}
/* ... and an artist's (r a record with the artist's name, as the letter strip's artist rows). */
int catalogue_artist_unavailable(void *r) {
    return group_unavailable(G_ARTIST, r && I(r, REC_ID) >= 0 ? group_hash(P(r, REC_ARTIST)) : 0);
}
/* Whether a row of Search's Artists (tab 1), Albums (2) or Genres (3) tab can't play now: a
 * directory record its tab's callback (0x506904, 0x506e94, 0x506d60) fills with the name in
 * REC_ARTIST, REC_ALBUM or REC_STYLE. */
static int search_group_unavailable(void *r, int tab) {
    static const int kind[] = { G_ARTIST, G_ALBUM, G_GENRE },
                     field[] = { REC_ARTIST, REC_ALBUM, REC_STYLE };
    if (!r || tab > 3 || I(r, REC_ID) < 0) return 0;
    return group_unavailable(kind[tab - 1], group_hash(P(r, field[tab - 1])));
}
/* Whether the showlist holds albums: all of them, an artist's, a composer's or a genre's (an
 * artist's tabs set classinfo, not g_class_type, as qm_open reads them). */
static int album_list(void *row) {
    void *win = window_of(row);
    unsigned cls =
        (unsigned)(win && !tk_strcmp(widget_get_prop_str(win, "name", ""), "artistinfo_page")
                       ? I(g_local_classinfo_save, 0)
                       : I(g_class_type, 0));
    return cls == CLASS_ALBUMS || (cls >= CLASS_ARTIST_ALBUMS && cls <= 0xff03);
}
/* A stock list's pooled row bound to its index'th record: the Library's lists are
 * p_deque_showlist's, and the row shows its record's
 * title or file name (a song's), or its album (an album list's); any other table (whose index means
 * something else) leaves its rows as they are. */
void catalogue_bind(void *row, unsigned index) {
    void *list = P(p_deque_showlist, 0);
    void *r = list && index < deque_size(list) ? deque_at(list, index) : 0;
    grey_set(row, !r ? MK_NONE
                  : I(r, REC_TYPE) == 8
                      ? song_mark(r)
                      : cat.ix.n && album_list(row) && catalogue_album_unavailable(r));
}
/* The greyed row w is in, or -1; up to depth levels above w. */
static int grey_of(void *w, int depth) {
    for (; w && cat.ngrey && depth-- > 0; w = P(w, W_PARENT))
        for (unsigned i = 0; i < cat.ngrey; ++i)
            if (cat.grey[i] == w) return (int)i;
    return -1;
}
/* The text colour of w, painted now, as the theme gave it: in a greyed row, CAT_GREY, or on the
 * selection that row's own ink at CAT_DIM, so it keeps its contrast with the bar or card
 * (docs/server-library.md#grayed-rows). Kept for the row's mark. */
int catalogue_greyed(void *w, unsigned *color) {
    int i = cat.noff ? grey_of(w, 64) : -1;
    if (i < 0 || cat.grey_mark[i] != MK_OFFLINE) return 0;
    unsigned a = *color >> 24;
    *color = ringnav_row_selected(cat.grey[i]) ? (*color & 0xffffffu) | (a * CAT_DIM / 255) << 24
                                               : (*color & 0xff000000u) | CAT_GREY;
    cat.grey_ink[i] = *color;
    return 1;
}
/* How many rows are greyed or marked now: none, and nothing needs a mark. */
int catalogue_greys(void) { return (int)cat.ngrey; }
/* Whether a signed-in app's songs are in the Library now: its rows indexed. */
int catalogue_has(void) { return cat.ix.n > 0; }
/* Whether row (as catalogue_row_ink takes it) is a greyed or marked row's, so carries a mark. */
int catalogue_row_greyed(void *row) { return grey_of(row, 4) >= 0; }
/* Whether row (a list's entry, or a widget a few levels inside its row) is a greyed row's, and that
 * row's text colour as last painted, for its mark. */
int catalogue_row_ink(void *row, unsigned *ink) {
    int i = grey_of(row, 4);
    if (i < 0 || cat.grey_mark[i] != MK_OFFLINE || !cat.grey_ink[i]) return 0;
    *ink = cat.grey_ink[i];
    return 1;
}
/* The labels under w (depth levels down) that reach past limit, an x in surface's coordinates (x
 * the left of w there): each ends at limit, its own width kept in _catw; on a row no longer greyed,
 * or limit wider again, each takes back what it can of that. */
static void fit_labels(void *w, int x, int limit, int greyed, int depth) {
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i) {
        void *c = widget_get_child(w, i);
        int cx = x + I(c, W_X), own = widget_get_prop_int(c, "_catw", 0);
        const char *t = widget_get_type(c);
        if (!tk_strcmp(t, "label") || !tk_strcmp(t, "hscroll_label")) {
            int full = own ? own : I(c, W_W),
                want = greyed && cx + full > limit ? limit - cx : full;
            if (want < 0) want = 0;
            if (want != I(c, W_W)) {
                if (!own) widget_set_prop_int(c, "_catw", full);
                widget_resize(c, want, I(c, W_H));
            }
        } else if (depth > 1)
            fit_labels(c, cx, limit, greyed, depth - 1);
    }
}
/* entry, a row of surface's list (navigation.c grey_rows), before it paints: a greyed row's labels
 * end at limit, clear of its mark; another row's take back the width they had. The row whose
 * labels these are: the greyed one entry is in, else entry's parent (a list item). Four levels
 * reach an album row's artist, inside its text's view_artist. */
void catalogue_fit(void *entry, void *surface, int limit) {
    int i = grey_of(entry, 4);
    void *row = i >= 0 ? cat.grey[i] : P(entry, W_PARENT);
    if (!row || (i < 0 && !cat.fitted)) return;
    int x = 0;
    for (void *p = row; p && p != surface; p = P(p, W_PARENT)) x += I(p, W_X);
    if (i >= 0) cat.fitted = 1;
    fit_labels(row, x, limit, i >= 0, 4);
}

/* The offline mark: Phosphor's cloud-slash, bold (phosphoricons.com, @phosphor-icons/core 2.1.1;
 * MIT, Copyright (c) 2023 Phosphor Icons), rasterised at 20 px by tools/art.py's path and
 * coverage, each pixel's coverage in 16 steps, cropped to its 20 x 16 ink. */
static const char MARK[16][21] = {
    "001b6000000222000000", "001ef50018dfffd81000", "0004ff408ffcacffd300", "00005fe327100019fe20",
    "00015bfe200000008fc0", "006effffd20000000cf3", "07ffacfdfc10000006f8", "2fe40af4bfc1000003fa",
    "8f8007c11cfb000003fa", "af40000001cf900006f8", "af400000002df8000cf4", "6fa000000002ef705fc0",
    "1ef7000000003ef51920", "04ffdbbbbbbbbeff4000", "003bffffffffffefe100", "0000122222222206a100",
};
/* The mark centred on (x, y), in ink (a final colour) with each pixel's coverage on its alpha: a
 * run of equal coverage is one rect. */
void catalogue_mark_paint(void *canvas, int x, int y, unsigned ink) {
    x -= CAT_MARK_W / 2, y -= 8;
    for (int r = 0; r < 16; ++r)
        for (int c = 0, n; c < 20; c += n) {
            char k = MARK[r][c];
            for (n = 1; c + n < 20 && MARK[r][c + n] == k; ++n);
            int v = k <= '9' ? k - '0' : k - 'a' + 10;
            if (!v) continue;
            stock_canvas_set_fill_color(canvas, (ink & 0xffffffu) | ((ink >> 24) * v / 15) << 24);
            canvas_fill_rect(canvas, x + c, y + r, n, 1);
        }
}

/* row's mark centred on (x, y) of w's canvas (navigation.c paint_offline): the offline mark in
 * its text's colour, or OLED's cloud or arrow (tools/art.py MARKS) in Text Tertiary, white on the
 * selection. */
void catalogue_row_mark(void *w, void *canvas, void *row, int x, int y) {
    int i = grey_of(row, 4);
    unsigned ink;
    char name[16];
    if (catalogue_row_ink(row, &ink)) {
        catalogue_mark_paint(canvas, x, y, ink);
    } else if (i >= 0 && cat.grey_mark[i] > MK_OFFLINE) {
        tk_snprintf(name, sizeof name, "mark_%s_%s",
                    cat.grey_mark[i] == MK_CLOUD ? "cloud" : "down",
                    ringnav_row_selected(cat.grey[i]) ? "w"
                    : ipod_light()                    ? "l"
                                                      : "d");
        paint_art(w, canvas, theme_art(name), x - CAT_ART_PX / 2, y - CAT_ART_PX / 2);
    }
}

/* ---- Apps, their files, the poll ---- */

/* A card app's q2app, for cat_scan: a catalogue app in the next slot, its state kept from the scan
 * before (was, wasn) by id. Stops once CAT_APPS are found. */
struct cat_scanning {
    cat_app_t was[CAT_APPS];
    int wasn, n;
};
static int cat_app_found(const char *id, const char *raw, void *ctx) {
    struct cat_scanning *c = ctx;
    if (strlen(id) >= sizeof cat.app[0].id || !strcmp(id, "q2pod") ||
        number(raw, "catalogue") != 1)
        return 0;
    cat_app_t *a = &cat.app[c->n++];
    memset(a, 0, sizeof *a);
    for (int i = 0; i < c->wasn; ++i)
        if (!strcmp(c->was[i].id, id)) *a = c->was[i];
    tk_snprintf(a->id, sizeof a->id, "%s", id);
    a->ratings = number(raw, "ratings");
    field(raw, "downloads", a->dl, sizeof a->dl);
    if (strstr(a->dl, "/") || a->dl[0] == '.') a->dl[0] = 0; /* one folder at the card's root */
    return c->n == CAT_APPS;
}
static void cat_scan(void) {
    struct cat_scanning c = { .wasn = cat.napps };
    memcpy(c.was, cat.app, sizeof c.was);
    if (card_mounted()) addon_q2apps(cat_app_found, &c);
    cat.napps = c.n;
}

/* The catalogue's first line: its seq, into seq (24 bytes), or 0. */
static int cat_seq(const char *id, char *seq) {
    char path[64], head[128];
    tk_snprintf(path, sizeof path, "/mnt/mmc/.%s/catalogue.tsv", id);
    if (!state_read(path, head, sizeof head)) return 0;
    const char *s = strstr(head, "\tseq=");
    unsigned k = 0;
    for (s = s ? s + 5 : 0; s && s[k] && s[k] != '\t' && s[k] != '\n' && k < 23; ++k) seq[k] = s[k];
    seq[k] = 0;
    return k > 0 && tk_str_start_with(head + 1, "#Q2CAT/1\t");
}
static void cat_answer(int slot, const char *seq, const char *why, unsigned rows) {
    char path[64], line[160];
    tk_snprintf(path, sizeof path, "/tmp/q2-%s.catalogue.done", cat.app[slot].id);
    int n = why && *why ? tk_snprintf(line, sizeof line, "%s error %s\n", seq, why)
                        : tk_snprintf(line, sizeof line, "%s ok %u\n", seq, rows);
    blob_io(path, "/tmp/q2-catalogue.tmp", line, (unsigned)n, 1);
}
static void cat_start(int slot, const char *seq) {
    cat.job_app = slot;
    tk_snprintf(cat.job_seq, sizeof cat.job_seq, "%s", seq ? seq : "");
    cat.done = 0;
    cat.gen = library_gen;
    cat.pending = 0;
    if (!pthread_create(&cat.thread, 0, cat_worker, 0)) cat.running = 1;
}
/* The job's end: its file swapped in, its index taken, the app answered. */
static void cat_swap(void) {
    pthread_join(cat.thread, 0);
    cat.running = 0;
    int slot = cat.job_app;
    if (!cat.job_why[0]) {
        cat_detach();
        if (!rename(CAT_NEW, CAT_DB)) {
            ix_free(&cat.ix);
            cat.ix = cat.job_ix;
            memset(&cat.job_ix, 0, sizeof cat.job_ix);
            ++cat.local_gen;
            /* open lists query again; a scan since the job began (cat.gen is the generation it
             * matched against) leaves cat.gen behind, so the next poll matches again */
            int matched = cat.gen == library_gen;
            ++library_gen;
            if (matched) cat.gen = library_gen;
        } else
            tk_snprintf(cat.job_why, sizeof cat.job_why, "Couldn't save");
        for (int i = 0; i < cat.napps; ++i) cat.app[i].avail_at = 0; /* read whole again */
    }
    ix_free(&cat.job_ix);
    if (slot >= 0) {
        tk_snprintf(cat.app[slot].seq, sizeof cat.app[slot].seq, "%s", cat.job_seq);
        if (cat.job_seq[0]) cat_answer(slot, cat.job_seq, cat.job_why, cat.job_rows);
    }
}

/* catalogue.avail's new lines: an offline copy for a sid, or none ("-<sid>"). */
static void cat_avail(int slot) {
    char path[64];
    tk_snprintf(path, sizeof path, "/mnt/mmc/.%s/catalogue.avail", cat.app[slot].id);
    long size = file_size(path);
    cat_app_t *a = &cat.app[slot];
    if (size < 0 || size == a->avail_at) return;
    if (size < a->avail_at) a->avail_at = 0;
    void *f = fopen(path, "rb");
    if (!f || fseek(f, a->avail_at, 0)) {
        if (f) fclose(f);
        return;
    }
    char buf[1024], line[600];
    unsigned k = 0;
    long at = a->avail_at;
    for (unsigned got; (got = fread(buf, 1, sizeof buf, f)) > 0;)
        for (unsigned i = 0; i < got; ++i) {
            if (buf[i] != '\n') {
                if (k < sizeof line - 1) line[k++] = buf[i];
                continue;
            }
            a->avail_at = at + (long)i + 1;
            if (k && line[k - 1] == '\r') --k;
            line[k] = 0, k = 0;
            char *f2[2];
            int gone = line[0] == '-';
            int n = tsv_split(line + gone, f2, 2);
            cat_ent *e = ix_sid(&cat.ix, slot, f2[0]);
            if (!e || e->dup) continue;
            int copy = !gone && n >= 2 && card_path(f2[1]);
            /* the copy the row has already (a song synced again): no new string */
            if (copy && e->local && !strcmp(ix_str(&cat.ix, e->local), f2[1])) continue;
            e->local = copy ? ix_put(&cat.ix, f2[1]) : 0;
            ++cat.local_gen;
        }
    /* a cut last line waits for its newline */
    fclose(f);
}

/* Whether app slot i is signed in to its server: its credentials file on the card (looked for,
 * never opened; signing out deletes it, q2-subsonic). Only a signed-in app's catalogue is in the
 * Library. */
static int cat_signed_in(int i) {
    char path[64];
    tk_snprintf(path, sizeof path, "/mnt/mmc/.%s/credentials", cat.app[i].id);
    return !access(path, 0);
}

/* Autostart: each catalogue app signed in is started in the background once, at boot, as a
 * catplay starts it (addon.c addon_wake), so its catalogue, covers and play reports catch up
 * without its page opened; it idle-exits as it would from its page. A signed-out app starts
 * nothing. cat_started: done since boot. */
static int cat_started __attribute__((section(".scratch")));
static void cat_autostart(void) {
    cat_started = 1;
    for (int i = 0; i < cat.napps; ++i)
        if (cat_signed_in(i)) addon_wake(cat.app[i].id);
}

static void ratings_in(int slot);
static void catplay_poll(void);
static void copies_build(void);
void catalogue_poll(void) {
    unsigned now = time_now_ms();
    if (cat.running && __atomic_load_n(&cat.done, __ATOMIC_ACQUIRE)) cat_swap();
    if (!cat.copies || cat.copies->gen != cat.local_gen) copies_build(); /* a new index */
    /* dark with nothing to play and no catplay waiting on its app, nothing here is looked at by
     * anyone: the app's files every CAT_DARK_MS, not each second */
    int idle = !g_backlight_status && !cat.cp_at && !cat.cp_wake && !cat.cp_link &&
               mclGetPlayStatus() != 2;
    if (now - cat.polled < (idle ? CAT_DARK_MS : CAT_POLL_MS)) return;
    /* nothing indexed and the screen off: no app's files looked at, so a Q2 with no server signed
     * in leaves the card alone while it is dark */
    if (!cat.ix.n && !cat.running && !g_backlight_status) return;
    cat.polled = now;
    /* not while a job runs: it reads the slots */
    if (!cat.running && (!cat.scanned || now - cat.scanned >= CAT_SCAN_MS)) {
        cat.scanned = now | 1, cat_scan();
        if (!cat_started && card_mounted()) cat_autostart(); /* the first scan with the card */
    }
    int up = 0;
    char seqs[CAT_APPS][24]; /* each app's catalogue.tsv seq, read once a poll; "" for none */
    for (int i = 0; i < cat.napps; ++i) {
        char path[48], raw[256], v[8];
        tk_snprintf(path, sizeof path, "/tmp/q2-%s.state", cat.app[i].id);
        state_read(path, raw, sizeof raw);
        field(raw, "server", v, sizeof v);
        cat.app[i].down = tk_str_start_with(v, "down");
        up |= !cat.app[i].down;
        cat_avail(i);
        /* a server row's line finds its path in the index: not while a job builds a new one, nor
         * before the app's catalogue is imported (a boot's first poll starts that job) */
        seqs[i][0] = 0;
        if (cat.running) continue;
        if (!cat_seq(cat.app[i].id, seqs[i])) seqs[i][0] = 0;
        if (!*seqs[i] || !strcmp(seqs[i], cat.app[i].seq)) ratings_in(i);
    }
    if (!cat.copies || cat.copies->gen != cat.local_gen) copies_build(); /* catalogue.avail's */
    /* Smart Wi-Fi's radio off or coming back counts: a press brings the link (catalogue_load) */
    cat.online = up && (networkGetConnectStatus() > 0 || smart_wifi_back());
    catplay_poll();
    if (cat.running || library_scanning() || !card_mounted()) return;
    for (int i = 0; i < cat.napps; ++i) {
        int has = *seqs[i] && cat_signed_in(i);
        if (has && strcmp(seqs[i], cat.app[i].seq)) {
            cat_start(i, seqs[i]);
            return;
        }
        if (!has && cat.ix.apps >> i & 1) { /* signed out, or its file gone: its rows leave */
            cat_start(i, 0);
            return;
        }
    }
    /* the card's library changed: matched again, so a song copied there leaves the server rows */
    if (cat.ix.n && library_gen != cat.gen) cat_start(-1, 0);
}

/* A catalogue app signed in to its server: its id (16 bytes) and its q2app's name (size bytes, the
 * id without one), 1; 0 with none. Signed in is its card folder holding its credentials file, which
 * is looked for and never opened: an app's status file is in /tmp and gone at boot, and signing out
 * deletes the file (q2-subsonic). The apps looked for again unless a job is reading them. */
int catalogue_signed(char *id, char *name, unsigned size) {
    if (!cat.running) cat_scan();
    char path[64], raw[512];
    for (int i = 0; i < cat.napps; ++i) {
        tk_snprintf(path, sizeof path, "/mnt/mmc/.%s/credentials", cat.app[i].id);
        if (access(path, 0)) continue;
        tk_snprintf(id, 16, "%s", cat.app[i].id);
        addon_q2app(cat.app[i].id, path, sizeof path);
        state_read(path, raw, sizeof raw);
        field(raw, "name", name, size);
        if (!*name) tk_snprintf(name, size, "%s", cat.app[i].id);
        return 1;
    }
    return 0;
}

/* The card going away (USB storage, addon_card_away): nothing of ours keeps it open. */
void catalogue_card_away(void) { cat_detach(); }

/* ---- Playing: a list with server rows ---- */

static int cat_app_of(const char *path) {
    for (int i = 0; i < cat.napps; ++i) {
        unsigned k = strlen(cat.app[i].id);
        if (!strncmp(path + 10, cat.app[i].id, k) && path[10 + k] == '/') return i;
    }
    return -1;
}
static unsigned play_hash(int slot) {
    char path[48], raw[600];
    tk_snprintf(path, sizeof path, "/tmp/q2-%s.play", cat.app[slot].id);
    state_read(path, raw, sizeof raw);
    return fnv(FNV_SEED, (const unsigned char *)raw);
}
/* The catplay request (docs/server-library.md): "<seq> <start>", then the list's paths. */
static int catplay_write(int slot, void *q, int at) {
    unsigned n = deque_size(q), cap = 64 + n * 160, len;
    char *b = calloc(1, cap), path[48];
    if (!b) return 0;
    len = (unsigned)tk_snprintf(b, cap, "%u %d\n", ++cat.cp_seq, at);
    for (unsigned i = 0; i < n; ++i) {
        const char *p = P(deque_at(q, i), REC_PATH);
        unsigned k = p ? (unsigned)strlen(p) : 0;
        while (len + k + 2 > cap) { /* doubled until the path, its newline and the NUL fit */
            char *g = realloc(b, cap *= 2);
            if (!g) {
                free(b);
                return 0;
            }
            b = g;
        }
        len += (unsigned)tk_snprintf(b + len, cap - len, "%s\n", p ? p : "");
    }
    tk_snprintf(path, sizeof path, "/tmp/q2-%s.catplay", cat.app[slot].id);
    ahead_start(cat.app[slot].id, cat.cp_seq); /* Download Ahead for the list, before it starts */
    int ok = blob_io(path, "/tmp/q2-catplay.tmp", b, len, 1);
    free(b);
    return ok;
}
/* q as the firmware plays it offline: card rows, and server rows by their offline copies; at
 * moved with it. 0 when nothing of q is left, *at then -1. */
static void *cat_offline(void *q, int *at) {
    void *out = _create_deque("stSongInfo");
    deque_init(out);
    int start = -1;
    for (unsigned i = 0, n = deque_size(q); i < n; ++i) {
        void *r = deque_at(q, i);
        const char *p = P(r, REC_PATH), *local = catalogue_path(p) ? cat_local(p) : p;
        if (!local) continue;
        if ((int)i >= *at && start < 0) start = (int)deque_size(out); /* it, or the next kept */
        _deque_push_back(out, r);
        if (local != p) { /* the copy's own record: its path the offline copy */
            void *c = deque_at(out, deque_size(out) - 1);
            char *s = strdup(local);
            if (s) free(P(c, REC_PATH)), P(c, REC_PATH) = s;
        }
    }
    if (!deque_size(out)) {
        deque_destroy(out);
        *at = -1;
        return 0;
    }
    *at = start < 0 ? 0 : start;
    return out;
}
/* ---- Now Playing while a catplay waits (server-library.md#now-playing-while-it-loads) ---- */

/* The song playing or paused, stopped once stock's press is over (its player_start's start of the
 * queue it had is refused: playback.c playback_start_refused). So its time and sound don't run on
 * under the pressed song's title. */
static int np_stop(const void *unused) {
    (void)unused;
    if (cat.np_list && mclGetPlayStatus() != 1) mclStop();
    return 0;
}
/* The song pressed, q's at'th: Now Playing shows it (catalogue_np_now) until the player parses a
 * song (id3_parses moves: the app's, the offline queue's or another the user picked), nothing
 * was left to play, or CATPLAY_SHOW_MS pass; the song playing stops (np_stop). */
static void np_press(void *q, int at) {
    if (cat.np_list) deque_destroy(cat.np_list), cat.np_list = 0;
    if (at < 0 || (unsigned)at >= deque_size(q)) return;
    cat.np_list = _create_deque("stSongInfo");
    deque_init(cat.np_list);
    _deque_push_back(cat.np_list, deque_at(q, (unsigned)at));
    cat.np_at = (unsigned)at, cat.np_n = deque_size(q);
    cat.np_parses = id3_parses, cat.np_since = time_now_ms();
    cat.np_text[0] = cat.np_text[1] = 0;
    cat.np_back = 0;
    tk_snprintf(cat.np_cover, sizeof cat.np_cover, "file://");
    if (!catalogue_cover(P(deque_at(cat.np_list, 0), REC_PATH), cat.np_cover + 7,
                         sizeof cat.np_cover - 7))
        tk_snprintf(cat.np_cover, sizeof cat.np_cover, "play_defaultcover");
    timer_add(np_stop, 0, 0);
}
/* A song not yet the player's shown on Now Playing as a press shows it (np_press: the song
 * playing stops, the spinner turns while it loads), r alone, at its place at of n: a card app's
 * playlist session's next song, which a Next waits for (addon.c addon_next_waits). */
void catalogue_np_show(void *r, unsigned at, unsigned n) {
    void *q = _create_deque("stSongInfo");
    deque_init(q);
    _deque_push_back(q, r);
    np_press(q, 0);
    deque_destroy(q);
    if (cat.np_list) cat.np_at = at, cat.np_n = n;
}
/* The pressed song shown no more: the player's title and artist go back on the page. Stock's update
 * writes them only while g_forcerefresh_flag (or g_autochange_flag) is set, and every library list
 * page's timer clears it too: one set here, from inside Now Playing's update (np_bar.c np_sync,
 * after its title), was taken by the Songs page under it before Now Playing's next update, so the
 * pressed song's title stayed on the song playing (the device, 2026-11-17, Shuffle Songs then
 * Next). catalogue_np_text writes them itself (np_back), the flag still asking stock for the rest.
 */
static void np_done(void) {
    if (!cat.np_list) return;
    deque_destroy(cat.np_list), cat.np_list = 0;
    cat.np_back = 1;
    g_forcerefresh_flag = 1;
}
/* The record Now Playing shows in place of the playing one, *at and *n its place in its list, or
 * 0 for none. */
void *catalogue_np_now(unsigned *at, unsigned *n) {
    if (cat.np_list &&
        (id3_parses != cat.np_parses || time_now_ms() - cat.np_since >= CATPLAY_SHOW_MS))
        np_done();
    if (!cat.np_list) return 0;
    *at = cat.np_at, *n = cat.np_n;
    return deque_at(cat.np_list, 0);
}
static unsigned wide_hash(const unsigned *s) {
    unsigned h = FNV_SEED;
    while (s && *s) h = (h ^ *s++) * 16777619u;
    return h;
}
/* win's title and artist (scrlabel_title, scrlabel_artist) as the shown song's, set again only
 * when their text is not what was set last (stock rewrites them on its own refreshes). After the
 * pressed song (np_back): once, the playing song's as the player parsed them (now_tag, whose tags
 * are complete once g_play_id3_info names its path), the record's name for a title it has none
 * of, as stock's g_song_name has it. */
void catalogue_np_text(void *win) {
    static const char *const names[] = { "scrlabel_title", "scrlabel_artist" };
    unsigned at, n;
    void *r = catalogue_np_now(&at, &n);
    if (!r && cat.np_back) {
        void *now = queue_now(&at, &n);
        if (!now || tk_strcmp((const char *)g_play_id3_info, P(now, REC_PATH))) return;
        cat.np_back = 0;
        for (int i = 0; i < 2; ++i) {
            void *label = widget_lookup(win, names[i], 1);
            const char *text = now_tag(now, i ? REC_ARTIST : REC_TITLE);
            if (!i && (!text || !*text)) text = P(now, REC_NAME);
            if (label) widget_set_text_utf8(label, text ? text : "");
        }
        return;
    }
    for (int i = 0; r && i < 2; ++i) {
        void *label = widget_lookup(win, names[i], 1);
        const char *text = P(r, i ? REC_ARTIST : REC_TITLE);
        if (!i && (!text || !*text)) text = P(r, REC_NAME); /* untagged: Search's name for it */
        if (!label || wide_hash(widget_get_text(label)) == cat.np_text[i]) continue;
        widget_set_text_utf8(label, text ? text : "");
        cat.np_text[i] = wide_hash(widget_get_text(label));
    }
}
/* Now Playing's art while it shows the pressed song: the app's cover of its album, else stock's
 * placeholder; 0 when it shows the playing song. */
const char *catalogue_np_cover(void) {
    unsigned at, n;
    return catalogue_np_now(&at, &n) ? cat.np_cover : 0;
}

static int catplay_send(void);
/* ringnav_load's: 0 when q has no server row (stock loads it as it is). Otherwise 1 with *own the
 * queue to load instead (offline: card rows and copies, playback.c destroys it), or 2: nothing to
 * load now (sent to the app, or nothing playable, said in a toast). */
int catalogue_load(void *q, int *at, void **own) {
    unsigned n = q ? deque_size(q) : 0, server = 0;
    *own = 0;
    for (unsigned i = 0; i < n && !server; ++i)
        server = catalogue_path(P(deque_at(q, i), REC_PATH));
    if (!server) return 0;
    void *start = *at >= 0 && (unsigned)*at < n ? deque_at(q, (unsigned)*at) : 0;
    if (start && catalogue_unavailable(start)) {
        toast("Not available offline");
        return 2;
    }
    const char *sp = start ? P(start, REC_PATH) : 0;
    int slot = sp && catalogue_path(sp) ? cat_app_of(sp) : -1;
    for (unsigned i = 0; i < n && slot < 0; ++i) {
        const char *p = P(deque_at(q, i), REC_PATH);
        if (catalogue_path(p)) slot = cat_app_of(p);
    }
    if (cat_online() && slot >= 0 && !cat.app[slot].down) {
        /* Smart Wi-Fi's link first (woken now); then, or with the link up, its app started when it
         * isn't running (after a boot, or its idle exit) */
        int link = !smart_wifi_wait(), woke = link ? addon_wake(cat.app[slot].id) : 0;
        if (cat.cp_list) deque_destroy(cat.cp_list);
        cat.cp_list = _create_deque("stSongInfo");
        deque_init(cat.cp_list);
        deque_assign(cat.cp_list, q);
        cat.cp_start = *at, cat.cp_app = slot, cat.cp_at = cat.cp_wake = 0, cat.cp_link = !link;
        if (woke == 1) cat.cp_wake = time_now_ms() | 1; /* written once it listens */
        if (cat.cp_link || cat.cp_wake || catplay_send()) {
            np_press(q, *at);
            return 2;
        }
        deque_destroy(cat.cp_list), cat.cp_list = 0;
    }
    *own = cat_offline(q, at);
    if (!*own) {
        toast("Not available offline");
        return 2;
    }
    return 1;
}
/* The catplay pending, cp_list from cp_start, written for cp_app; its wait begins. 0 when it
 * couldn't be written. */
static int catplay_send(void) {
    int slot = cat.cp_app;
    unsigned before = play_hash(slot);
    if (!catplay_write(slot, cat.cp_list, cat.cp_start)) return 0;
    cat.app[slot].play = before;
    cat.cp_at = time_now_ms() | 1;
    return 1;
}
/* The pending list played by the firmware, from card rows and offline copies. */
static void catplay_offline(void) {
    int at = cat.cp_start;
    void *own = cat_offline(cat.cp_list, &at);
    if (own) {
        play_folder(own, at, 0xf001);
        deque_destroy(own);
    } else {
        np_done();
        toast("Not available offline");
    }
}
/* A catplay waiting for Smart Wi-Fi's link: once it is up, its app started (or, running, the
 * catplay written); no link SMART_CONNECT_MS on (smart_wifi_wait gives up), the list played
 * offline. An app started for a catplay: the catplay written once it listens, the list played
 * offline if it doesn't within CATPLAY_START_MS. A catplay unanswered for CATPLAY_WAIT_MS: its list
 * played offline. One answered: done, the app's .play plays through addon.c's stream_poll. */
static void catplay_poll(void) {
    int slot = cat.cp_app;
    if (cat.cp_link && !smart_wifi_wait()) {
        cat.cp_link = 0;
        int woke =
            networkGetConnectStatus() > 0 && slot < cat.napps ? addon_wake(cat.app[slot].id) : -1;
        if (woke == 1)
            cat.cp_wake = time_now_ms() | 1;
        else if (woke || !catplay_send())
            catplay_offline();
    } else if (cat.cp_wake && slot < cat.napps && addon_ready(cat.app[slot].id)) {
        cat.cp_wake = 0;
        if (!catplay_send()) catplay_offline();
    } else if (cat.cp_wake && time_now_ms() - cat.cp_wake >= CATPLAY_START_MS) {
        cat.cp_wake = 0;
        catplay_offline();
    } else if (cat.cp_at && slot < cat.napps && play_hash(slot) != cat.app[slot].play) {
        cat.cp_at = 0;
    } else if (cat.cp_at && time_now_ms() - cat.cp_at >= CATPLAY_WAIT_MS) {
        cat.cp_at = 0;
        catplay_offline();
    }
    if (!cat.cp_at && !cat.cp_wake && !cat.cp_link && cat.cp_list)
        deque_destroy(cat.cp_list), cat.cp_list = 0;
}

/* ---- Server playlists changed on the Q2 (docs/server-library.md#playlists-from-the-q2) ---- */

extern int stock_db_open(const char *path, void **db); /* playlists.c */

/* A playlist's server identity (cat.serverlists): its app's id and its playlist id. */
typedef struct {
    char app[16], pid[200];
} pls_t;
static int pls_one(void *ctx, int n, char **v, char **names) {
    pls_t *p = ctx;
    if (n == 2 && v[0] && v[1] && !p->app[0]) {
        tk_snprintf(p->app, sizeof p->app, "%s", v[0]);
        tk_snprintf(p->pid, sizeof p->pid, "%s", v[1]);
    }
    return 0;
}
/* s to f, after a tab, with the card-app channel's escapes (%, tab, newline, return as %XX). */
static void pls_put(void *f, const char *s) {
    fwrite("\t", 1, 1, f);
    for (; s && *s; ++s) {
        char e[4];
        int esc = *s == '%' || *s == '\t' || *s == '\n' || *s == '\r';
        if (esc) tk_snprintf(e, sizeof e, "%%%02X", (unsigned char)*s);
        fwrite(esc ? e : s, 1, esc ? 3 : 1, f);
    }
}
static int pls_sid(void *f, int n, char **v, char **names) {
    if (n == 1 && v[0] && *v[0]) pls_put(f, v[0]);
    return 0;
}

/* The server playlists' covers, read once per Playlists list built (catalogue_playlist_covers):
 * each stock playlist's app and server id, at most 100. */
static struct {
    int n;
    struct {
        int list;
        pls_t id;
    } *at;
} plc __attribute__((section(".scratch")));
static int plc_row(void *ctx, int n, char **v, char **names) {
    if (n != 3 || !v[0] || !v[1] || !v[2] || plc.n >= 100) return 0;
    plc.at[plc.n].list = atoi(v[0]);
    tk_snprintf(plc.at[plc.n].id.app, sizeof plc.at[0].id.app, "%s", v[1]);
    tk_snprintf(plc.at[plc.n].id.pid, sizeof plc.at[0].id.pid, "%s", v[2]);
    ++plc.n;
    return 0;
}
/* The mapping read again, for catalogue_playlist_cover's asks that follow. */
void catalogue_playlist_covers(void) {
    void *db = 0;
    plc.n = 0;
    if (!catalogue_has() || (!plc.at && !(plc.at = calloc(100, sizeof *plc.at))) ||
        stock_db_open("/mnt/data/database.db", &db))
        goto done;
    sqlite3_exec(db, "select list,app,pid from cat.serverlists", plc_row, 0, 0);
done:
    if (db) sqlite3_close(db);
}
/* Server playlist list's cover, the app's covers/pl-<pid>.jpg (its server's coverArt,
 * docs/server-library.md#covers), as a file:// url in url (600 bytes); 0 when it has none yet or
 * is no server playlist. */
int catalogue_playlist_cover(int list, char *url) {
    for (int i = 0; i < plc.n; ++i) {
        pls_t *p = &plc.at[i].id;
        if (plc.at[i].list != list || strstr(p->app, "/") || strstr(p->pid, "/")) continue;
        tk_snprintf(url, 600, "file:///mnt/mmc/.%s/covers/pl-%s.jpg", p->app, p->pid);
        return file_size(url + 7) > 0;
    }
    return 0;
}

/* Playlist list (0 to 99) changed on the Q2 (playlists.c): when it is a server playlist, a line to
 * its app's playlists.out, which the app sends on to the server and then empties: "set PID SID..."
 * (its songs in their own order, after an add or a removal; a song with no server id, a card file
 * the server doesn't have, is left out), "name PID NAME" after a Rename, "delete PID" after a
 * Delete. A local playlist, or no signed-in app's rows indexed, writes nothing. On the UI thread: a
 * lookup of the playlist and one read of its songs' ids through stock's connection, with cat
 * attached. */
void catalogue_playlist_sync(int list, int what, const char *name) {
    void *db = 0, *f = 0;
    pls_t p = { "", "" };
    char sql[600], file[64];
    if (!catalogue_has() || list < 0 || list >= 100 || stock_db_open("/mnt/data/database.db", &db))
        goto done;
    tk_snprintf(sql, sizeof sql, "select app,pid from cat.serverlists where list=%d", list);
    if (sqlite3_exec(db, sql, pls_one, &p, 0) || !p.app[0] || strstr(p.app, "/")) goto done;
    tk_snprintf(file, sizeof file, "/mnt/mmc/.%s/playlists.out", p.app);
    if (!(f = fopen(file, "ab"))) goto done;
    const char *op = what == PLS_SET ? "set" : what == PLS_NAME ? "name" : "delete";
    fwrite(op, 1, strlen(op), f);
    pls_put(f, p.pid);
    if (what == PLS_NAME) pls_put(f, name);
    if (what == PLS_SET) {
        tk_snprintf(sql, sizeof sql,
                    "select ifnull((select sid from cat.servermeta m where m.fileurl=p.fileurl or "
                    "m.dup=p.fileurl limit 1),(select sid from cat.serveritem i where "
                    "i.path=p.fileurl limit 1)) from main.playlist p where p.id>100 and "
                    "p.cd_num=%d order by p.id",
                    list);
        sqlite3_exec(db, sql, pls_sid, f, 0);
    }
    fwrite("\n", 1, 1, f);
done:
    if (f) fclose(f);
    if (db) sqlite3_close(db);
}

/* ---- Sync Offline (docs/internals.md#sync-offline) ---- */

/* What a Library item is on its server, for its app's Sync Offline (offline.c): its app's id into
 * app (16 bytes) and "<kind> <id>" into key (n bytes), 1; 0 for an item no signed-in server has.
 * kind OFF_SONG: the record r, a server row or a card file linked to one, by its sid; OFF_ALBUM: an
 * album list's record, by the album id in the path of one of its server rows (cat/<album>/<sid>),
 * a row whose album and album artist (or artist) are r's; OFF_ARTIST: an artist list's record, by
 * name ("artistname <name>"), when a server row has that artist or album artist; OFF_PLAYLIST:
 * stock's playlist list (0 to 99), a server playlist by its id (cat.serverlists). */
int catalogue_offline_key(int kind, void *r, int list, char *app, char *key, unsigned n) {
    *app = *key = 0;
    if (!catalogue_has()) return 0;
    if (kind == OFF_PLAYLIST) {
        void *db = 0;
        pls_t pl = { "", "" };
        char sql[96];
        if (list < 0 || list >= 100 || stock_db_open("/mnt/data/database.db", &db)) return 0;
        tk_snprintf(sql, sizeof sql, "select app,pid from cat.serverlists where list=%d", list);
        sqlite3_exec(db, sql, pls_one, &pl, 0);
        sqlite3_close(db);
        if (!pl.app[0] || !pl.pid[0]) return 0;
        tk_snprintf(app, 16, "%s", pl.app);
        tk_snprintf(key, n, "playlist %s", pl.pid);
        return 1;
    }
    if (!r) return 0;
    const cat_ent *e = 0;
    if (kind == OFF_SONG) {
        if (!(e = cat_lookup(P(r, REC_PATH))) || e->app >= cat.napps) return 0;
        tk_snprintf(key, n, "song %s", ix_str(&cat.ix, e->sid));
    } else {
        const char *name = P(r, kind == OFF_ALBUM ? REC_ALBUM : REC_ARTIST);
        unsigned h = group_hash(name), by = kind == OFF_ALBUM ? group_hash(P(r, REC_ARTIST)) : 0;
        for (unsigned i = 0; h && i < cat.ix.n && !e; ++i) {
            const cat_ent *c = &cat.ix.e[i];
            if (c->dup || c->app >= cat.napps) continue;
            if (kind == OFF_ALBUM ? c->grp[0] == h && (!by || c->grp[1] == by || c->grp[2] == by)
                                  : c->grp[1] == h || c->grp[2] == h)
                e = c;
        }
        if (!e) return 0;
        if (kind == OFF_ARTIST)
            tk_snprintf(key, n, "artistname %s", name);
        else { /* the album id: the path's folder under cat/ */
            const char *path = ix_str(&cat.ix, e->path), *at = strstr(path, "/cat/"),
                       *end = at ? strstr(at + 5, "/") : 0;
            if (!end || end == at + 5) return 0;
            tk_snprintf(key, n, "album %.*s", (int)(end - at - 5), at + 5);
        }
    }
    tk_snprintf(app, 16, "%s", cat.app[e->app].id);
    return 1;
}

/* ---- Ratings (docs/server-library.md#ratings-and-favorites) ---- */

/* A song's stars or like changed on the Q2: a line ("stars=N" or "like=0|1") to each app with
 * ratings=2. An app with ratings=1 takes one number, five for its star, so it can carry neither
 * apart from the other and is sent nothing. A server song playing from its offline copy is its
 * server row (catalogue_song_key): the row's key and sid. */
static void rated_line(void *r, const char *what, int v) {
    const char *path = r ? P(r, REC_PATH) : 0;
    if (!path || !cat.napps) return;
    const cat_ent *row = cat_row_of(r), *e = row ? row : cat_lookup(path);
    unsigned key = row ? row_key(row) : listen_key(r);
    char line[1200], file[64];
    for (int i = 0; i < cat.napps; ++i) {
        if (cat.app[i].ratings < 2) continue;
        const char *sid = e && e->app == i ? ix_str(&cat.ix, e->sid) : "";
        int k = tk_snprintf(line, sizeof line, "%08x\t%s\t%s=%d\t%u\n", key, sid, what, v,
                            (unsigned)time(0));
        if (k <= 0 || (unsigned)k >= sizeof line) continue;
        tk_snprintf(file, sizeof file, "/mnt/mmc/.%s/ratings.out", cat.app[i].id);
        void *f = fopen(file, "ab");
        if (f) fwrite(line, 1, (unsigned)k, f), fclose(f);
    }
}
void catalogue_rated(void *r, int stars) { rated_line(r, "stars", stars); }
void catalogue_liked(void *r, int on) { rated_line(r, "like", on != 0); }

/* A line's third column: 'l' for a like, 's' for stars, 'h' for an app's one number (ratings=1:
 * five its star, else its stars and no star), 0 for none of them. */
static char rated_kind(const char *v) {
    return !strncmp(v, "like=", 5)    ? 'l'
           : !strncmp(v, "stars=", 6) ? 's'
           : *v >= '0' && *v <= '9'   ? 'h'
                                      : 0;
}
static unsigned hex32(const char *s);
/* ratings.out's lines, each its key, kind ('l', 's' or 'h') and time, sorted by key: read and
 * indexed once a ratings.in, so each line looks its key up rather than reading the file again. */
typedef struct {
    unsigned key, t;
    char kind;
} out_t;
static int out_cmp(const void *a, const void *b) {
    unsigned x = ((const out_t *)a)->key, y = ((const out_t *)b)->key;
    return x < y ? -1 : x > y;
}
static out_t *out_index(char *out, unsigned *n) {
    unsigned lines = 0;
    *n = 0;
    for (const char *l = out; l && *l; l = strstr(l, "\n") ? strstr(l, "\n") + 1 : 0) ++lines;
    out_t *o = lines ? calloc(lines, sizeof *o) : 0;
    for (char *l = out, *e; o && l && *l; l = e) {
        if ((e = strstr(l, "\n"))) *e++ = 0;
        char *f[4];
        unsigned key;
        if (tsv_split(l, f, 4) < 4 || !(key = hex32(f[0]))) continue;
        o[*n].key = key, o[*n].kind = rated_kind(f[2]), o[*n].t = (unsigned)atoi(f[3]);
        ++*n;
    }
    if (*n) qsort(o, *n, sizeof *o, out_cmp);
    return o;
}
/* The newest time the index holds for key changing kind, 0 for none; a line of one number changed
 * both. */
static unsigned out_time(const out_t *o, unsigned n, unsigned key, char kind) {
    unsigned lo = 0, hi = n, t = 0;
    while (lo < hi)
        if (o[(lo + hi) / 2].key < key)
            lo = (lo + hi) / 2 + 1;
        else
            hi = (lo + hi) / 2;
    for (; lo < n && o[lo].key == key; ++lo)
        if ((o[lo].kind == kind || o[lo].kind == 'h') && o[lo].t > t) t = o[lo].t;
    return t;
}
static unsigned hex32(const char *s) {
    unsigned v = 0;
    for (int i = 0; i < 8; ++i) {
        int d = hexv(s[i]);
        if (d < 0) return 0;
        v = v << 4 | (unsigned)d;
    }
    return s[8] ? 0 : v;
}
/* ratings.in's lines, by key: a card song's path found in songtable by its listen_key (the app's
 * synced files are not in the catalogue), in one pass over it for all of them. */
typedef struct {
    unsigned key;
    int stars, like;   /* -1: the line leaves it as it is */
    char *path, *copy; /* copy: a server row's Stream Cache copy, or 0 */
} in_t;
typedef struct {
    in_t *l;
    unsigned n;
} in_set;
static int in_cmp(const void *a, const void *b) {
    unsigned x = ((const in_t *)a)->key, y = ((const in_t *)b)->key;
    return x < y ? -1 : x > y;
}
static int key_row(void *ctx, int n, char **v, char **c) {
    in_set *k = ctx;
    int rec[REC_CUE_END / 4 + 2] = { 0 };
    if (!v[0]) return 0;
    P(rec, REC_PATH) = v[0], I(rec, REC_CUE_START) = v[1] ? atoi(v[1]) : 0;
    unsigned key = listen_key(rec), lo = 0, hi = k->n;
    while (lo < hi)
        if (k->l[(lo + hi) / 2].key < key)
            lo = (lo + hi) / 2 + 1;
        else
            hi = (lo + hi) / 2;
    for (; lo < k->n && k->l[lo].key == key; ++lo) /* a song's like and stars: a line each */
        if (!k->l[lo].path) k->l[lo].path = strdup(v[0]);
    return 0;
}
extern int rate_keep(int album, unsigned key, int n), rate_release(void);
extern void rate_hold(void);
extern int stock_db_open(const char *path, void **db); /* playlists.c */
/* My Fav: the song's row in or out, as stock keeps it, and copy's own row (a server row's Stream
 * Cache copy, which stock's heart puts in by its path, or 0) out either way; on db, or a
 * connection of its own for 0. */
static void fav_set_on(void *db, const char *path, int on, const char *copy) {
    char sql[1700], *o = sql, *end = sql + sizeof sql - CAT_SLACK;
    PUT(o, end, "delete from myfav where fileurl=");
    o = sq(o, end, path);
    if (copy) PUT(o, end, " or fileurl="), o = sq(o, end, copy);
    if (on) {
        PUT(o, end,
            ";insert into myfav(filename,fileurl,album,songer,style,name,composer,albumsonger,year,"
            "track,cd_num,time,time_end,time_create,Initial,bitdepth,samprate) select filename,"
            "fileurl,album,songer,style,name,composer,albumsonger,year,track,cd_num,time,time_end,"
            "time_create,Initial,bitdepth,samprate from %s where fileurl=",
            catalogue_path(path) ? "servertable" : "songtable");
        o = sq(o, end, path), PUT(o, end, " limit 1");
    }
    int own = !db;
    if (o >= end || (own && stock_db_open("/mnt/data/database.db", &db))) return;
    *o = 0;
    sqlite3_exec(db, sql, 0, 0, 0);
    if (own) sqlite3_close(db);
}
static void fav_set(const char *path, int on, const char *copy) { fav_set_on(0, path, on, copy); }
/* My Fav's contents may have changed: catalogue_row_fav reads the table again. */
void catalogue_fav_changed(void) { ++cat.fav_gen; }
/* Moves with each catalogue_fav_changed: Now Playing reads a copy's own My Fav again (ratings.c
 * rate_row_heart). */
unsigned catalogue_fav_gen(void) { return cat.fav_gen; }
static int fav_found(void *ctx, int n, char **v, char **c) {
    *(int *)ctx = 1;
    return 0;
}
/* Whether r, a server song's Stream Cache copy, is a favourite by its server row: the row's path
 * in stock's myfav, where five hearts from the server (ratings_in) or the queue menu on the
 * Library's row put it. Stock checks the copy's own path, which isn't there. -1 when r is
 * no copy. Now Playing asks twice a second (ratings.c rate_heart): the answer is kept until the
 * row, the index or My Fav changes. */
int catalogue_row_fav(void *r) {
    const cat_ent *e = cat_row_of(r);
    if (!e) return -1;
    unsigned row = (unsigned)(e - cat.ix.e) + 1;
    if (cat.fav_row != row || cat.fav_local != cat.local_gen + 1 || cat.fav_seen != cat.fav_gen) {
        void *db = 0;
        char sql[640], *o = sql, *end = sql + sizeof sql - CAT_SLACK;
        int on = 0;
        PUT(o, end, "select 1 from myfav where fileurl=");
        o = sq(o, end, ix_str(&cat.ix, e->path)), PUT(o, end, " limit 1");
        if (o >= end || stock_db_open("/mnt/data/database.db", &db)) return 0; /* asked again */
        *o = 0;
        sqlite3_exec(db, sql, fav_found, &on, 0);
        sqlite3_close(db);
        cat.fav_row = row, cat.fav_local = cat.local_gen + 1, cat.fav_seen = cat.fav_gen;
        cat.fav_on = on;
    }
    return cat.fav_on;
}
/* Takes r's server row out of My Fav (r a Stream Cache copy; any other record, nothing). */
void catalogue_row_unfav(void *r) {
    const cat_ent *e = cat_row_of(r);
    if (e) fav_set(ix_str(&cat.ix, e->path), 0, 0), catalogue_fav_changed();
}

/* ratings.in's lines applied as a change made here: "stars=N" kept by the key (rate_keep),
 * "like=0|1" My Fav by the path (a server row's Stream Cache copy out of My Fav either way, so a
 * heart given to the copy here follows the server's). An app with ratings=1 writes one number:
 * five is its star, the like, with the stars left as they are; under five is that many stars and
 * no like. A line is passed over when the Q2's own change of the same kind to that song, still in
 * ratings.out, is newer (ratings.out indexed once, out_index). The file is renamed before it is
 * read, so lines the app writes meanwhile go to a new ratings.in, not lost. The whole file is one
 * pass: the stars written once, My Fav in one transaction. */
static void ratings_in(int slot) {
    char in[64], taking[72], outp[64];
    if (!cat.app[slot].ratings) return;
    tk_snprintf(in, sizeof in, "/mnt/mmc/.%s/ratings.in", cat.app[slot].id);
    tk_snprintf(taking, sizeof taking, "%s.taking", in);
    tk_snprintf(outp, sizeof outp, "/mnt/mmc/.%s/ratings.out", cat.app[slot].id);
    if (access(taking, 0) && (access(in, 0) || rename(in, taking))) return;
    unsigned size = 0, osize = 0, lines = 0, nout = 0;
    char *raw = read_all(taking, 1 << 20, &size);
    char *out = raw ? read_all(outp, 1 << 20, &osize) : 0;
    out_t *ox = out ? out_index(out, &nout) : 0;
    for (char *l = raw; l && *l; l = strstr(l, "\n") ? strstr(l, "\n") + 1 : 0) ++lines;
    in_set k = { lines ? calloc(lines, sizeof *k.l) : 0, 0 };
    for (char *l = raw, *e; k.l && l && *l; l = e) {
        if ((e = strstr(l, "\n"))) *e++ = 0;
        char *f[4];
        if (tsv_split(l, f, 4) < 4) continue;
        unsigned key = hex32(f[0]);
        char kind = rated_kind(f[2]);
        int v = atoi(f[2] + (kind == 'l' ? 5 : kind == 's' ? 6 : 0));
        if (!key || !kind || v < 0 || v > (kind == 'l' ? 1 : 5)) continue;
        int stars = kind == 's' || (kind == 'h' && v < 5) ? v : -1;
        int like = kind == 'l' ? v : kind == 'h' ? v == 5 : -1;
        unsigned at = (unsigned)atoi(f[3]);
        if (stars >= 0 && out_time(ox, nout, key, 's') > at) stars = -1;
        if (like >= 0 && out_time(ox, nout, key, 'l') > at) like = -1;
        if (stars < 0 && like < 0) continue;
        in_t *t = &k.l[k.n++];
        t->key = key, t->stars = stars, t->like = like;
        const cat_ent *x = *f[1] ? ix_sid(&cat.ix, slot, f[1]) : 0;
        if (x) t->path = strdup(ix_str(&cat.ix, x->path));
        if (x && !x->dup && x->local)
            t->copy = strdup(ix_str(&cat.ix, x->local)); /* cat_row_of's */
    }
    qsort(k.l, k.n, sizeof *k.l, in_cmp);
    unsigned unknown = 0;
    for (unsigned i = 0; i < k.n; ++i) unknown += !k.l[i].path;
    void *db = 0;
    if (unknown && !stock_db_open("/mnt/data/database.db", &db)) {
        sqlite3_exec(db, "select fileurl,time from songtable", key_row, &k, 0);
        sqlite3_close(db);
    }
    /* the whole file at once: the stars in the table, written once (rate_release); My Fav's rows
     * on one connection in one transaction, so one journal and one sync */
    int likes = 0;
    for (unsigned i = 0; i < k.n; ++i) likes += k.l[i].like >= 0 && k.l[i].path;
    db = 0;
    if (likes && !stock_db_open("/mnt/data/database.db", &db))
        sqlite3_exec(db, "begin", 0, 0, 0);
    rate_hold();
    for (unsigned i = 0; i < k.n; ++i) {
        if (k.l[i].stars >= 0) rate_keep(0, k.l[i].key, k.l[i].stars);
        if (k.l[i].like >= 0 && k.l[i].path && db) fav_set_on(db, k.l[i].path, k.l[i].like, k.l[i].copy);
        free(k.l[i].path), free(k.l[i].copy);
    }
    rate_release();
    if (db) sqlite3_exec(db, "commit", 0, 0, 0), sqlite3_close(db);
    if (likes) catalogue_fav_changed();
    free(k.l), free(raw), free(out), free(ox);
    unlink(taking);
    ratings_copy_due();
}
