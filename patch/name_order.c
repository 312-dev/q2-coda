/* A sorted library query's order kept across loads and boots (docs/internals.md#name-order).
 * toolsQueryDbTable (with its sort argument) and toolsQueryDbTableAndSort (in every mode) sort a
 * query's records in C, algo_sort_if with one of stock's comparators, and on a large library that
 * sort is most of a list's open: in the emulator, 1.3-2.7 s of a Songs open at 20,103 songs. The
 * same records in the same order sort the same way, so library_query asks for them unsorted (the
 * same exec and row callback) and hands them here: name_order sorts them as stock would have (the
 * same call, the same comparator: identical order, equal names included), and keeps each record's
 * id in that order under a hash of the records as they came, in RAM and on the card. The next load
 * whose records come the same (the query's text and sort, the comparator flag, the count and the
 * hash, after a reboot too) moves them into the kept order instead (rec_permute). A full sort runs
 * on a thread of its own under the loading overlay (spin_sort, list_marks.c load_block). After a
 * library or catalogue change the Library's Songs, Albums and Artists are loaded again and sorted
 * ahead in the UI's quiet (name_order_poll), so their next open finds the order kept. */
#include "navigation.h"

#define NO_SLOTS 4
#define NO_MIN 512 /* below this many records the sort is a few ms: nothing kept */
#define NO_FILE "/mnt/mmc/.q2pod/order%d"
#define NO_MAGIC 0x314f4e51u /* "QNO1" */
#define WARM_IDLE_MS 3000 /* with the screen lit, no list is loaded ahead this soon after input */

/* A slot's head, also its file's: the firmware (Q2POD_VERSION's hash: ringnav_sort_key is in the
 * comparators), the query key, the count, the records' hash, the kept ids' own hash and the Library
 * list the order is (name_order_list's cls, 0 for any other load). */
typedef struct {
    unsigned magic, version, key, n;
    unsigned long long sum;
    unsigned idsum, cls;
} no_head;
typedef struct {
    no_head h; /* h.n 0: empty */
    unsigned used;
    no_head *file; /* the head, then the ids in the sorted order; 0 while on the card only */
} no_slot;
static struct {
    no_slot s[NO_SLOTS];
    unsigned tick;
    int read; /* the card's heads read, once a boot */
    void *list, *cmp;
    volatile int done;
} no __attribute__((section(".scratch")));
#define IDS(f) ((int *)((f) + 1))

static unsigned long long fnv64(unsigned long long h, const char *s) {
    if (s)
        while (*s) h = (h ^ (unsigned char)*s++) * 0x100000001b3ull;
    return (h ^ 0xff) * 0x100000001b3ull; /* a field's end: "ab","c" is not "a","bc" */
}
/* The records as they came, in order: each id and what sort's comparator reads (the type, then the
 * title and the file name, or the path for toolsQueryDbTableAndSort's mode 2). */
static unsigned long long list_hash(void *list, int sort) {
    unsigned long long h = 0xcbf29ce484222325ull;
    char id[24];
    for (unsigned i = 0, n = deque_size(list); i < n; ++i) {
        void *r = deque_at(list, i);
        tk_snprintf(id, sizeof id, "%d/%d", I(r, REC_ID), I(r, REC_TYPE));
        h = fnv64(h, id);
        h = sort == 0x102 ? fnv64(h, P(r, REC_PATH))
                          : fnv64(fnv64(h, P(r, REC_TITLE)), P(r, REC_NAME));
    }
    return h;
}
/* The query's text, its sort argument and which comparator pair stock picks (NAME_SORT_FLAG). */
static unsigned query_key(const char *sql, int sort) {
    unsigned h = 0x811c9dc5u ^ (unsigned)sort ^ (unsigned)B(NAME_SORT_FLAG, 0) << 16;
    while (*sql) h = (h ^ (unsigned char)*sql++) * 0x01000193u;
    return h;
}
/* library_query's sort: toolsQueryDbTable's argument, or 0x100 and toolsQueryDbTableAndSort's mode.
 */
