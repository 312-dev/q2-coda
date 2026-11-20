/* q2radiofetch's work that needs no network (docs/internals.md#local-radio-fetch): the bundled tables, the
 * readers of what each source answers, matching an answer to a station, and the list as text. */
#include "radiofetch.h"

static int up(int c) { return c >= 'a' && c <= 'z' ? c - 32 : c; }
static int alpha(int c) { return (c | 32) >= 'a' && (c | 32) <= 'z'; }
static int digit(int c) { return c >= '0' && c <= '9'; }
static int alnum(int c) { return alpha(c) || digit(c); }

/* Copy s into out (cap bytes), cut, NUL-terminated. */
static void put(char *out, unsigned cap, const char *s) {
    unsigned i = 0;
    while (s[i] && i + 1 < cap) out[i] = s[i], ++i;
    out[i] = 0;
}

/* ---- the tables ---- */

/* Great-circle km between two points, and the bearing from the first to the second in degrees (0 north,
 * clockwise) in *bearing if it is not NULL. */
double rf_km(double lat1, double lon1, double lat2, double lon2, double *bearing) {
    const double r = 3.14159265358979 / 180;
    double p1 = lat1 * r, p2 = lat2 * r, dl = (lon2 - lon1) * r;
    double s1 = sin((p2 - p1) / 2), s2 = sin(dl / 2);
    double a = s1 * s1 + cos(p1) * cos(p2) * s2 * s2;
    if (bearing) {
        double b = atan2(sin(dl) * cos(p2), cos(p1) * sin(p2) - sin(p1) * cos(p2) * cos(dl)) / r;
        *bearing = b < 0 ? b + 360 : b;
    }
    return 2 * 6371.0 * asin(sqrt(a > 1 ? 1 : a));
}

static unsigned u16(const unsigned char *p) { return p[0] | p[1] << 8; }
static unsigned u32(const unsigned char *p) { return p[0] | p[1] << 8 | p[2] << 16 | (unsigned)p[3] << 24; }

/* n bytes of fd at off into buf; 1 if all of them came. */
static int pread_all(int fd, long off, void *buf, unsigned n) {
    if (lseek(fd, off, SEEK_SET) != off) return 0;
    unsigned got = 0;
    while (got < n) {
        ssize_t r = read(fd, (char *)buf + got, n - got);
        if (r <= 0) return 0;
        got += r;
    }
    return 1;
}

/* The station at rec if its reach covers (lat, lon), into a free or the farthest slot of out. The reach
 * toward the place is the two nearest of RF_RADII radii, weighted by the angle between them. */
static void consider(int fd, unsigned pool, const unsigned char *rec, double lat, double lon, rf_list *out) {
    double slat = (int)u32(rec + 12) / 1e5, slon = (int)u32(rec + 16) / 1e5, bearing;
    double km = rf_km(slat, slon, lat, lon, &bearing);
    double k = bearing * RF_RADII / 360;
    int i = (int)k % RF_RADII;
    double f = k - (int)k, reach = rec[24 + i] * (1 - f) + rec[24 + (i + 1) % RF_RADII] * f;
    if (km > reach) return;
    rf_station *st = out->n < RF_STATIONS ? &out->st[out->n++] : 0;
    if (!st) { /* full: this one replaces the farthest, if it is nearer */
        st = &out->st[0];
        for (int j = 1; j < RF_STATIONS; ++j)
            if (out->st[j].km > st->km) st = &out->st[j];
        if (st->km <= km) return;
    }
    memset(st, 0, sizeof *st);
    memcpy(st->call, rec, 7);
    unsigned b = 0;
    while (b < 5 && st->call[b] && st->call[b] != '-') st->base[b] = st->call[b], ++b;
    st->freq = u16(rec + 8);
    st->flags = rec[10];
    st->km = km;
    char city[RF_CITY];
    if (!pread_all(fd, pool + u32(rec + 20), city, sizeof city)) city[0] = 0;
    city[sizeof city - 1] = 0;
    put(st->city, sizeof st->city, city);
}

static int by_band_freq(const rf_station *a, const rf_station *b) {
    int am = (a->flags & RF_AM) - (b->flags & RF_AM);
    return am ? am : (int)a->freq - (int)b->freq;
}

/* The stations whose reach covers (lat, lon), from stations.bin at table, into out: FM then AM, each by
 * frequency. The table is sorted by latitude and no reach passes 255 km, so only the records within 2.4
 * degrees of latitude are read, a block at a time. Their count, or -1 if the table can't be read. */
