/* q2radiofetch: the stations that reach a place and their streams
 * (docs/internals.md#local-radio-fetch).
 *
 *   q2radiofetch [--ip | --zip NNNNN | --at LAT,LON] [--refresh] [--deadline MS] [--cache DIR]
 *                [--data DIR] [--ca FILE] [--out FILE] [-v]
 *
 * prints the list (radiofetch_core.c rf_text) on stdout, or with --out writes it to FILE whole
 * (beside it, then renamed), as Radio reads it (radio_tuner.c). The place: the network's
 * public address, raced over three geolocation services (--ip, the default), a ZIP looked up in the
 * bundled Census table, or a point. The stations come from the bundled FCC table, offline; only
 * their streams come from the web, on one libcurl multi handle under one deadline: iHeart's
 * stations near the point, Radio Browser's for the stations' states and TuneIn's local list at
 * once, then probes by call sign (Triton, Amperwave, SoundStack, TuneIn's Tune) for the stations
 * still without a stream. What came by the deadline is the list; one cut short says partial.
 *
 * Each list is cached on the card under its place (rf_key). A fresh one (RF_FRESH_S, RF_PARTIAL_S
 * for a partial one) is printed and that is all; an older one is printed at once, stdout is closed
 * (the reader sees its end) and the list is fetched again into the cache, under a lock so two runs
 * don't, and into --out's FILE too, where the reader takes it up when this ends. Exit 0 with a
 * list, 1 with none, 2 for a wrong argument. */
#include "radiofetch.h"

#define DATA_DIR "/usr/share/q2radio"
#define CACHE_DIR "/mnt/mmc/Radio/.local"
#define CA_FILE                                                                                    \
    "/etc/scrobble-ca.pem" /* every host here verifies against it                                  \
                              (docs/internals.md#local-radio-fetch) */
#define UA "Mozilla/5.0"   /* a generic one: nothing that names the device or the firmware */
#define DEADLINE_MS 8000   /* the whole run, from start to the list */
#define LOCATE_MS 3000     /* the geolocation race's share of it */
#define PHASE_MS                                                                                   \
    3500 /* the probes start when the first round is in, or this long after it began... */
#define PROBE_MS                                                                                   \
    2000 /* ...or this long before the deadline, whichever is sooner: their time to answer */
#define REQ_MS 3000 /* one request, connection included */
#define BIG_MS 4500 /* a list request (iHeart, Radio Browser), which can be 200 KB */
#define BIG_MAX                                                                                    \
    (768u << 10) /* the most one answer may hold, decoded: a big state's Radio Browser list */
#define SMALL_MAX (64u << 10)   /* a probe's answer (SoundStack's page is about 34 KB) */
#define PARALLEL 16             /* transfers at once */
#define PER_HOST 4              /* connections to one host, reused by its later requests */
#define RF_FRESH_S (7 * 86400L) /* a complete list is fetched again after a week */
#define RF_PARTIAL_S 3600L      /* a partial one after an hour */
#define TEXT_MAX (96u << 10)

/* ---- libcurl, as the rootfs's libcurl.so.4 exports it (no headers on this side: the option
 * numbers are curl.h's, stable since they were added) ---- */
void *curl_easy_init(void);
int curl_easy_setopt(void *, int, ...), curl_easy_getinfo(void *, int, ...), curl_global_init(long);
void curl_easy_cleanup(void *);
void *curl_multi_init(void);
int curl_multi_add_handle(void *, void *), curl_multi_remove_handle(void *, void *),
    curl_multi_cleanup(void *);
int curl_multi_perform(void *, int *), curl_multi_poll(void *, void *, unsigned, int, int *);
int curl_multi_setopt(void *, int, ...);
typedef struct {
    int msg;
    void *easy;
    union {
        void *whatever;
        int result;
    } data;
} curl_msg;
curl_msg *curl_multi_info_read(void *, int *);
#define URL 10002
#define WRITEFUNCTION 20011
#define WRITEDATA 10001
#define PRIVATE 10103
#define USERAGENT 10018
#define CAINFO 10065
#define SSL_VERIFYPEER 64
#define SSL_VERIFYHOST 81
#define NOSIGNAL 99
#define FOLLOWLOCATION 52
#define MAXREDIRS 68
#define TIMEOUT_MS 155
#define CONNECTTIMEOUT_MS 156
#define ACCEPT_ENCODING 10102
#define PROTOCOLS_STR 10318
#define RESPONSE_CODE 0x200002
#define M_MAXCONNECTS 6
#define M_MAX_HOST_CONNECTIONS 7
#define M_MAX_TOTAL_CONNECTIONS 13
#define MSG_DONE 1

