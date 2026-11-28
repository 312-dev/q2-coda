#include "peq.h"

#ifdef PEQ_HOST
int __isoc99_sscanf(const char *, const char *, ...); /* stdio.h may hide it behind C23's sscanf redirect */
#endif

static int between(double x, double lo, double hi) {
    return __builtin_isfinite(x) && x >= lo && x <= hi;
}

void peq_default(peq_preset *p) {
    static const double frequencies[10] = {31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000};
    memset(p, 0, sizeof(*p));
    p->count = 10;
    p->bypass = 1;
    for (int i = 0; i < PEQ_BANDS; ++i) {
        p->bands[i].frequency = i < 10 ? frequencies[i] : 1000;
        p->bands[i].q = 1.41; /* one octave wide, as graphic EQs use */
    }
}

static int valid_band(const peq_band *b) {
    return b->enabled >= 0 && b->enabled <= 3 && b->type >= 0 && b->type < PEQ_TYPES &&
           between(b->frequency, 20, 20000) && between(b->gain, -24, 24) && between(b->q, 0.1, PEQ_Q_MAX(b->type));
}

int peq_valid(const peq_preset *p) {
    if (!p || p->count < 0 || p->count > PEQ_BANDS || (p->bypass != 0 && p->bypass != 1) ||
        !between(p->preamp, -60, 24) || !between(p->balance, -12, 12) || (unsigned)p->automatic > 2)
        return 0;
    for (int i = 0; i < p->count; ++i) if (!valid_band(&p->bands[i])) return 0;
    return 1;
}

static int error_at(peq_error *e, unsigned line, const char *reason) {
    if (e) { e->line = line; e->reason = reason; }
    return 0;
}

/* Decimal only (comma accepted as the mark, like APO); no hex floats, expressions, NaN or infinity.
 * libc reads the value, and the token must be consumed whole. demo's GOT carries __isoc99_sscanf,
 * not strtod (tools/peq.py LIBC). */
int peq_number(const char *s, double *out) {
    char buf[PEQ_LINE_LIMIT + 1];
    unsigned n = 0;
    while (s[n]) {
        if (n == PEQ_LINE_LIMIT) return 0;
        char c = s[n];
        if (!(c >= '0' && c <= '9') && c != '+' && c != '-' && c != '.' && c != ',' &&
            c != 'e' && c != 'E') return 0;
        buf[n] = c == ',' ? '.' : c;
        ++n;
    }
    buf[n] = 0;
    const char *p = buf + (buf[0] == '+' || buf[0] == '-');
    if (!n || (p[0] == '0' && (p[1] | 32) == 'x')) return 0; /* no hex floats */
    int len = 0;
    double v;
    if (__isoc99_sscanf(buf, "%lf%n", &v, &len) != 1 || len != (int)n || !__builtin_isfinite(v))
        return 0;
    *out = v;
    return 1;
}

/* A line left out: counted, the first one's line, reason and a word for it kept for the message. */
static void skip(peq_error *e, unsigned line, const char *reason, const char *what, const char *word) {
    if (!e->skipped++) {
        e->line = line, e->reason = reason;
        if (what) snprintf(e->what, sizeof e->what, "%s%s", what, word ? word : "");
        else snprintf(e->what, sizeof e->what, "line %u", line);
    }
}

static int is(const char *t, const char *word) { return !strcasecmp(t, word); }

/* Equalizer APO's configuration text (docs/internals.md#lenient-import): Preamp, Channel and
 * Filter lines, whatever the case, "1000Hz" as "1000 Hz". Lines the Q2 can't play are left out
 * and counted in error->skipped, the first named in error->what; text without a colon is no
 * command, as APO takes it. Only a file too big, a NUL, an over-long line, a summed preamp or
 * balance out of range, or nothing usable at all fails it. */