int rf_near(const char *table, double lat, double lon, rf_list *out) {
    unsigned char head[16], buf[RF_RECORD * 64];
    int fd = open(table, O_RDONLY | O_CLOEXEC);
    out->n = 0, out->lat = lat, out->lon = lon;
    if (fd < 0) return -1;
    if (!pread_all(fd, 0, head, 16) || memcmp(head, RF_MAGIC, 4) || head[4] != 1) {
        close(fd);
        return -1;
    }
    unsigned count = u32(head + 8), pool = u32(head + 12), lo = 0, hi = count;
    int floor = (int)((lat - 2.4) * 1e5);
    while (lo < hi) { /* the first record at or north of floor */
        unsigned mid = (lo + hi) / 2;
        unsigned char rec[RF_RECORD];
        if (!pread_all(fd, 16 + (long)mid * RF_RECORD, rec, RF_RECORD)) break;
        if ((int)u32(rec + 12) < floor)
            lo = mid + 1;
        else
            hi = mid;
    }
    int ceil = (int)((lat + 2.4) * 1e5), done = 0;
    for (unsigned at = lo; at < count && !done; at += 64) {
        unsigned n = count - at < 64 ? count - at : 64;
        if (!pread_all(fd, 16 + (long)at * RF_RECORD, buf, n * RF_RECORD)) break;
        for (unsigned i = 0; i < n && !done; ++i) {
            const unsigned char *rec = buf + i * RF_RECORD;
            if ((int)u32(rec + 12) > ceil)
                done = 1;
            else
                consider(fd, pool, rec, lat, lon, out);
        }
    }
    close(fd);
    for (int i = 1; i < out->n; ++i) /* insertion sort: a few dozen */
        for (int j = i; j > 0 && by_band_freq(&out->st[j], &out->st[j - 1]) < 0; --j) {
            rf_station t = out->st[j];
            out->st[j] = out->st[j - 1], out->st[j - 1] = t;
        }
    return out->n;
}

/* A five-digit ZIP's point from zcta.bin at table. 1 when the Census lists it, 2 when it does not (a PO box or
 * one company's ZIP) and the nearest listed ZIP with its first three digits stands in, 0 for none, -1 if the
 * table can't be read. */
int rf_zip(const char *table, const char *zip, double *lat, double *lon) {
    int z = 0;
    for (int i = 0; i < 5; ++i) {
        if (!digit(zip[i])) return 0;
        z = z * 10 + zip[i] - '0';
    }
    if (zip[5]) return 0;
    unsigned char *bits = malloc(RF_ZIPS / 8), pt[4];
    int fd = open(table, O_RDONLY | O_CLOEXEC), found = -1;
    if (fd >= 0 && bits && pread_all(fd, 0, bits, RF_ZIPS / 8)) {
        int pick = -1, lo = z / 100 * 100;
        for (int d = 0; d < 100 && pick < 0; ++d) /* this ZIP, else the nearest in its three-digit area */
            for (int s = 0; s < 2 && pick < 0; ++s) {
                int c = s ? z + d : z - d;
                if (c >= lo && c < lo + 100 && bits[c / 8] >> (c % 8) & 1) pick = c;
            }
        found = 0;
        if (pick >= 0) {
            unsigned rank = 0;
            for (int i = 0; i < pick / 8; ++i)
                for (unsigned char b = bits[i]; b; b &= b - 1) ++rank;
            for (int i = pick / 8 * 8; i < pick; ++i) rank += bits[i / 8] >> (i % 8) & 1;
            if (pread_all(fd, RF_ZIPS / 8 + rank * 4L, pt, 4)) {
                *lat = (short)u16(pt) / 100.0, *lon = (short)u16(pt + 2) / 100.0;
                found = pick == z ? 1 : 2;
            }
        }
    }
    if (fd >= 0) close(fd);
    free(bits);
    return found;
}

/* ---- JSON ---- */

#define J_DEPTH 24
#define J_VAL 1024

/* The string at *p (just past its opening quote) unescaped into out (cap bytes, cut there), *p left past the
 * closing quote. 0 if the text ends first. */