enum { GEO, IHEART, RADIOBROWSER, TUNEIN, TUNE, TRITON, AMPERWAVE, SOUNDSTACK };
static const char *const kind_name[] = { "geo",  "iheart", "radiobrowser", "tunein",
                                         "tune", "triton", "amperwave",    "soundstack" };

typedef struct xfer {
    void *easy;
    int kind, station, aac;
    char url[RF_URL], mount[16];
    char *body;
    unsigned n, max;
    long started;
    struct xfer *next;
} xfer;

static struct {
    void *multi;
    xfer *queue, *queue_tail, *running[PARALLEL];
    int active, verbose, requests;
    long t0;
    const char *ca, *base; /* base: Q2RF_TEST_BASE: every host is a path under it
                              (test/unit/test_radiofetch.py) */
} net;

static long now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1000L + t.tv_nsec / 1000000;
}

static void say(const char *s) { write(2, s, strlen(s)); }

static unsigned got(char *data, unsigned size, unsigned count, void *p) {
    xfer *x = p;
    unsigned n = size * count;
    if (x->n + n > x->max)
        return 0; /* past its cap: the transfer stops, and what came is read as far as it goes */
    if (!x->body && !(x->body = malloc(x->max + 1)))
        return 0; /* one allocation, at its cap, on the first data */
    memcpy(x->body + x->n, data, n);
    x->n += n;
    x->body[x->n] = 0;
    return n;
}

/* A transfer of url ("host/path...", https unless net.base) queued; started as slots free. */
static xfer *fetch(int kind, int station, const char *host_path, unsigned max) {
    xfer *x = calloc(1, sizeof *x);
    if (!x) return 0;
    x->kind = kind, x->station = station, x->max = max;
    if (net.base)
        snprintf(x->url, sizeof x->url, "%s/%s", net.base, host_path);
    else
        snprintf(x->url, sizeof x->url, "https://%s", host_path);
    if (net.queue_tail)
        net.queue_tail->next = x;
    else
        net.queue = x;
    net.queue_tail = x;
    return x;
}

static void start_queued(void) {
    while (net.queue && net.active < PARALLEL) {
        xfer *x = net.queue;
        net.queue = x->next;
        if (!net.queue) net.queue_tail = 0;
        x->next = 0;
        void *c = x->easy = curl_easy_init();
        if (!c) {
            free(x);
            continue;
        }
        curl_easy_setopt(c, URL, x->url);
        curl_easy_setopt(c, WRITEFUNCTION, got);
        curl_easy_setopt(c, WRITEDATA, x);
        curl_easy_setopt(c, PRIVATE, x);
        curl_easy_setopt(c, USERAGENT, UA);
        curl_easy_setopt(c, CAINFO, net.ca);
        curl_easy_setopt(c, SSL_VERIFYPEER, 1L);
        curl_easy_setopt(c, SSL_VERIFYHOST, 2L);
        curl_easy_setopt(c, NOSIGNAL, 1L);
        curl_easy_setopt(c, FOLLOWLOCATION, 1L);
        curl_easy_setopt(c, MAXREDIRS, 3L);
        curl_easy_setopt(c, PROTOCOLS_STR, "http,https");
        curl_easy_setopt(c, ACCEPT_ENCODING,
                         ""); /* gzip: iHeart's 120 KB list is about 15 KB on the wire */
        curl_easy_setopt(c, CONNECTTIMEOUT_MS, (long)REQ_MS);
        curl_easy_setopt(c, TIMEOUT_MS, (long)(x->max > SMALL_MAX ? BIG_MS : REQ_MS));
        x->started = now_ms();
        if (curl_multi_add_handle(net.multi, c)) {
            curl_easy_cleanup(c);
            free(x);
            continue;
        }
        net.running[net.active++] = x, ++net.requests;
    }
}