int peq_parse(const char *text, unsigned size, peq_preset *out, peq_error *error) {
    peq_preset p;
    peq_error none;
    peq_default(&p);
    p.count = 0;
    p.bypass = 0;
    unsigned pos = 0, line = 0, preamp_line = 0, used = 0, words = 0;
    int scope = 1, automatic = 0, graphic = 0; /* APO Channel: 1 both, 2 left, 3 right, 0 neither */
    double side[4] = {0}; /* Preamp under Channel: L or R */
    if (!error) error = &none;
    memset(error, 0, sizeof *error);
    if (size > PEQ_FILE_LIMIT) return error_at(error, 1, "file exceeds 16384 bytes");
    if (size >= 3 && (unsigned char)text[0] == 239 && (unsigned char)text[1] == 187 &&
        (unsigned char)text[2] == 191) pos = 3;
    while (pos < size) {
        char buf[PEQ_LINE_LIMIT + 1], split[2 * PEQ_LINE_LIMIT + 2], *t[16], *d = split;
        unsigned length = 0;
        ++line;
        while (pos < size && text[pos] != '\n') {
            if (length == PEQ_LINE_LIMIT) return error_at(error, line, "line exceeds 512 bytes");
            if (!text[pos]) return error_at(error, line, "embedded NUL");
            buf[length++] = text[pos++];
        }
        if (pos < size) ++pos;
        buf[length] = 0;
        int n = 0, colon = 0, over = 0;
        char *s = buf;
        while (*s == ' ' || *s == '\t') ++s;
        /* "# q2: preamp auto" is the Q2's own setting (peq_export), a comment to anything else. */
        if (*s == '#') {
            for (++s; *s == ' ' || *s == '\t';) ++s;
            if (!strncasecmp(s, "q2:", 3)) {
                for (s += 3; *s == ' ' || *s == '\t';) ++s;
                if (!strncasecmp(s, "preamp auto", 11)) automatic = 1;
            }
            continue;
        }
        /* Words into split, each ended: a colon is one of its own, even in "Filter 1:", and a
         * number runs into its unit ("1000Hz", "-3dB") only up to the first letter past an
         * exponent's e. */
        while (*s && *s != '#') {
            if (*s == ' ' || *s == '\t' || *s == '\r') { ++s; continue; }
            if (n == 16) { over = 1; break; }
            t[n++] = d;
            if (*s == ':') { *d++ = *s++, *d++ = 0, colon = 1; continue; }
            int number = (*s >= '0' && *s <= '9') || *s == '+' || *s == '-' || *s == '.' || *s == ',';
            while (*s && *s != ' ' && *s != '\t' && *s != '\r' && *s != ':' && *s != '#') {
                int letter = (*s | 32) >= 'a' && (*s | 32) <= 'z';
                if (number && letter && !((*s | 32) == 'e' && ((s[1] >= '0' && s[1] <= '9') || s[1] == '-' || s[1] == '+')))
                    break;
                *d++ = *s++;
            }
            *d++ = 0;
        }
        if (!n) continue;
        if (!colon) { ++words; continue; } /* not a command: a title, notes, REW's header */
        if (over) { skip(error, line, "unsupported filter form", 0, 0); continue; }
        if (is(t[0], "Preamp")) {
            double gain;
            if (n < 3 || n > 4 || strcmp(t[1], ":") || (n == 4 && !is(t[3], "dB")) || !peq_number(t[2], &gain) ||
                !between(gain, -60, 24)) {
                skip(error, line, "Preamp: -60 to 24 dB", "Preamp", 0);
                continue;
            }
            if (!scope) continue; /* another channel's, its Channel line skipped */
            if (scope == 1) p.preamp += gain;
            else side[scope] += gain;
            preamp_line = line, ++used;
            continue;
        }
        if (is(t[0], "Channel")) {
            int l = 0, r = 0, other = 0;
            if (n < 3 || strcmp(t[1], ":")) { skip(error, line, "expected Channel: L, R or all", "Channel", 0); continue; }
            for (int i = 2; i < n; ++i) {
                if (is(t[i], "L") || !strcmp(t[i], "1")) l = 1;
                else if (is(t[i], "R") || !strcmp(t[i], "2")) r = 1;
                else if (is(t[i], "all")) l = r = 1;
                else if (!other++) skip(error, line, "channels L, R and all only", "Channel ", t[i]);
            }
            scope = l && r ? 1 : l ? 2 : r ? 3 : 0;
            used += scope != 0;
            continue;
        }
        if (is(t[0], "GraphicEQ")) graphic = 1;
        if (strncasecmp(t[0], "Filter", 6)) { skip(error, line, "unsupported command", t[0], 0); continue; }
        int k = 1;
        for (s = t[0] + 6; *s >= '0' && *s <= '9'; ++s) {}
        if (*s) { skip(error, line, "unsupported command", t[0], 0); continue; }
        if (n > 1 && strcmp(t[1], ":")) {
            for (s = t[1]; *s >= '0' && *s <= '9'; ++s) {}
            if (*s) { skip(error, line, "invalid filter label", 0, 0); continue; }
            ++k;
        }
        if (n <= k + 2 || strcmp(t[k++], ":")) {
            if (!(n == k + 2 && is(t[k+1], "None"))) skip(error, line, "expected Filter: ON <type>", 0, 0);
            continue; /* ... ON None: REW's and APO's empty slot */
        }
        peq_band b = {0, 0, 0, 0, 0.7071067811865476};
        double slope = 0, bw = 0, gain = 0;
        int fc = 0, has_gain = 0, has_q = 0;
        if (is(t[k], "ON")) b.enabled = scope;
        else if (!is(t[k], "OFF")) { skip(error, line, "expected ON or OFF", 0, 0); continue; }
        ++k;
        /* APO's names (BiQuadFilterFactory.cpp): LP and LPQ, HP and HPQ are the same filter. */
        static const char names[][6] = {"PK", "PEQ", "Modal", "LS", "LSC", "HS", "HSC", "LP", "LPQ", "HP", "HPQ", "BP", "NO", "AP"};
        static const signed char kinds[] = {PEQ_PK, PEQ_PK, PEQ_PK, PEQ_LS, PEQ_LS, PEQ_HS, PEQ_HS,
                                            PEQ_LP, PEQ_LP, PEQ_HP, PEQ_HP, PEQ_BP, PEQ_NO, PEQ_AP};
        const char *type = t[k++];
        b.type = -1;
        for (unsigned j = 0; j < sizeof kinds; ++j) if (is(type, names[j])) b.type = kinds[j];
        if (b.type < 0) {
            if (b.enabled && !is(type, "None")) skip(error, line, "unsupported filter type", type, " filter");
            continue;
        }
        int shelf = b.type == PEQ_LS || b.type == PEQ_HS, gained = PEQ_GAINED(b.type);
        int corner = shelf && !type[2], bad = 0; /* APO: LS/HS take a corner frequency, LSC/HSC the centre */
        /* APO's defaults without a Q: 0.7071 for the passes (b.q's start), 30 for a notch. */
        if (b.type == PEQ_NO) b.q = 30;
        /* A shelf's slope, "LS 6dB" or "LSC 12 dB", comes first; then each named value. */
        if (shelf && k + 1 < n && is(t[k+1], "dB") && peq_number(t[k], &slope)) k += 2;
        while (k < n && !bad) {
            if (is(t[k], "Fc") && k + 2 < n && is(t[k+2], "Hz")) fc = peq_number(t[k+1], &b.frequency), bad = !fc, k += 3;
            else if (is(t[k], "Gain") && k + 2 < n && is(t[k+2], "dB")) has_gain = 1, bad = !peq_number(t[k+1], &gain), k += 3;
            else if (is(t[k], "Q") && k + 1 < n) has_q = 1, bad = !peq_number(t[k+1], &b.q), k += 2;
            else if (is(t[k], "BW") && k + 2 < n && is(t[k+1], "Oct")) bad = !peq_number(t[k+2], &bw) || bw <= 0, k += 3;
            else bad = 1;
        }
        b.gain = gained ? gain : 0; /* APO ignores a pass's, notch's or all-pass's gain, and BP's plays none */
        /* APO takes bandwidth over Q, at the rate playing; here at 48 kHz, as the graph and
         * Auto's figure: Q = 1 / (2 sinh(ln 2 / 2 * BW * w / sin w)). A shelf ignores it. */
        if (!bad && fc && !shelf && bw && between(b.frequency, 20, 20000)) {
            double w = 6.283185307179586 * b.frequency / 48000, x = bw / 2 * w / sin(w);
            b.q = 1 / (pow(2, x) - pow(2, -x));
            has_q = 1;
        }
        /* A gain on a peak and shelves; a Q (or BW) on a peak and an all-pass, as APO asks */
        if (bad || !fc || (gained && !has_gain) || ((b.type == PEQ_PK || b.type == PEQ_AP) && !has_q)) {
            if (b.enabled) skip(error, line, "expected Fc, Gain and a peak's Q", 0, 0);
            continue;
        }
        if (shelf && valid_band(&b)) {
            /* Match Equalizer APO: a slope S (12 dB is 1), or 0.9 with neither it nor a Q, at Fc;
             * LS and HS with either shift a corner to the centre. inverse is 1/S. */
            double a = pow(10, b.gain / 40), ab = a + 1 / a, inverse = 1 / (slope ? slope / 12 : 0.9);
            if (has_q && !slope) inverse = (1 / (b.q * b.q) - 2) / ab + 1;
            else b.q = 1 / __builtin_sqrt(ab * (inverse - 1) + 2);
            if (corner && (has_q || slope)) {
                double f = pow(10, __builtin_fabs(b.gain) / 80 * inverse);
                b.frequency = b.type == PEQ_LS ? b.frequency * f : b.frequency / f;
                /* The shift can leave 20..20000 Hz; clamp instead of leaving the band out. */
                if (b.frequency > 20000) b.frequency = 20000;
                if (b.frequency < 20) b.frequency = 20;
            }
        }
        if (!valid_band(&b)) {
            /* APO ignores OFF filters; exporters fill blank slots with Fc 0 Q 0 */
            if (b.enabled) skip(error, line, "filter outside the ranges", "band out of range", 0);
            continue;
        }
        if (!scope) continue; /* another channel's, its Channel line skipped */
        if (p.count == PEQ_BANDS) { skip(error, line, "more than 30 bands", "bands past 30", 0); continue; }
        p.bands[p.count++] = b, ++used;
    }
    /* Per-channel preamps become the louder one's common preamp plus a balance that turns the other down. */
    p.preamp += side[2] > side[3] ? side[2] : side[3];
    p.balance = side[3] - side[2];
    if (!between(p.preamp, -60, 24)) return error_at(error, preamp_line, "summed preamp outside -60..24 dB");
    if (!between(p.balance, -12, 12)) return error_at(error, preamp_line, "L/R preamps over 12 dB apart");
    if (!used && graphic) return error_at(error, 0, "Use the ParametricEQ file, not GraphicEQ"); /* no line to blame */
    if (!used && error->skipped) return error_at(error, error->line, error->reason);
    if (!used && words) return error_at(error, 1, "no Equalizer APO lines");
    /* The file's preamp, as if picked; without one, or marked Auto, Auto */
    p.automatic = automatic || !preamp_line ? 1 : 2;
    *out = p; /* Commit only after the complete file validates. */
    return 1;
}