static void *comparator(int sort) {
    int set = B(NAME_SORT_FLAG, 0);
    return (void *)(sort == 0x101   ? set ? FILE_NAME_CMP : FILE_NAME_CMP_PINYIN
                    : sort == 0x102 ? PATH_CMP
                    : set           ? NAME_CMP
                                    : NAME_CMP_PINYIN);
}
#ifdef __mips__
/* list sorted as toolsQueryDbTable sorts it (test/fw stands in for this call) */
void cstl_sort(void *list, void *cmp) { algo_sort_if(deque_begin(list), deque_end(list), cmp); }
#endif

static char *path(char *s, int i) {
    tk_snprintf(s, 32, NO_FILE, i);
    return s;
}
static unsigned version(void) { return fnv(FNV_SEED, (const unsigned char *)Q2POD_VERSION); }
static unsigned id_hash(const no_head *f) {
    return hash_bytes(FNV_SEED, (const unsigned char *)IDS(f), f->n * 4);
}

/* Slot i's file, whole, when it is still the one its head named; never a torn or foreign one. */
static no_head *card_file(int i) {
    no_slot *k = &no.s[i];
    char s[32], end;
    void *f = fopen(path(s, i), "rb");
    no_head *b = f ? calloc(1, sizeof *b + k->h.n * 4) : 0;
    int ok = b && fread(b, sizeof *b + k->h.n * 4, 1, f) == 1 && !fread(&end, 1, 1, f) &&
             !memcmp(b, &k->h, sizeof *b) && id_hash(b) == b->idsum;
    if (f) fclose(f);
    if (!ok) free(b), b = 0, k->h.n = 0;
    return b;
}
/* Each slot's head as the last boot left it, read once a boot. */
static void read_heads(void) {
    for (int i = 0; i < NO_SLOTS && !no.read; ++i) {
        char s[32];
        no_head *h = &no.s[i].h;
        void *f = fopen(path(s, i), "rb");
        if (f &&
            (fread(h, sizeof *h, 1, f) != 1 || h->magic != NO_MAGIC || h->version != version()))
            memset(h, 0, sizeof *h);
        if (f) fclose(f);
    }
    no.read = 1;
}
static no_slot *find(unsigned key, unsigned n, unsigned long long sum) {
    read_heads();
    for (int i = 0; i < NO_SLOTS; ++i) {
        no_slot *k = &no.s[i];
        if (k->h.n == n && k->h.key == key && k->h.sum == sum &&
            (k->file || (k->file = card_file(i))))
            return k;
    }
    return 0;
}

/* list (the unsorted load) moved into k's order; 0 when an id is not k's or comes twice. */
static int apply(no_slot *k, void *list) {
    unsigned n = k->h.n, m = 2;
    const int *ids = IDS(k->file);
    while (m < 2 * n) m *= 2;
    unsigned *slot = calloc(m + n, sizeof *slot), *at = slot + m;
    if (!slot) return 0;
    /* the kept ids' places, by id: slot holds place + 1 */
    for (unsigned i = 0; i < n; ++i) {
        unsigned h = (unsigned)ids[i] * 0x9e3779b1u & (m - 1);
        while (slot[h] && ids[slot[h] - 1] != ids[i]) h = (h + 1) & (m - 1);
        if (slot[h]) return free(slot), 0; /* a repeated id: no one place for it */
        slot[h] = i + 1;
    }
    for (unsigned i = 0; i < n; ++i) at[i] = n;
    for (unsigned i = 0; i < n; ++i) {
        int id = I(deque_at(list, i), REC_ID);
        unsigned h = (unsigned)id * 0x9e3779b1u & (m - 1);
        while (slot[h] && ids[slot[h] - 1] != id) h = (h + 1) & (m - 1);
        if (!slot[h] || at[slot[h] - 1] != n) return free(slot), 0;
        at[slot[h] - 1] = i; /* that place takes record i */
    }
    rec_permute(list, at);
    free(slot);
    k->used = ++no.tick;
    return 1;
}