static void drop(xfer *x) {
    for (int i = 0; i < net.active; ++i)
        if (net.running[i] == x) net.running[i] = net.running[--net.active];
    curl_multi_remove_handle(net.multi, x->easy);
    curl_easy_cleanup(x->easy);
    free(x->body);
    free(x);
}

/* Run transfers until done(ctx) or the deadline (an absolute now_ms); each finished one goes to
 * finished(ctx, x, code). With last, what is still running or queued at the end is stopped and
 * dropped; else it goes on in the next run. */
static void run(long deadline, int last, int (*done)(void *),
                void (*finished)(void *, xfer *, long), void *ctx) {
    for (;;) {
        if (now_ms() < deadline) start_queued();
        int running = 0, left;
        curl_multi_perform(net.multi, &running);
        curl_msg *m;
        while ((m = curl_multi_info_read(net.multi, &left))) {
            if (m->msg != MSG_DONE) continue;
            xfer *x = 0;
            long code = 0;
            curl_easy_getinfo(m->easy, 0x100015, &x); /* CURLINFO_PRIVATE */
            if (!m->data.result) curl_easy_getinfo(m->easy, RESPONSE_CODE, &code);
            if (net.verbose) {
                char line[RF_URL + 64];
                snprintf(line, sizeof line, "%6ld ms  %4ld ms  %3ld %6u B  %s %s\n",
                         now_ms() - net.t0, now_ms() - x->started, code, x->n, kind_name[x->kind],
                         x->url);
                say(line);
            }
            if (!x->body && !(x->body = calloc(1, 1))) code = 0;
            finished(ctx, x, code);
            drop(x);
        }
        if (done(ctx) || (!net.active && !net.queue)) break;
        long wait = deadline - now_ms();
        if (wait <= 0) break;
        curl_multi_poll(net.multi, 0, 0, wait < 100 ? (int)wait : 100, 0);
    }
    if (!last) return;
    while (net.active) drop(net.running[0]);
    while (net.queue) {
        xfer *x = net.queue;
        net.queue = x->next;
        free(x);
    }
    net.queue_tail = 0;
}

/* ---- the place ---- */

typedef struct {
    double lat, lon;
    int have;
    char from[48];
} geo_ctx;

static int geo_done(void *p) { return ((geo_ctx *)p)->have; }

static void geo_finished(void *p, xfer *x, long code) {
    geo_ctx *g = p;
    if (g->have || code != 200) return;
    if (rf_geo(x->body, &g->lat, &g->lon)) {
        g->have = 1;
        const char *h = strstr(x->url, "//");
        h = h ? h + 2 : x->url;
        unsigned n = 0;
        while (h[n] && h[n] != '/' && n + 1 < sizeof g->from) g->from[n] = h[n], ++n;
        g->from[n] = 0;
    }
}

/* The network's place from the first of three services to answer with one; 1 if one did. */
static int locate(double *lat, double *lon, long deadline) {
    static const char *const services[] = { "ipinfo.io/json", "ipwho.is/",
                                            "get.geojs.io/v1/ip/geo.json" };
    geo_ctx g = { 0, 0, 0, "" };
    for (unsigned i = 0; i < 3; ++i) fetch(GEO, -1, services[i], SMALL_MAX);
    run(deadline, 1, geo_done, geo_finished, &g); /* the race's losers are dropped */
    if (g.have) *lat = g.lat, *lon = g.lon;
    if (net.verbose && g.have) {
        char line[96];
        snprintf(line, sizeof line, "located by %s in %ld ms\n", g.from, now_ms() - net.t0);
        say(line);
    }
    return g.have;
}

/* ---- the streams ---- */

typedef struct {
    rf_list *l;
    int first_left; /* first-round transfers still out */
} fetch_ctx;

static int fetch_done(void *p) { return 0; }

static void url_escape(char *out, unsigned cap, const char *s) {
    unsigned n = 0;
    for (; *s && n + 4 < cap; ++s) {
        int c = (unsigned char)*s;
        if ((c | 32) >= 'a' && (c | 32) <= 'z')
            out[n++] = c;
        else
            n += snprintf(out + n, cap - n, "%%%02X", c);
    }
    out[n] = 0;
}