int peq_import_file(const char *path, peq_preset *out, peq_error *error) {
    char *text = calloc(1, PEQ_FILE_LIMIT + 1);
    if (!text) return error_at(error, 1, "out of memory");
    void *f = fopen(path, "rb");
    if (!f) { free(text); return error_at(error, 1, "cannot open file"); }
    unsigned n = fread(text, 1, PEQ_FILE_LIMIT + 1, f);
    int bad = ferror(f);
    if (fclose(f)) bad = 1;
    int ok = bad ? error_at(error, 1, "file read failed") : peq_parse(text, n, out, error);
    free(text);
    return ok;
}

int peq_load(const char *path, peq_preset *out) {
    struct { char magic[8]; peq_preset preset; } file;
    void *f = fopen(path, "rb");
    if (!f) return 0;
    char extra;
    memset(file.magic, 0, sizeof(file.magic));
    peq_default(&file.preset); /* bands a v1 file lacks keep their defaults */
    unsigned n = fread(&file, 1, sizeof(file), f);
    int ok = fread(&extra, 1, 1, f) == 0 && !ferror(f);
    if (fclose(f)) ok = 0;
    /* Q2PEQ01 predates balance and held ten bands; Q2PEQ02 before 1.3.6 lacks automatic. */
    unsigned want =
        !memcmp(file.magic, "Q2PEQ01", 8) ? 8 + __builtin_offsetof(peq_preset, bands[10]) : 0;
    if (!memcmp(file.magic, "Q2PEQ02", 8)) want = n == sizeof(file) ? n : 8 + PEQ_PRESET_V2;
    if (!ok || !want || n != want || !peq_valid(&file.preset)) return 0;
    *out = file.preset;
    return 1;
}