static int jstring(const char **p, const char *end, char *out, unsigned cap) {
    unsigned n = 0;
    const char *s = *p;
    while (s < end && *s != '"') {
        unsigned c = (unsigned char)*s++;
        if (c == '\\') {
            if (s >= end) return 0;
            c = (unsigned char)*s++;
            if (c == 'n') c = '\n';
            else if (c == 't') c = '\t';
            else if (c == 'r') c = '\r';
            else if (c == 'b' || c == 'f') c = ' ';
            else if (c == 'u') {
                unsigned v = 0;
                for (int i = 0; i < 4; ++i, ++s) {
                    if (s >= end) return 0;
                    int h = *s;
                    v = v << 4 | (digit(h) ? h - '0' : (h | 32) >= 'a' && (h | 32) <= 'f' ? (h | 32) - 'a' + 10 : 0);
                }
                if (v >= 0xd800 && v < 0xdc00 && end - s >= 6 && s[0] == '\\' && s[1] == 'u') {
                    unsigned lo = 0;
                    for (int i = 2; i < 6; ++i) {
                        int h = s[i];
                        lo = lo << 4 | (digit(h) ? h - '0' : (h | 32) - 'a' + 10);
                    }
                    if (lo >= 0xdc00 && lo < 0xe000) v = 0x10000 + ((v - 0xd800) << 10) + lo - 0xdc00, s += 6;
                }
                unsigned char u[4];
                int k = v < 0x80 ? (u[0] = v, 1)
                        : v < 0x800 ? (u[0] = 0xc0 | v >> 6, u[1] = 0x80 | (v & 63), 2)
                        : v < 0x10000 ? (u[0] = 0xe0 | v >> 12, u[1] = 0x80 | (v >> 6 & 63), u[2] = 0x80 | (v & 63), 3)
                                      : (u[0] = 0xf0 | v >> 18, u[1] = 0x80 | (v >> 12 & 63),
                                         u[2] = 0x80 | (v >> 6 & 63), u[3] = 0x80 | (v & 63), 4);
                if (n + k < cap) memcpy(out + n, u, k), n += k;
                continue;
            }
        }
        if (n + 1 < cap) out[n++] = c;
    }
    out[n] = 0;
    if (s >= end) return 0;
    *p = s + 1;
    return 1;
}

/* Walk the JSON text s (n bytes), calling cb for each object and array as it opens and closes and for each
 * scalar. A text cut short (a capped transfer) is walked as far as it goes: what came whole was reported.
 * 1 when the text was whole, 0 when it ended early or broke off. */
int rf_json(const char *s, unsigned n, rf_json_cb cb, void *ctx) {
    const char *p = s, *end = s + n;
    char stack[J_DEPTH], key[64] = "", *val = malloc(J_VAL);
    int depth = 0, want_key = 0, ok = 0;
    if (!val) return 0;
    for (;;) {
        while (p < end && (*p == ' ' || *p == '\n' || *p == '\r' || *p == '\t' || *p == ',')) ++p;
        if (p >= end) {
            ok = depth == 0 && p > s;
            break;
        }
        char c = *p;
        if (want_key && c == '"') {
            ++p;
            if (!jstring(&p, end, key, sizeof key)) break;
            while (p < end && (*p == ' ' || *p == ':')) ++p;
            want_key = 0;
            continue;
        }
        if (c == '{' || c == '[') {
            if (depth == J_DEPTH) break;
            cb(ctx, c == '{' ? RF_J_OBJECT : RF_J_ARRAY, depth, key, "");
            stack[depth++] = c, ++p;
            want_key = c == '{';
            key[0] = 0;
            continue;
        }
        if (c == '}' || c == ']') {
            if (!depth || stack[depth - 1] != (c == '}' ? '{' : '[')) break;
            --depth, ++p;
            cb(ctx, c == '}' ? RF_J_END_OBJECT : RF_J_END_ARRAY, depth, "", "");
            want_key = depth && stack[depth - 1] == '{';
            key[0] = 0;
            if (!depth) {
                ok = 1;
                break;
            }
            continue;
        }
        if (c == '"') {
            ++p;
            if (!jstring(&p, end, val, J_VAL)) break;
        } else {
            unsigned k = 0;
            while (p < end && *p != ',' && *p != '}' && *p != ']' && *p != ' ' && *p != '\n' && *p != '\r')
                if (k + 1 < J_VAL) val[k++] = *p++;
                else ++p;
            if (p >= end) break; /* a number cut by the end may be cut short: not reported */
            val[k] = 0;
        }
        cb(ctx, RF_J_VALUE, depth, key, val);
        want_key = depth && stack[depth - 1] == '{';
        key[0] = 0;
    }
    free(val);
    return ok;
}

/* ---- matching ---- */

/* The station of the list with this base call sign (WXRT, no -FM) on this band, or NULL. */
rf_station *rf_find(rf_list *l, const char *base, int am) {
    for (int i = 0; i < l->n; ++i)
        if (!strcmp(l->st[i].base, base) && !(l->st[i].flags & RF_AM) == !am) return &l->st[i];
    return 0;
}