static void first_finished(void *p, xfer *x, long code) {
    fetch_ctx *f = p;
    if (x->kind == IHEART || x->kind == RADIOBROWSER || x->kind == TUNEIN) --f->first_left;
    if (code != 200) return;
    if (x->kind == IHEART) rf_iheart(f->l, x->body, x->n);
    if (x->kind == RADIOBROWSER) rf_radiobrowser(f->l, x->body, x->n);
    if (x->kind == TUNEIN) rf_tunein_local(f->l, x->body, x->n);
}

static int first_done(void *p) { return ((fetch_ctx *)p)->first_left <= 0; }

static void probe_finished(void *p, xfer *x, long code) {
    fetch_ctx *f = p;
    rf_station *st = &f->l->st[x->station];
    if (x->kind == TRITON) {
        if (code == 200 && rf_triton(st, x->mount, x->body)) return;
        if (x->aac) { /* no AAC mount: the MP3 one */
            char path[96], mount[16];
            xfer *y;
            rf_mount(st, mount, 0);
            snprintf(path, sizeof path,
                     "playerservices.streamtheworld.com/api/livestream?version=1.9&mount=%s",
                     mount);
            if ((y = fetch(TRITON, x->station, path, SMALL_MAX)))
                memcpy(y->mount, mount, sizeof mount);
        }
    } else if (x->kind == AMPERWAVE) {
        if (code == 200 && (!strncmp(x->body, "http", 4) ||
                            !strncmp(x->body, "#EXTM3U", 7))) { /* a playlist: the mount is live */
            char url[RF_URL], call[8];
            unsigned i = 0;
            for (; st->base[i]; ++i) call[i] = st->base[i] | 32;
            call[i] = 0;
            snprintf(url, sizeof url, "https://live.amperwave.net/direct/audacy-%s%saac-imc", call,
                     st->flags & RF_AM ? "am" : "fm");
            rf_add(st, "amperwave", url);
        }
    } else if (x->kind == SOUNDSTACK) {
        if (code == 200) rf_soundstack(st, x->body);
    } else if (x->kind == TUNE) {
        if (code == 200) rf_tunein_tune(st, x->body, x->n);
    } else {
        first_finished(p, x, code); /* a first-round answer that came after the probes began */
    }
}

/* The streams for l's stations, until the deadline; 1 if every transfer finished in time. */
static int streams(rf_list *l, long deadline) {
    fetch_ctx f = { l, 0 };
    char path[RF_URL];
    snprintf(path, sizeof path,
             "api.iheart.com/api/v2/content/liveStations?lat=%.4f&lng=%.4f&limit=100", l->lat,
             l->lon);
    f.first_left += !!fetch(IHEART, -1, path, BIG_MAX);
    const char *states[3];
    int ns = rf_states(l, states, 3);
    for (int i = 0; i < ns; ++i) {
        char name[64];
        url_escape(name, sizeof name, states[i]);
        snprintf(path, sizeof path,
                 "all.api.radio-browser.info/json/stations/"
                 "search?countrycode=US&state=%s&hidebroken=true&limit=1500",
                 name);
        f.first_left += !!fetch(RADIOBROWSER, -1, path, BIG_MAX);
    }
    snprintf(path, sizeof path,
             "opml.radiotime.com/Browse.ashx?c=local&render=json&latlon=%.4f,%.4f", l->lat, l->lon);
    f.first_left += !!fetch(TUNEIN, -1, path, BIG_MAX);
    long start = now_ms(), phase = start + PHASE_MS;
    if (phase > deadline - PROBE_MS)
        phase = deadline - PROBE_MS > start ? deadline - PROBE_MS : start;
    run(phase, 0, first_done, first_finished, &f);

    for (int i = 0; i < l->n; ++i) { /* the probes, for the stations still without a stream */
        rf_station *st = &l->st[i];
        if (st->n) continue;
        char mount[16];
        rf_mount(st, mount, 1);
        snprintf(path, sizeof path,
                 "playerservices.streamtheworld.com/api/livestream?version=1.9&mount=%s", mount);
        xfer *x = fetch(TRITON, i, path, SMALL_MAX);
        if (x) memcpy(x->mount, mount, sizeof mount), x->aac = 1;
        if (st->flags & RF_AUDACY) {
            char call[8];
            unsigned k = 0;
            for (; st->base[k]; ++k) call[k] = st->base[k] | 32;
            call[k] = 0;
            snprintf(path, sizeof path, "live.amperwave.net/playlist/audacy-%s%saac-imc.m3u", call,
                     st->flags & RF_AM ? "am" : "fm");
            fetch(AMPERWAVE, i, path, SMALL_MAX);
        }
        snprintf(path, sizeof path, "live.mystreamplayer.com/%s%s", st->base,
                 st->flags & RF_AM ? "AM" : "");
        fetch(SOUNDSTACK, i, path, SMALL_MAX);
        if (st->tunein[0]) {
            snprintf(path, sizeof path,
                     "opml.radiotime.com/Tune.ashx?id=%s&render=json&formats=mp3,aac", st->tunein);
            fetch(TUNE, i, path, SMALL_MAX);
        }
    }
    run(deadline, 0, fetch_done, probe_finished, &f);
    int whole = !net.active && !net.queue;
    run(0, 1, fetch_done, probe_finished, &f);
    return whole;
}