/* magic (8 bytes, unless 0) then data, fsynced under path.tmp and renamed over path. */
int peq_save_file(const char *path, const char *magic, const void *data, unsigned size) {
    char tmp[600];
    if (strlen(path) > 580) return 0;
    snprintf(tmp, sizeof(tmp), "%s.tmp", path);
    void *f = fopen(tmp, "wb");
    if (!f) return 0;
    int ok = (!magic || fwrite(magic, 1, 8, f) == 8) && fwrite(data, 1, size, f) == size;
    if (fflush(f) || fsync(fileno(f))) ok = 0;
    if (fclose(f)) ok = 0;
    if (ok && !rename(tmp, path)) return 1;
    unlink(tmp);
    return 0;
}

int peq_save(const char *path, const peq_preset *p, int replace) {
    if (!peq_valid(p)) return 0;
    if (!replace && !access(path, 0)) return 2;
    return peq_save_file(path, "Q2PEQ02", p, sizeof(*p));
}

/* v in the fewer digits of %.15g and %.17g that peq_number reads back as v. */
static void decimal(char *s, double v) {
    double back;
    snprintf(s, 32, "%.15g", v);
    if (!peq_number(s, &back) || back != v) snprintf(s, 32, "%.17g", v);
}

/* Equalizer APO's own lines (docs/internals.md#presets-on-the-card): Preamp, a balance as one
 * side's Preamp, a Channel line where a band's side changes, and the bands as numbered Filter
 * lines with Q: PK, LSC and HSC with their Gain, since a band's frequency is the centre, then LPQ,
 * HPQ, BP, NO and AP without one. OFF bands stay, for peq_parse; APO skips them. Only Auto has no
 * APO form. */