/* url as one of st's streams from src, unless it is there already or st has RF_STREAMS; 1 if added. Only
 * http and https URLs, which the player opens. */
int rf_add(rf_station *st, const char *src, const char *url) {
    if (strncmp(url, "https://", 8) && strncmp(url, "http://", 7)) return 0;
    if (strlen(url) >= RF_URL || st->n >= RF_STREAMS) return 0;
    for (int i = 0; i < st->n; ++i)
        if (!strcmp(st->s[i].url, url)) return 0;
    put(st->s[st->n].src, sizeof st->s[0].src, src);
    put(st->s[st->n].url, RF_URL, url);
    ++st->n;
    return 1;
}

/* A display name, kept from the first source that gives one. */
static void name(rf_station *st, const char *s) {
    if (!st->name[0] && s[0]) put(st->name, sizeof st->name, s);
}

/* ---- iHeart: api.iheart.com's liveStations near a point, a market's stations whatever their owner ---- */

typedef struct {
    rf_list *l;
    int found;
    char call[16], name[RF_NAME], url[4][RF_URL]; /* secure_shoutcast, shoutcast, secure_hls, hls */
} ih_ctx;

static void ih_cb(void *c, int ev, int depth, const char *key, const char *val) {
    ih_ctx *x = c;
    static const char *const kinds[4] = {"secure_shoutcast_stream", "shoutcast_stream", "secure_hls_stream",
                                         "hls_stream"};
    if (ev == RF_J_OBJECT && depth == 2) { /* {"hits": [ {one station}, ... ]} */
        x->call[0] = x->name[0] = 0;
        for (int i = 0; i < 4; ++i) x->url[i][0] = 0;
    } else if (ev == RF_J_VALUE && depth == 3) {
        if (!strcmp(key, "callLetters")) put(x->call, sizeof x->call, val);
        if (!strcmp(key, "name")) put(x->name, sizeof x->name, val);
    } else if (ev == RF_J_VALUE && depth == 4) {
        for (int i = 0; i < 4; ++i)
            if (!strcmp(key, kinds[i])) put(x->url[i], RF_URL, val);
    } else if (ev == RF_J_END_OBJECT && depth == 2) {
        char *dash = strchr(x->call, '-'); /* WGCI-FM; an HD channel (KASE-HD2) is not a station */
        if (!dash || (strcmp(dash, "-FM") && strcmp(dash, "-AM"))) return;
        *dash = 0;
        rf_station *st = rf_find(x->l, x->call, dash[1] == 'A');
        if (!st) return;
        /* two: the HTTPS one (Shoutcast's, else HLS), then the plain HTTP one, for a stream host whose
         * certificate the player can't verify */
        int added = rf_add(st, "iheart", x->url[0][0] ? x->url[0] : x->url[2]);
        added += rf_add(st, "iheart", x->url[1][0] ? x->url[1] : x->url[3]);
        if (added) name(st, x->name), ++x->found;
    }
}

/* The streams of iHeart's answer (liveStations?lat=..&lng=..) for the list's stations; how many matched. */
int rf_iheart(rf_list *l, const char *body, unsigned n) {
    ih_ctx *x = calloc(1, sizeof *x);
    if (!x) return 0;
    x->l = l;
    rf_json(body, n, ih_cb, x);
    int found = x->found;
    free(x);
    return found;
}

/* ---- Radio Browser: a state's stations, matched by the call sign in their name, tags or home page ---- */

typedef struct {
    rf_list *l;
    int found;
    char name[RF_NAME], url[RF_URL], resolved[RF_URL], blob[600];
    unsigned blen;
    int ok;
} rb_ctx;

static void blob_add(rb_ctx *x, const char *s) {
    for (unsigned i = 0; s[i] && x->blen + 2 < sizeof x->blob; ++i) x->blob[x->blen++] = up(s[i]);
    x->blob[x->blen++] = ' ';
    x->blob[x->blen] = 0;
}

/* Does text hold f (a frequency as written: 93.1, 780) as a number of its own? */
static int has_number(const char *text, const char *f) {
    unsigned n = strlen(f);
    for (const char *p = strstr(text, f); p; p = strstr(p + 1, f))
        if ((p == text || !digit(p[-1])) && !digit(p[n]) && !(p[n] == '.' && digit(p[n + 1]))) return 1;
    return 0;
}

static void freq_text(const rf_station *st, char *out, unsigned cap) {
    if (st->flags & RF_AM)
        snprintf(out, cap, "%u", st->freq);
    else
        snprintf(out, cap, "%u.%u", st->freq / 10, st->freq % 10);
}