/* ---- files ---- */

static char *slurp(const char *path, unsigned max) {
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return 0;
    char *b = malloc(max + 1);
    unsigned n = 0;
    ssize_t r;
    while (b && n < max && (r = read(fd, b + n, max - n)) > 0) n += r;
    close(fd);
    if (b) b[n] = 0;
    return b;
}

static void put_all(int fd, const char *s, unsigned n) {
    while (n) {
        ssize_t w = write(fd, s, n);
        if (w <= 0) return;
        s += w, n -= w;
    }
}

/* text as path, whole or not at all: written beside it, synced, then renamed over it. 1 when it is
 * there. */
static int save(const char *path, const char *text, unsigned n) {
    char tmp[300];
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) return 0;
    put_all(fd, text, n);
    int ok = !fsync(fd);
    close(fd);
    if (!ok || rename(tmp, path)) {
        unlink(tmp);
        return 0;
    }
    return 1;
}

static void mkdirs(const char *dir) {
    char p[256];
    snprintf(p, sizeof p, "%s", dir);
    for (char *s = p + 1; *s; ++s)
        if (*s == '/') *s = 0, mkdir(p, 0755), *s = '/';
    mkdir(p, 0755);
}

static int usage(void) {
    say("usage: q2radiofetch [--ip | --zip NNNNN | --at LAT,LON] [--refresh] [--deadline MS] "
        "[--cache DIR] "
        "[--data DIR] [--ca FILE] [--out FILE] [-v]\n");
    return 2;
}