/* After a full sort: list's order kept under key in that key's slot, else in the slot of the same
 * Library list (cls, NO_SONGS and on; its records have changed), else the least recently used, and
 * written to its file (blob_io: a temporary file, synced, renamed over). */
static void keep(unsigned key, unsigned long long sum, void *list, unsigned cls) {
    unsigned n = deque_size(list);
    no_slot *k = 0;
    for (int i = 0; i < NO_SLOTS; ++i)
        if (no.s[i].h.key == key && no.s[i].h.n) k = &no.s[i];
    for (int i = 0; i < NO_SLOTS && !k && cls; ++i)
        if (no.s[i].h.cls == cls && no.s[i].h.n) k = &no.s[i];
    if (!k)
        for (int i = 0; i < NO_SLOTS; ++i)
            if (!k || no.s[i].used < k->used) k = &no.s[i];
    no_head *f = realloc(k->file, sizeof *f + n * 4);
    if (!f) {
        free(k->file), k->file = 0, k->h.n = 0;
        return;
    }
    for (unsigned i = 0; i < n; ++i) IDS(f)[i] = I(deque_at(list, i), REC_ID);
    no_head h = { NO_MAGIC, version(), key, n, sum, 0, cls };
    f->n = n;
    h.idsum = id_hash(f);
    *f = k->h = h;
    k->file = f, k->used = ++no.tick;
    char s[32], tmp[40];
    path(s, (int)(k - no.s));
    tk_snprintf(tmp, sizeof tmp, "%s.tmp", s);
    mkdir("/mnt/mmc/.q2pod", 0755);
    if (!blob_io(s, tmp, f, sizeof *f + n * 4, 1)) unlink(tmp);
}

/* What a sort's wait says under the spinner (list_marks.c load_block): the Library list it sorts,
 * by name_order_list's cls. */
static const char *sort_caption(unsigned cls) {
    return cls == NO_SONGS     ? "Sorting songs\xe2\x80\xa6"
           : cls == NO_ALBUMS  ? "Sorting albums\xe2\x80\xa6"
           : cls == NO_ARTISTS ? "Sorting artists\xe2\x80\xa6"
                               : "Sorting\xe2\x80\xa6";
}
static void *sorter(void *arg) {
    cstl_sort(no.list, no.cmp);
    no.done = 1;
    return arg;
}
/* list sorted on a thread of its own while the UI thread waits under the loading overlay, caption
 * under it (list_marks.c load_block: it shows once the sort has run LOAD_SHOW_MS); in place when
 * no thread starts (a started one has a nonzero id). */
static void spin_sort(void *list, void *cmp, const char *caption) {
    unsigned long t = 0;
    no.list = list, no.cmp = cmp, no.done = 0;
    if (pthread_create(&t, 0, sorter, 0) || !t) {
        cstl_sort(list, cmp);
        return;
    }
    load_block(&no.done, caption);
    pthread_join(t, 0);
}

/* Sorted ahead (docs/internals.md#name-order): after a boot, and each time the library or the
 * server catalogue has changed since (library_gen moved: a scan's end, a catalogue's load or
 * import), Songs, Albums and Artists are each loaded again as their open loads them (library_warm,
 * on the UI thread, with the card mounted and the screen dark or WARM_IDLE_MS after the last
 * input), opened before or
 * not, and when their records are not the kept ones (changed, or kept by another firmware) they
 * are sorted on a thread of their own with stock's call and comparator while the UI runs on, and
 * kept as an open's sort keeps them. The open after it finds its order kept. An open that meets
 * that sort still running waits for it, under the spinner, rather than sorting again. */