/* The station a call sign at p in blob names: one its suffix (-FM, FM, -AM, AM) puts on a band, else the only
 * station with that base, else the one whose frequency blob also holds. */
static rf_station *named(rf_list *l, const char *blob, const char *p, unsigned len, const char *base) {
    const char *q = p + len;
    if (*q == '-') ++q;
    if ((q[0] == 'F' || q[0] == 'A') && q[1] == 'M' && !alnum(q[2])) return rf_find(l, base, q[0] == 'A');
    if (alpha(p[len]) || digit(p[len])) return 0;
    rf_station *fm = rf_find(l, base, 0), *am = rf_find(l, base, 1);
    if (!fm || !am) return fm ? fm : am;
    char f[8];
    freq_text(fm, f, sizeof f);
    if (has_number(blob, f)) return fm;
    freq_text(am, f, sizeof f);
    return has_number(blob, f) ? am : 0;
}

static void rb_cb(void *c, int ev, int depth, const char *key, const char *val) {
    rb_ctx *x = c;
    if (ev == RF_J_OBJECT && depth == 1) { /* [ {one station}, ... ] */
        x->name[0] = x->url[0] = x->resolved[0] = x->blob[0] = 0;
        x->blen = 0, x->ok = 1;
    } else if (ev == RF_J_VALUE && depth == 2) {
        if (!strcmp(key, "name")) put(x->name, sizeof x->name, val), blob_add(x, val);
        else if (!strcmp(key, "tags") || !strcmp(key, "homepage")) blob_add(x, val);
        else if (!strcmp(key, "url")) put(x->url, RF_URL, val);
        else if (!strcmp(key, "url_resolved")) put(x->resolved, RF_URL, val);
        else if (!strcmp(key, "lastcheckok")) x->ok = strcmp(val, "0") != 0;
    } else if (ev == RF_J_END_OBJECT && depth == 1 && x->ok) {
        const char *b = x->blob;
        for (unsigned i = 0; b[i]; ++i) { /* each word that starts like a US call sign: K or W and 2 or 3 letters */
            if ((b[i] != 'K' && b[i] != 'W') || (i && alnum(b[i - 1]))) continue;
            unsigned len = 1;
            while (len < 5 && alpha(b[i + len])) ++len;
            for (unsigned k = len; k >= 3; --k) { /* WBBMFM: WBBM, then the suffix */
                char base[6];
                memcpy(base, b + i, k), base[k] = 0;
                rf_station *st = named(x->l, b, b + i, k, base);
                if (st && rf_add(st, "radiobrowser", x->resolved[0] ? x->resolved : x->url)) {
                    name(st, x->name), ++x->found;
                    return;
                }
                if (st) return;
            }
        }
    }
}

/* The streams of Radio Browser's answer (stations/search?state=..) for the list's stations; how many matched.
 * Only stations its last check found working (lastcheckok) count. */
int rf_radiobrowser(rf_list *l, const char *body, unsigned n) {
    rb_ctx *x = calloc(1, sizeof *x);
    if (!x) return 0;
    x->l = l;
    rf_json(body, n, rb_cb, x);
    int found = x->found;
    free(x);
    return found;
}

/* ---- TuneIn: its local list (Browse.ashx?c=local&latlon=), then one station's Tune.ashx ---- */

typedef struct {
    rf_list *l;
    int found, am, group;
    char text[128], id[16];
} ti_ctx;