int main(int argc, char **argv) {
    const char *zip = 0, *data = DATA_DIR, *cache = CACHE_DIR, *at = 0, *out = 0;
    int refresh = 0;
    long budget = DEADLINE_MS;
    net.t0 = now_ms(), net.ca = CA_FILE;
    for (int i = 1; i < argc; ++i) {
        const char *a = argv[i], *v = i + 1 < argc ? argv[i + 1] : 0;
        if (!strcmp(a, "--ip"))
            zip = at = 0;
        else if (!strcmp(a, "--zip") && v)
            zip = v, ++i;
        else if (!strcmp(a, "--at") && v)
            at = v, ++i;
        else if (!strcmp(a, "--refresh"))
            refresh = 1;
        else if (!strcmp(a, "--deadline") && v)
            budget = strtol(v, 0, 10), ++i;
        else if (!strcmp(a, "--cache") && v)
            cache = v, ++i;
        else if (!strcmp(a, "--data") && v)
            data = v, ++i;
        else if (!strcmp(a, "--out") && v)
            out = v, ++i;
        else if (!strcmp(a, "--ca") && v)
            net.ca = v, ++i;
        else if (!strcmp(a, "-v"))
            net.verbose = 1;
        else
            return usage();
    }
    long deadline = net.t0 + budget;
    net.base = getenv("Q2RF_TEST_BASE");
    char path[256], label[64] = "", key[32];
    double lat = 0, lon = 0;

    if (zip) {
        snprintf(path, sizeof path, "%s/zcta.bin", data);
        int z = rf_zip(path, zip, &lat, &lon);
        if (z <= 0) {
            say(z < 0 ? "q2radiofetch: no ZIP table\n" : "q2radiofetch: not a US ZIP Code\n");
            return z < 0 ? 1 : 2;
        }
        snprintf(label, sizeof label, "ZIP %.5s%s", zip, z == 2 ? " (nearby)" : "");
        rf_key(key, sizeof key, zip, 0, 0);
    } else if (at) {
        char *e;
        lat = strtod(at, &e);
        if (*e != ',') return usage();
        lon = strtod(e + 1, 0);
        snprintf(label, sizeof label, "%.4f,%.4f", lat, lon);
        rf_key(key, sizeof key, 0, lat, lon);
    }

    curl_global_init(3); /* CURL_GLOBAL_DEFAULT */
    net.multi = curl_multi_init();
    if (!net.multi) return 1;
    curl_multi_setopt(net.multi, M_MAX_TOTAL_CONNECTIONS, (long)PARALLEL);
    curl_multi_setopt(net.multi, M_MAX_HOST_CONNECTIONS, (long)PER_HOST);
    curl_multi_setopt(net.multi, M_MAXCONNECTS, (long)PARALLEL);

    char last[256];
    snprintf(last, sizeof last, "%s/last", cache);
    if (!zip && !at) {
        long locate_by = net.t0 + LOCATE_MS < deadline ? net.t0 + LOCATE_MS : deadline;
        if (locate(&lat, &lon, locate_by)) {
            snprintf(label, sizeof label, "near %.2f,%.2f", lat, lon);
            rf_key(key, sizeof key, 0, lat, lon);
        } else { /* offline, or no service answered: the last place's list */
            char *k = slurp(last, sizeof key - 1);
            if (!k || !k[0]) {
                say("q2radiofetch: no location\n");
                return 1;
            }
            snprintf(key, sizeof key, "%s", k);
            free(k);
            snprintf(label, sizeof label, "last place");
            lat = 999; /* no point: only the cache can answer */
        }
    }

    mkdirs(cache);
    char file[300];
    snprintf(file, sizeof file, "%s/%s.tsv", cache, key);
    char *old = refresh ? 0 : slurp(file, TEXT_MAX);
    long fetched = 0, wall = time(0);
    int complete = 0, have_old = old && rf_header(old, &fetched, &complete);
    if (have_old) {
        save(last, key, strlen(key));
        if (out)
            save(out, old, strlen(old));
        else
            put_all(1, old, strlen(old));
        long age = wall - fetched;
        if (lat == 999 || (age >= 0 && age < (complete ? RF_FRESH_S : RF_PARTIAL_S))) return 0;
        int devnull = open("/dev/null", O_WRONLY);
        if (devnull >= 0)
            dup2(devnull, 1),
                close(devnull); /* the reader has its list; the fetch goes on unseen */
    }
    free(old);
    if (lat == 999) {
        say("q2radiofetch: no list for the last place\n");
        return 1;
    }
    snprintf(path, sizeof path, "%s/.lock", cache);
    int lock = open(path, O_WRONLY | O_CREAT | O_CLOEXEC, 0644);
    if (have_old && (lock < 0 || flock(lock, LOCK_EX | LOCK_NB)))
        return 0; /* another run is refreshing it */

    rf_list *l = calloc(1, sizeof *l);
    char *text = malloc(TEXT_MAX);
    if (!l || !text) return 1;
    snprintf(path, sizeof path, "%s/stations.bin", data);
    if (rf_near(path, lat, lon, l) < 0) {
        say("q2radiofetch: no station table\n");
        return 1;
    }
    int whole = streams(l, deadline);
    int n = rf_text(l, text, TEXT_MAX, time(0), whole, label);
    int streaming = 0;
    for (int i = 0; i < l->n; ++i) streaming += l->st[i].n > 0;
    if (n && (streaming || !have_old) && out) save(out, text, n); /* a refresh's newer list too */
    if (!have_old && !out) put_all(1, text, n);
    if (n && (streaming || !have_old) && save(file, text, n)) save(last, key, strlen(key));
    if (net.verbose) {
        char line[160];
        snprintf(line, sizeof line, "%d stations, %d with a stream, %d requests, %s, %ld ms\n",
                 l->n, streaming, net.requests, whole ? "complete" : "partial", now_ms() - net.t0);
        say(line);
    }
    return n ? 0 : 1;
}