static struct {
    unsigned gen, due;  /* library_gen as last looked at; a bit (1 << cls) per list to load again */
    int seen;           /* library_gen looked at since the boot */
    unsigned cls;       /* the list a load is for: an open's (name_order_list), or the warm's */
    int warming;        /* name_order is called from library_warm: a sort is noted, not run */
    int noted, running; /* the warm's records to sort (key, sum, n, sort); its sort on a thread */
    unsigned key, n, at, wcls;
    int sort;
    unsigned long long sum;
    void *list, *cmp;
    unsigned long thread;
    volatile int done;
} nw __attribute__((section(".scratch")));

void name_order_list(unsigned cls) { nw.cls = cls; }

static void *warm_sorter(void *arg) {
    cstl_sort(nw.list, nw.cmp);
    nw.done = 1;
    return arg;
}
/* The warm's sort is done: its order kept, its records dropped. */
static void warm_end(void) {
    pthread_join(nw.thread, 0);
    keep(nw.key, nw.sum, nw.list, nw.wcls);
    char msg[80];
    tk_snprintf(msg, sizeof msg, "list %u sorted ahead: %u rows in %u ms", nw.wcls, nw.n,
                (unsigned)time_now_ms() - nw.at);
    ulog('I', "ui", msg);
    deque_destroy(nw.list);
    nw.list = 0, nw.running = 0;
}

/* list, sql's records unsorted as library_query loaded them, in the order stock's sort gives
 * them; sort is library_query's (comparator). */
void name_order(const char *sql, int sort, void *list) {
    unsigned n = deque_size(list);
    void *cmp = comparator(sort);
    if (n < NO_MIN) {
        if (n > 1 && !nw.warming) cstl_sort(list, cmp);
        return;
    }
    unsigned key = query_key(sql, sort);
    unsigned long long sum = list_hash(list, sort);
    if (nw.warming) { /* sorted once library_warm returns, if not kept (name_order_poll) */
        if (!find(key, n, sum)) nw.noted = 1, nw.key = key, nw.sum = sum, nw.n = n, nw.sort = sort;
        return;
    }
    no_slot *k = find(key, n, sum);
    if (!k && nw.running) { /* the warm's sort may be this load's: waited for, then looked again */
        load_block(&nw.done, sort_caption(nw.cls));
        warm_end();
        k = find(key, n, sum);
    }
    if (k && apply(k, list)) return;
    spin_sort(list, cmp, sort_caption(nw.cls));
    keep(key, sum, list, nw.cls);
}

/* Every UI loop pass (ringnav_sleep): the warm's sort collected when done, and the lists due after
 * a library_gen change loaded again, one a pass. */
void name_order_poll(void) {
    if (nw.running) {
        if (nw.done) warm_end();
        return;
    }
    if (!nw.seen || nw.gen != library_gen)
        nw.seen = 1, nw.gen = library_gen,
        nw.due = 1u << NO_SONGS | 1u << NO_ALBUMS | 1u << NO_ARTISTS;
    if (!nw.due || !card_mounted() || library_scanning() || coverflow_opening() ||
        (g_backlight_status && (unsigned)time_now_ms() - st.last_input < WARM_IDLE_MS))
        return;
    unsigned cls = 0;
    while (!(nw.due >> cls & 1)) ++cls;
    nw.due &= ~(1u << cls);
    void *list = _create_deque("stSongInfo");
    deque_init(list);
    ui_watch_step("library warm");
    nw.warming = 1, nw.noted = 0;
    library_warm(cls, list);
    nw.warming = 0;
    /* stock adds rows after its sort (an Unknown row): the records as they were hashed, else none
     */
    for (unsigned m = deque_size(list); nw.noted && m > nw.n; --m) deque_pop_back(list);
    unsigned long t = 0;
    if (nw.noted && deque_size(list) == nw.n && list_hash(list, nw.sort) == nw.sum) {
        nw.list = list, nw.cmp = comparator(nw.sort), nw.wcls = cls, nw.done = 0;
        nw.at = (unsigned)time_now_ms();
        if (!pthread_create(&t, 0, warm_sorter, 0) && t) {
            nw.thread = t, nw.running = 1;
            return;
        }
        nw.list = 0;
    }
    deque_destroy(list);
}