static void ti_cb(void *c, int ev, int depth, const char *key, const char *val) {
    ti_ctx *x = c;
    /* {"body": [ {"text": "FM", "children": [ {"text": "97.1 | 97.1 The Drive (Classic Rock)", "guide_id"}]}]} */
    if (ev == RF_J_VALUE && depth == 3 && !strcmp(key, "text"))
        x->group = !strcmp(val, "FM") ? 1 : !strcmp(val, "AM") ? 2 : 0, x->am = x->group == 2;
    else if (ev == RF_J_OBJECT && depth == 4)
        x->text[0] = x->id[0] = 0;
    else if (ev == RF_J_VALUE && depth == 5 && !strcmp(key, "text"))
        put(x->text, sizeof x->text, val);
    else if (ev == RF_J_VALUE && depth == 5 && !strcmp(key, "guide_id"))
        put(x->id, sizeof x->id, val);
    else if (ev == RF_J_END_OBJECT && depth == 4 && x->group && x->id[0] == 's') {
        double f = strtod(x->text, 0);
        unsigned freq = (unsigned)(x->am ? f + 0.5 : f * 10 + 0.5);
        char *bar = strstr(x->text, " | ");
        const char *label = bar ? bar + 3 : x->text;
        char upper[128];
        unsigned k = 0;
        for (; label[k] && k + 1 < sizeof upper; ++k) upper[k] = up(label[k]);
        upper[k] = 0;
        rf_station *pick = 0;
        int same = 0;
        for (int i = 0; i < x->l->n; ++i) {
            rf_station *st = &x->l->st[i];
            if (st->freq != freq || !(st->flags & RF_AM) != !x->am) continue;
            ++same;
            const char *p = strstr(upper, st->base);
            unsigned bl = strlen(st->base);
            if (p && (p == upper || !alnum(p[-1])) && !alpha(p[bl])) pick = st, same = 1;
        }
        if (!pick && same == 1) /* the only station of the list on that frequency */
            for (int i = 0; i < x->l->n && !pick; ++i)
                if (x->l->st[i].freq == freq && !(x->l->st[i].flags & RF_AM) == !x->am) pick = &x->l->st[i];
        if (pick && !pick->tunein[0]) {
            put(pick->tunein, sizeof pick->tunein, x->id);
            char *paren = strstr(label, " (");
            if (paren) *paren = 0;
            name(pick, label);
            ++x->found;
        }
    }
}

/* The TuneIn station ids (st->tunein) of the local list's entries that are the list's stations: same band and
 * frequency, and the call sign in the entry's name or no other station of the list on that frequency. */
int rf_tunein_local(rf_list *l, const char *body, unsigned n) {
    ti_ctx *x = calloc(1, sizeof *x);
    if (!x) return 0;
    x->l = l;
    rf_json(body, n, ti_cb, x);
    int found = x->found;
    free(x);
    return found;
}

/* A Triton mount's own redirect URL, which takes no token: a playlist URL TuneIn hands out
 * (playerservices.streamtheworld.com/pls/WDRVFMAAC.pls?tdtok=...) is turned into it, as an Amperwave playlist
 * (live.amperwave.net/playlist/audacy-wscrfmaac-imc.m3u?source=TuneIn) is into its direct stream. */
static void triton_url(char *out, unsigned cap, const char *mount, unsigned n) {
    int aac = n >= 3 && !memcmp(mount + n - 3, "AAC", 3);
    snprintf(out, cap, "https://playerservices.streamtheworld.com/api/livestream-redirect/%.*s.%s", (int)n, mount,
             aac ? "aac" : "mp3");
}

typedef struct {
    rf_station *st;
    int added;
} tt_ctx;

static void tt_cb(void *c, int ev, int depth, const char *key, const char *val) {
    tt_ctx *x = c;
    if (ev != RF_J_VALUE || strcmp(key, "url") || x->added) return;
    const char *pls = strstr(val, "streamtheworld.com/pls/");
    char url[RF_URL];
    const char *aw = strstr(val, "live.amperwave.net/playlist/");
    if (pls) {
        const char *m = pls + 23, *e = strstr(m, ".pls");
        if (!e) return;
        triton_url(url, sizeof url, m, e - m);
        val = url;
    } else if (aw) { /* an Amperwave playlist: its direct stream, without TuneIn's query */
        const char *m = aw + 28, *e = strstr(m, ".m3u");
        if (!e) return;
        snprintf(url, sizeof url, "https://live.amperwave.net/direct/%.*s", (int)(e - m), m);
        val = url;
    }
    x->added = rf_add(x->st, "tunein", val);
}

/* The first stream of a Tune.ashx answer for st; 1 if added. */
int rf_tunein_tune(rf_station *st, const char *body, unsigned n) {
    tt_ctx x = {st, 0};
    rf_json(body, n, tt_cb, &x);
    return x.added;
}

/* ---- Triton, Amperwave, SoundStack: answers to probes by call sign ---- */

/* st's Triton mount: base call, band, AAC if aac (WXRTFMAAC). */
void rf_mount(const rf_station *st, char *out, int aac) {
    snprintf(out, 16, "%s%s%s", st->base, st->flags & RF_AM ? "AM" : "FM", aac ? "AAC" : "");
}

/* Triton's livestream answer for mount: the mount plays when it says <status-code>200; 1 if added. */
int rf_triton(rf_station *st, const char *mount, const char *body) {
    if (!strstr(body, "<status-code>200</status-code>")) return 0;
    char url[RF_URL];
    triton_url(url, sizeof url, mount, strlen(mount));
    return rf_add(st, "triton", url);
}