unsigned peq_export(const peq_preset *p, char *out, unsigned size) {
    static const char *const types[] = {"PK", "LSC", "HSC", "LPQ", "HPQ", "BP", "NO", "AP"},
                      *const sides[] = {"", "all", "L", "R"};
    char a[32], b[32], c[32], g[48];
    unsigned n = 0;
    int scope = 1;
    if (!peq_valid(p)) return 0;
    decimal(a, p->preamp);
    decimal(b, -__builtin_fabs(p->balance));
    n += snprintf(out, size, "# Equalizer APO preset from q2-coda. \"# q2:\" lines are the Q2's own.\n%s"
                             "Preamp: %s dB\n", p->automatic == 2 ? "" : "# q2: preamp auto\n", a);
    if (p->balance && n < size) /* a positive balance turns the left down */
        n += snprintf(out + n, size - n, "Channel: %s\nPreamp: %s dB\nChannel: all\n", p->balance > 0 ? "L" : "R", b);
    for (int i = 0; i < p->count && n < size; ++i) {
        const peq_band *d = &p->bands[i];
        if (d->enabled && d->enabled != scope)
            n += snprintf(out + n, size - n, "Channel: %s\n", sides[scope = d->enabled]);
        decimal(a, d->frequency), decimal(b, d->gain), decimal(c, d->q);
        if (PEQ_GAINED(d->type)) snprintf(g, sizeof g, " Gain %s dB", b);
        else g[0] = 0;
        if (n < size)
            n += snprintf(out + n, size - n, "Filter %d: %s %s Fc %s Hz%s Q %s\n", i + 1,
                          d->enabled ? "ON" : "OFF", types[d->type], a, g, c);
    }
    return n < size ? n : 0;
}

int peq_export_file(const char *path, const peq_preset *p, int replace) {
    char text[4096]; /* 30 bands at 17 digits, each on its own side, take under 3.8 KB */
    unsigned n = peq_export(p, text, sizeof text);
    if (!n) return 0;
    if (!replace && !access(path, 0)) return 2;
    return peq_save_file(path, 0, text, n);
}

/* The 3EQ's fixed filters at gain dB each: a 155 Hz low shelf, a peak at 900 Hz to cut or 1750 Hz to
 * boost (Listener's reading of Apple's AirPods EQ; Q 0.707 and the frequencies within its ranges are
 * Vamp's choices), a 5 kHz high shelf. A zero gain is an identity band. */
void eq3_bands(peq_preset *p, const double gain[3]) {
    static const int types[3] = {1, 0, 2};
    static const double frequencies[3] = {155, 1750, 5000};
    memset(p, 0, sizeof(*p));
    p->count = 3;
    for (int i = 0; i < 3; ++i)
        p->bands[i] = (peq_band){1, types[i], i == 1 && gain[1] < 0 ? 900 : frequencies[i], gain[i], 0.707};
}

static int eq_valid(const eq_state *s) {
    return s->mode >= EQ_OFF && s->mode <= EQ_PEQ && (s->hold == 0 || s->hold == 1) && peq_valid(&s->peq) &&
           peq_valid(&s->eq3) && s->eq3.count == 3;
}

