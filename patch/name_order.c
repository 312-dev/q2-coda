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
 * on a thread of its own while the UI thread draws a spinner on the screen (spin_sort). */
#include "navigation.h"

#define NO_SLOTS 4
#define NO_MIN 512 /* below this many records the sort is a few ms: nothing kept */
#define NO_FILE "/mnt/mmc/.q2pod/order%d"
#define NO_MAGIC 0x314f4e51u /* "QNO1" */
#define SPIN_AFTER_MS 250    /* a sort still running this long shows the spinner */
#define SPIN_TICK_MS 20
#define SPIN_STEP_MS 80 /* a dot a step, 8 a turn */
#define SPIN_BOX 64     /* px, a #1c1c1e square with 12 px corners at the screen's centre */

/* A slot's head, also its file's: the firmware (Q2POD_VERSION's hash: ringnav_sort_key is in the
 * comparators), the query key, the count, the records' hash and the kept ids' own hash. */
typedef struct {
    unsigned magic, version, key, n;
    unsigned long long sum;
    unsigned idsum, pad;
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
        h = sort == 0x102 ? fnv64(h, P(r, REC_PATH)) : fnv64(fnv64(h, P(r, REC_TITLE)), P(r, REC_NAME));
    }
    return h;
}
/* The query's text, its sort argument and which comparator pair stock picks (NAME_SORT_FLAG). */
static unsigned query_key(const char *sql, int sort) {
    unsigned h = 0x811c9dc5u ^ (unsigned)sort ^ (unsigned)B(NAME_SORT_FLAG, 0) << 16;
    while (*sql) h = (h ^ (unsigned char)*sql++) * 0x01000193u;
    return h;
}
/* library_query's sort: toolsQueryDbTable's argument, or 0x100 and toolsQueryDbTableAndSort's mode. */
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
static no_slot *find(unsigned key, unsigned n, unsigned long long sum) {
    for (int i = 0; i < NO_SLOTS && !no.read; ++i) { /* each slot's head as the last boot left it */
        char s[32];
        no_head *h = &no.s[i].h;
        void *f = fopen(path(s, i), "rb");
        if (f && (fread(h, sizeof *h, 1, f) != 1 || h->magic != NO_MAGIC || h->version != version()))
            memset(h, 0, sizeof *h);
        if (f) fclose(f);
    }
    no.read = 1;
    for (int i = 0; i < NO_SLOTS; ++i) {
        no_slot *k = &no.s[i];
        if (k->h.n == n && k->h.key == key && k->h.sum == sum && (k->file || (k->file = card_file(i))))
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

/* After a full sort: list's order kept under key in that key's slot, else the least recently
 * used, and written to its file (blob_io: a temporary file, synced, renamed over). */
static void keep(unsigned key, unsigned long long sum, void *list) {
    unsigned n = deque_size(list);
    no_slot *k = 0;
    for (int i = 0; i < NO_SLOTS; ++i)
        if (no.s[i].h.key == key && no.s[i].h.n) k = &no.s[i];
    if (!k)
        for (int i = 0; i < NO_SLOTS; ++i)
            if (!k || no.s[i].used < k->used) k = &no.s[i];
    no_head *f = realloc(k->file, sizeof *f + n * 4);
    if (!f) {
        free(k->file), k->file = 0, k->h.n = 0;
        return;
    }
    for (unsigned i = 0; i < n; ++i) IDS(f)[i] = I(deque_at(list, i), REC_ID);
    no_head h = { NO_MAGIC, version(), key, n, sum, 0, 0 };
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

/* The spinner, straight onto the shown framebuffer page (320 x 375, 32 bpp, rotated: a square at
 * the centre is one either way), what it covers saved first and put back after, as no frame is
 * painted meanwhile: the UI thread is waiting here. */
static struct {
    unsigned char *map;
    unsigned len, stride, *at, *under;
} sp __attribute__((section(".scratch")));
static int spin_open(void) {
    unsigned var[40], fix[17];
    int fd = open("/dev/fb0", 2 /* O_RDWR */);
    if (fd < 0) return 0;
    int ok = !ioctl(fd, 0x4600 /* FBIOGET_VSCREENINFO */, var) && !ioctl(fd, 0x4602, fix) &&
             var[0] == 320 && var[1] == 375 && var[6] == 32 && fix[11] >= 1280 &&
             (sp.under = calloc(SPIN_BOX * SPIN_BOX, 4)) &&
             (sp.map = mmap(0, fix[5], 3, 1, fd, 0)) != (void *)-1 && sp.map;
    close(fd);
    if (!ok) return free(sp.under), sp.map = 0, 0;
    sp.len = fix[5], sp.stride = fix[11] / 4;
    sp.at = (unsigned *)sp.map + (var[5] + 187 - SPIN_BOX / 2) * sp.stride + 160 - SPIN_BOX / 2;
    for (int y = 0; y < SPIN_BOX; ++y)
        memcpy(sp.under + y * SPIN_BOX, sp.at + y * sp.stride, SPIN_BOX * 4);
    return 1;
}
/* A coverage 0-255 from a squared distance (half pixels) inside r2 - w and out by r2. */
static int cover(int d2, int r2, int w) { return d2 >= r2 ? 0 : d2 <= r2 - w ? 255 : (r2 - d2) * 255 / w; }
/* Frame f: eight dots 15 px out, the lit one at dot f & 7 and each before it dimmer. */
static void spin_paint(int f) {
    static const signed char dots[8][2] = { { 0, -15 }, { 11, -11 }, { 15, 0 }, { 11, 11 },
                                            { 0, 15 },  { -11, 11 }, { -15, 0 }, { -11, -11 } };
    static const unsigned char lit[8] = { 255, 200, 160, 128, 100, 80, 64, 56 };
    for (int y = 0; y < SPIN_BOX; ++y)
        for (int x = 0; x < SPIN_BOX; ++x) {
            int u = 2 * x + 1 - SPIN_BOX, v = 2 * y + 1 - SPIN_BOX; /* half pixels from the centre */
            int ex = (u < 0 ? -u : u) - 40, ey = (v < 0 ? -v : v) - 40, box, ink = 0, gray = 0;
            ex = ex < 0 ? 0 : ex, ey = ey < 0 ? 0 : ey;
            if (!(box = cover(ex * ex + ey * ey, 625, 96))) continue;
            for (int k = 0; k < 8; ++k) {
                int du = u - 2 * dots[k][0], dv = v - 2 * dots[k][1], a = cover(du * du + dv * dv, 49, 24);
                if (a > ink) ink = a, gray = lit[(f - k) & 7];
            }
            unsigned under = sp.under[y * SPIN_BOX + x], out = under & 0xff000000u;
            for (int c = 0; c < 24; c += 8) {
                int bg = (c ? 0x1c : 0x1e) + (gray - (c ? 0x1c : 0x1e)) * ink / 255, was = under >> c & 255;
                out |= (unsigned)(was + (bg - was) * box / 255) << c;
            }
            sp.at[y * sp.stride + x] = out;
        }
}
static void *sorter(void *arg) {
    cstl_sort(no.list, no.cmp);
    no.done = 1;
    return arg;
}
/* list sorted on a thread of its own, the spinner shown from SPIN_AFTER_MS until it is done; in
 * place when no thread starts (a started one has a nonzero id). */
static void spin_sort(void *list, void *cmp) {
    unsigned long t = 0;
    no.list = list, no.cmp = cmp, no.done = 0;
    if (pthread_create(&t, 0, sorter, 0) || !t) {
        cstl_sort(list, cmp);
        return;
    }
    for (int ms = 0, shown = 0; !no.done; ms += SPIN_TICK_MS) {
        usleep(SPIN_TICK_MS * 1000);
        if (ms >= SPIN_AFTER_MS && (shown || (shown = spin_open()))) spin_paint(ms / SPIN_STEP_MS);
    }
    pthread_join(t, 0);
    if (sp.map) {
        for (int y = 0; y < SPIN_BOX; ++y)
            memcpy(sp.at + y * sp.stride, sp.under + y * SPIN_BOX, SPIN_BOX * 4);
        munmap(sp.map, sp.len), free(sp.under), sp.map = 0;
    }
}

/* list, sql's records unsorted as library_query loaded them, in the order stock's sort gives
 * them; sort is library_query's (comparator). */
void name_order(const char *sql, int sort, void *list) {
    unsigned n = deque_size(list);
    void *cmp = comparator(sort);
    if (n < NO_MIN) {
        if (n > 1) cstl_sort(list, cmp);
        return;
    }
    unsigned key = query_key(sql, sort);
    unsigned long long sum = list_hash(list, sort);
    no_slot *k = find(key, n, sum);
    if (k && apply(k, list)) return;
    spin_sort(list, cmp);
    keep(key, sum, list);
}