/* A SoundStack player page (live.mystreamplayer.com/CALL) declares its stream as
 * streams = [{"format":"iceaac","host":"ais-sa8.cdnstream1.com","id":"pptsa5hdbog/cjo5jxmng7u","https":1}];
 * the first one is https://host/id. 1 if added. */
int rf_soundstack(rf_station *st, const char *body) {
    const char *p = strstr(body, "streams = [{");
    if (!p) return 0;
    const char *e = strstr(p, "}]");
    if (!e) return 0;
    char host[96] = "", id[160] = "";
    for (const char *k = p; k < e; ++k) {
        char *dst = !strncmp(k, "\"host\":\"", 8) ? host : !strncmp(k, "\"id\":\"", 6) ? id : 0;
        if (!dst) continue;
        k += dst == host ? 8 : 6;
        unsigned n = 0, cap = dst == host ? sizeof host : sizeof id;
        while (k < e && *k != '"' && n + 1 < cap) dst[n++] = *k == '\\' ? *++k : *k, ++k;
        dst[n] = 0;
    }
    for (const char *h = host; *h; ++h)
        if (!alnum(*h) && *h != '.' && *h != '-') return 0;
    if (!host[0] || !id[0] || strchr(id, '"')) return 0;
    char url[RF_URL];
    snprintf(url, sizeof url, "https://%s/%s", host, id);
    return rf_add(st, "soundstack", url);
}

/* ---- states, for Radio Browser's search ---- */

static const char states[][2][21] = {
    {"AL", "Alabama"},     {"AK", "Alaska"},       {"AZ", "Arizona"},       {"AR", "Arkansas"},
    {"CA", "California"},  {"CO", "Colorado"},     {"CT", "Connecticut"},   {"DE", "Delaware"},
    {"DC", "District of Columbia"}, {"FL", "Florida"}, {"GA", "Georgia"},   {"HI", "Hawaii"},
    {"ID", "Idaho"},       {"IL", "Illinois"},     {"IN", "Indiana"},       {"IA", "Iowa"},
    {"KS", "Kansas"},      {"KY", "Kentucky"},     {"LA", "Louisiana"},     {"ME", "Maine"},
    {"MD", "Maryland"},    {"MA", "Massachusetts"}, {"MI", "Michigan"},     {"MN", "Minnesota"},
    {"MS", "Mississippi"}, {"MO", "Missouri"},     {"MT", "Montana"},       {"NE", "Nebraska"},
    {"NV", "Nevada"},      {"NH", "New Hampshire"}, {"NJ", "New Jersey"},   {"NM", "New Mexico"},
    {"NY", "New York"},    {"NC", "North Carolina"}, {"ND", "North Dakota"}, {"OH", "Ohio"},
    {"OK", "Oklahoma"},    {"OR", "Oregon"},       {"PA", "Pennsylvania"},  {"RI", "Rhode Island"},
    {"SC", "South Carolina"}, {"SD", "South Dakota"}, {"TN", "Tennessee"},  {"TX", "Texas"},
    {"UT", "Utah"},        {"VT", "Vermont"},      {"VA", "Virginia"},      {"WA", "Washington"},
    {"WV", "West Virginia"}, {"WI", "Wisconsin"},  {"WY", "Wyoming"},       {"PR", "Puerto Rico"},
};

/* A state's name for its two-letter code, or NULL. */
const char *rf_state_name(const char *code) {
    for (unsigned i = 0; i < sizeof states / sizeof *states; ++i)
        if (!memcmp(states[i][0], code, 2) && !code[2]) return states[i][1];
    return 0;
}

/* The states of the list's stations (their cities end ", IL"), most stations first, at most max; how many. */
int rf_states(const rf_list *l, const char **out, int max) {
    const char *name[8];
    int count[8], n = 0;
    for (int i = 0; i < l->n; ++i) {
        const char *c = strstr(l->st[i].city, ", ");
        const char *s = c ? rf_state_name(c + 2) : 0;
        if (!s) continue;
        int j = 0;
        while (j < n && name[j] != s) ++j;
        if (j == n) {
            if (n == 8) continue;
            name[n] = s, count[n++] = 0;
        }
        ++count[j];
    }
    for (int i = 1; i < n; ++i) /* by count, then as first seen */
        for (int j = i; j > 0 && count[j] > count[j - 1]; --j) {
            int c = count[j];
            const char *s = name[j];
            count[j] = count[j - 1], name[j] = name[j - 1], count[j - 1] = c, name[j - 1] = s;
        }
    int k = n < max ? n : max;
    for (int i = 0; i < k; ++i) out[i] = name[i];
    return k;
}