/* Q2PEQ03, else a Q2PEQ01/02 preset from before 3EQ: its PEQ, on unless bypassed, and a flat 3EQ. */
int eq_load_from(const char *path, eq_state *s) {
    static const double flat[3];
    struct { char magic[8]; eq_state s; } file;
    memset(s, 0, sizeof(*s));
    peq_default(&s->peq);
    eq3_bands(&s->eq3, flat);
    void *f = fopen(path, "rb");
    if (!f) return 0;
    char extra;
    memset(&file, 0, sizeof(file));
    unsigned n = fread(&file, 1, sizeof(file), f), v2 = 8 + 8 + 2 * PEQ_PRESET_V2;
    int ok = (n == sizeof(file) || n == v2) && fread(&extra, 1, 1, f) == 0 && !ferror(f);
    if (fclose(f)) ok = 0;
    /* 1.3.5's: presets without automatic, which the zeroed tail leaves 0; demo has no memmove. */
    if (n == v2) {
        peq_preset eq3;
        memcpy(&eq3, (char *)&file.s.peq + PEQ_PRESET_V2, sizeof(eq3));
        file.s.eq3 = eq3;
        file.s.peq.automatic = file.s.peq.spare = 0;
    }
    if (ok && !memcmp(file.magic, "Q2PEQ03", 8) && eq_valid(&file.s)) {
        *s = file.s;
        return 1;
    }
    if (!peq_load(path, &s->peq)) return 0;
    s->mode = s->peq.bypass ? EQ_OFF : EQ_PEQ;
    return 1;
}
int eq_load(eq_state *s) { return eq_load_from(PEQ_ACTIVE, s); }

int eq_save_to(const char *path, const eq_state *s) {
    return eq_valid(s) && peq_save_file(path, "Q2PEQ03", s, sizeof(*s));
}
int eq_save(const eq_state *s) { return eq_save_to(PEQ_ACTIVE, s); }

/* What plays: the mode's preset, bypassed when Off, or with hold the 3EQ's preamp alone. Only PEQ
 * marks Auto (1), unless picked: never picked (0) is Auto, as the editor takes it. Either way the
 * saved preamp is what plays, Auto's being demo's cut for every rate (peq_level.c peq_headroom). */
void eq_effective(const eq_state *s, peq_preset *out) {
    *out = s->mode == EQ_3 ? s->eq3 : s->peq;
    if (s->mode == EQ_PEQ ? !out->automatic : out->automatic == 1)
        out->automatic = s->mode == EQ_PEQ;
    out->bypass = s->mode == EQ_OFF && !s->hold;
    if (s->mode == EQ_OFF && s->hold) out->count = 0, out->preamp = s->eq3.preamp, out->balance = 0;
}

int peq_load_active(peq_preset *p) {
    eq_state s;
    int ok = eq_load(&s);
    eq_effective(&s, p);
    return ok;
}

/* The preamp, linear, on every channel, then balance turning one side down. */
static void level(peq_engine *e, double gain, double balance) {
    for (int ch = 0; ch < PEQ_CHANNELS; ++ch) e->gain[ch] = gain;
    if (balance > 0) e->gain[0] *= pow(10, -balance / 20);
    if (balance < 0) e->gain[1] *= pow(10, balance / 20);
}

/* The RBJ Audio EQ Cookbook's peaking, Q-form shelves, low and high pass, band pass (0 dB peak),
 * notch and all pass, as Equalizer APO's BiQuad.cpp designs them. All design work is outside the
 * PCM callback; the per-sample work is the same biquad whatever the type. */
int peq_compile(const peq_preset *p, int rate, peq_engine *out) {
    peq_engine e;
    memset(&e, 0, sizeof(e));
    if (!peq_valid(p) || rate < 8000 || rate > 384000) return 0;
    level(&e, pow(10, p->preamp / 20), p->balance);
    e.bypass = p->bypass;
    for (int i = 0; i < PEQ_BANDS; ++i) {
        e.c[i].b0 = 1;
        /* A peak or shelf of no gain is identity; the other types act at any gain */
        if (i >= p->count || !p->bands[i].enabled || (PEQ_GAINED(p->bands[i].type) && !p->bands[i].gain)) continue;
        e.used = i + 1;
        if (p->bands[i].enabled > 1) e.only[i] = p->bands[i].enabled - 1;
        const peq_band *b = &p->bands[i];
        /* Not representable at this rate; skip only this band. ponytail: a high or band pass up
         * there would silence the band's channels, and passes everything instead (rates under 40 kHz
         * only, since Fc stops at 20 kHz). */
        if (b->frequency >= rate * 0.5) continue;
        double a = pow(10, b->gain / 40), w = 6.283185307179586 * b->frequency / rate;
        double c = cos(w), alpha = sin(w) / (2 * b->q), r = 2 * __builtin_sqrt(a) * alpha;
        double b0, b1, b2, a0 = 1 + alpha, a1 = -2 * c, a2 = 1 - alpha; /* the gainless types' poles */
        if (b->type == PEQ_LP) b0 = b2 = (1 - c) / 2, b1 = 1 - c;
        else if (b->type == PEQ_HP) b0 = b2 = (1 + c) / 2, b1 = -(1 + c);
        else if (b->type == PEQ_BP) b0 = alpha, b1 = 0, b2 = -alpha;
        else if (b->type == PEQ_NO) b0 = b2 = 1, b1 = a1;
        else if (b->type == PEQ_AP) b0 = a2, b1 = a1, b2 = a0;
        else if (b->type == PEQ_PK) {
            b0 = 1 + alpha * a; b1 = -2 * c; b2 = 1 - alpha * a;
            a0 = 1 + alpha / a; a1 = -2 * c; a2 = 1 - alpha / a;
        } else if (b->type == PEQ_LS) {
            b0 = a * ((a+1) - (a-1)*c + r); b1 = 2*a*((a-1) - (a+1)*c);
            b2 = a*((a+1) - (a-1)*c - r); a0 = (a+1) + (a-1)*c + r;
            a1 = -2*((a-1) + (a+1)*c); a2 = (a+1) + (a-1)*c - r;
        } else {
            b0 = a*((a+1) + (a-1)*c + r); b1 = -2*a*((a-1) + (a+1)*c);
            b2 = a*((a+1) + (a-1)*c - r); a0 = (a+1) - (a-1)*c + r;
            a1 = 2*((a-1) - (a+1)*c); a2 = (a+1) - (a-1)*c - r;
        }
        e.c[i] = (peq_coeff){b0/a0, b1/a0, b2/a0, a1/a0, a2/a0};
    }
    *out = e;
    return 1;
}

/* |H|^2 of the engine's left and right chains, without preamp or balance. */
void peq_response(const peq_engine *e, int rate, double f, double *l, double *r) {
    double w = 6.283185307179586 * f / rate, c1 = cos(w), s1 = sin(w), c2 = 2 * c1 * c1 - 1,
           s2 = 2 * s1 * c1;
    *l = *r = 1;
    for (int i = 0; i < e->used; ++i) { /* bands past used are identity */
        const peq_coeff *q = &e->c[i];
        double nr = q->b0 + q->b1 * c1 + q->b2 * c2, ni = q->b1 * s1 + q->b2 * s2;
        double dr = 1 + q->a1 * c1 + q->a2 * c2, di = q->a1 * s1 + q->a2 * s2;
        double m = (nr * nr + ni * ni) / (dr * dr + di * di);
        if (e->only[i] != 2) *l *= m; /* only: 1 left, 2 right */
        if (e->only[i] != 1) *r *= m;
    }
}

/* The loudest |H|^2 of e (p compiled at rate), either channel, at least 1 (Auto never boosts) and
 * at most 60 dB. Includes DC, Nyquist and each band's centre: near Nyquist a peak can be narrower
 * than the grid. shortcut: sampled steady-state peaks, not a transient or intersample bound. */
static double peak(const peq_engine *e, const peq_preset *p, int rate) {
    double nyquist = rate * 0.5, best = 1;
    for (int k = -2; k < 1024 + p->count; ++k) {
        double f = k == -2    ? 0
                   : k == -1  ? nyquist
                   : k < 1024 ? pow(nyquist, k / 1023.0)
                              : p->bands[k - 1024].frequency;
        if (f >= nyquist && k != -1) continue;
        double l, r;
        peq_response(e, rate, f, &l, &r);
        if (l > best) best = l;
        if (r > best) best = r;
    }
    return best < 1.0000230261 ? 1 : best > 1e6 ? 1e6 : best; /* under 0.0001 dB is none */
}

double peq_auto(const peq_preset *p, int rate) {
    peq_engine e;
    if (!peq_compile(p, rate, &e)) return p->preamp;
    double v = peak(&e, p, rate);
    return v > 1 ? -decibels(v) : 0; /* no boost: +0, not -0.0 on screen */
}

void peq_reset(peq_dsp *d, int rate, int channels, const peq_preset *p) {
    memset(d, 0, sizeof(*d));
    d->rate = rate;
    d->channels = channels;
    d->ramp_length = rate / 50; /* 20 ms */
    if (!peq_compile(p, rate, &d->current)) d->current.bypass = 1; /* the saved preamp, Auto's too */
}

int peq_update(peq_dsp *d, const peq_preset *p) {
    if (!peq_compile(p, d->rate, &d->pending)) return 0;
    d->waiting = 1; /* Latest complete update wins; never interrupt an in-flight crossfade. */
    return 1;
}