/* ---- IP geolocation ---- */

typedef struct {
    double lat, lon;
    int have;
} geo_ctx;

static void geo_cb(void *c, int ev, int depth, const char *key, const char *val) {
    geo_ctx *x = c;
    if (ev != RF_J_VALUE || depth != 1) return;
    char *end;
    double v = strtod(val, &end);
    if (!strcmp(key, "loc")) { /* ipinfo.io: "41.8886,-87.7986" */
        if (*end == ',') x->lat = v, x->lon = strtod(end + 1, 0), x->have = 3;
    } else if (end != val && !*end) {
        if (!strcmp(key, "latitude") || !strcmp(key, "lat")) x->lat = v, x->have |= 1;
        if (!strcmp(key, "longitude") || !strcmp(key, "lon")) x->lon = v, x->have |= 2;
    }
}

/* A geolocation service's answer (ipinfo.io's loc, or latitude and longitude, as numbers or strings) as a
 * point; 1 if it held one inside the range a place can be (0, 0 is a service's "unknown"). */
int rf_geo(const char *body, double *lat, double *lon) {
    geo_ctx x = {0, 0, 0};
    rf_json(body, strlen(body), geo_cb, &x);
    if (x.have != 3 || x.lat < -90 || x.lat > 90 || x.lon < -180 || x.lon > 180 || (!x.lat && !x.lon)) return 0;
    *lat = x.lat, *lon = x.lon;
    return 1;
}

/* ---- the list as text: what the program prints and the cache holds ---- */

/* A field with no tab or line break in it. */
static unsigned field(char *out, unsigned cap, const char *s) {
    unsigned n = 0;
    for (; s[n] && n + 1 < cap; ++n) out[n] = s[n] == '\t' || s[n] == '\n' || s[n] == '\r' ? ' ' : s[n];
    out[n] = 0;
    return n;
}

/* The list as text into out (cap bytes): the header
 *   #Q2RADIO 1 <fetched, Unix time> <latitude> <longitude> <complete|partial> <label>
 * then a line per station, its fields separated by tabs:
 *   call  FM|AM  frequency (93.1, 780)  km  name  city  then source=url for each stream
 * Its length, or 0 if it did not fit. */
int rf_text(const rf_list *l, char *out, unsigned cap, long fetched, int complete, const char *label) {
    char lab[64];
    field(lab, sizeof lab, label);
    int n = snprintf(out, cap, "#Q2RADIO 1 %ld %.4f %.4f %s %s\n", fetched, l->lat, l->lon,
                     complete ? "complete" : "partial", lab);
    for (int i = 0; i < l->n && n > 0 && (unsigned)n < cap; ++i) {
        const rf_station *st = &l->st[i];
        char f[8], nm[RF_NAME], city[RF_CITY];
        freq_text(st, f, sizeof f);
        field(nm, sizeof nm, st->name);
        field(city, sizeof city, st->city);
        n += snprintf(out + n, cap - n, "%s\t%s\t%s\t%.1f\t%s\t%s", st->call, st->flags & RF_AM ? "AM" : "FM", f,
                      st->km, nm, city);
        for (int k = 0; k < st->n && (unsigned)n < cap; ++k)
            n += snprintf(out + n, cap - n, "\t%s=%s", st->s[k].src, st->s[k].url);
        if ((unsigned)n < cap) n += snprintf(out + n, cap - n, "\n");
    }
    return n > 0 && (unsigned)n < cap ? n : 0;
}

/* A list text's header: when it was fetched and whether it was complete; 1 if text starts with one. */
int rf_header(const char *text, long *fetched, int *complete) {
    if (strncmp(text, "#Q2RADIO 1 ", 11)) return 0;
    char *end;
    *fetched = strtol(text + 11, &end, 10);
    if (end == text + 11) return 0;
    for (int i = 0; i < 2; ++i) { /* latitude, longitude */
        while (*end == ' ') ++end;
        strtod(end, &end);
    }
    while (*end == ' ') ++end;
    *complete = !strncmp(end, "complete ", 9);
    return *complete || !strncmp(end, "partial ", 8);
}

/* The cache's name for a place: z and the ZIP, else g and the point to a tenth of a degree (11 km), so
 * nearby places share a list. */
void rf_key(char *out, unsigned cap, const char *zip, double lat, double lon) {
    if (zip)
        snprintf(out, cap, "z%.5s", zip);
    else
        snprintf(out, cap, "g%+.1f%+.1f", lat, lon);
}