/* Identity coefficients: with zero memory the band passes x through unchanged. */
static inline __attribute__((always_inline)) int identity(const peq_coeff *c) { return c->b0 == 1 && !c->b1 && !c->b2 && !c->a1 && !c->a2; }

/* The bands sample can act on, in order: identity bands with zero memory stay so until an update. */
static int live(const peq_engine *e, int *list) {
    int n = 0;
    for (int i = 0; i < e->used; ++i) {
        int on = e->only[i] || !identity(&e->c[i]);
        for (int ch = 0; ch < PEQ_CHANNELS && !on; ++ch) on = e->z[ch][i][0] || e->z[ch][i][1];
        if (on) list[n++] = i;
    }
    return n;
}

static double sample(peq_engine *e, const int *list, int n, double x, int ch) {
    if (e->bypass) return x;
    for (int k = 0; k < n; ++k) {
        int i = list[k];
        const peq_coeff *c = &e->c[i];
        double *z = e->z[ch][i];
        /* The other channel's band: drop memory carried from a both-channel version. */
        if (e->only[i] && e->only[i] != ch + 1) { z[0] = z[1] = 0; continue; }
        /* Still checked per sample: a band can drain to idle mid-block. */
        if (identity(c) && !z[0] && !z[1]) continue;
        /* Flush |y| < ~2e-34 to zero: decaying tails would otherwise reach denormals, which MIPS FPUs trap on. */
        double y = c->b0 * x + z[0] + 1e-18 - 1e-18;
        z[0] = c->b1 * x - c->a1 * y + z[1];
        z[1] = c->b2 * x - c->a2 * y;
        x = y;
    }
    return x * e->gain[ch]; /* after the cascade, so filter memory is independent of preamp */
}

void peq_process(peq_dsp *d, float *audio, unsigned frames) {
    if (!audio || d->channels < 1 || d->channels > PEQ_CHANNELS) return;
    if (d->current.bypass && !d->ramp && !d->waiting) return; /* steady bypass leaves the whole block untouched */
    int cur[PEQ_BANDS], nxt[PEQ_BANDS], nc = live(&d->current, cur), nn = d->ramp ? live(&d->next, nxt) : 0;
    for (unsigned i = 0; i < frames; ++i) {
        if (!d->ramp && d->waiting) {
            d->next = d->pending;
            d->waiting = 0;
            d->ramp = d->ramp_length;
            /* Carry filter memory over so unchanged bands and preamp-only edits crossfade without a transient. */
            if (!d->current.bypass) memcpy(d->next.z, d->current.z, sizeof(d->next.z));
            /* Bands dropped from the end still run until their carried memory drains. */
            for (int b = d->next.used; b < d->current.used; ++b)
                for (int ch = 0; ch < PEQ_CHANNELS; ++ch) if (d->next.z[ch][b][0] || d->next.z[ch][b][1]) d->next.used = b + 1;
            nn = live(&d->next, nxt);
        }
        for (int ch = 0; ch < d->channels; ++ch, ++audio) {
            if (!d->ramp && d->current.bypass) continue; /* bit-exact steady bypass */
            double x = *audio;
            if (!__builtin_isfinite(x)) x = 0;
            double y = sample(&d->current, cur, nc, x, ch);
            if (d->ramp) {
                double wet = sample(&d->next, nxt, nn, x, ch);
                double mix = (double)(d->ramp_length - d->ramp + 1) / d->ramp_length;
                y += (wet - y) * mix;
            }
            /* Keep float headroom for downstream volume; the output converter owns full-scale clipping. */
            *audio = (float)y;
        }
        if (d->ramp && !--d->ramp) d->current = d->next, nc = nn, memcpy(cur, nxt, sizeof(cur));
    }
}

double decibels(double power) { return 10 * log(power) / 2.302585092994046; } /* no log10 in demo's GOT */

long long now_ns(void) {
    struct { long s, ns; } t; /* struct timespec */
    clock_gettime(1, (void *)&t); /* CLOCK_MONOTONIC: the same clock in hciplayer and demo */
    return t.s * 1000000000LL + t.ns;
}
unsigned long long now_ms(void) { /* not now_ns() / 1e6: no __udivdi3 here */
    struct { long s, ns; } t;
    clock_gettime(1, (void *)&t); /* CLOCK_MONOTONIC, librespot's at= clock and the press stamp's on both sides */
    return (unsigned long long)(unsigned)t.s * 1000 + (unsigned)t.ns / 1000000;
}
