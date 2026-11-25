/* q2wifi's core (docs/internals.md#wi-fi-fault-recorder): the deaf radio's watch, the parsers of
 * wpa_supplicant's answers and events and of the kernel's files, and the log's lines, with no reads
 * of the device (q2wifi.c reads; test/unit/test_q2wifi.py tests this on this machine). */
#include "q2wifi.h"

/* ---- privacy ---- */
static int hexval(int c) {
    return c >= '0' && c <= '9'   ? c - '0'
           : c >= 'a' && c <= 'f' ? c - 'a' + 10
           : c >= 'A' && c <= 'F' ? c - 'A' + 10
                                  : -1;
}
static int is_mac(const char *s) { /* xx:xx:xx:xx:xx:xx */
    for (int i = 0; i < 17; ++i)
        if (i % 3 == 2 ? s[i] != ':' : hexval((unsigned char)s[i]) < 0) return 0;
    return 1;
}
unsigned wifi_salt(const char *boot_id) {
    unsigned h = 2166136261u;
    for (const char *s = boot_id; s && *s && *s != '\n'; ++s)
        h = (h ^ (unsigned char)*s) * 16777619u;
    return h;
}
void wifi_hash(unsigned salt, const char *mac, char *out) {
    static const char hex[] = "0123456789abcdef";
    unsigned h = salt;
    for (int i = 0; i < 17; ++i) {
        int v = hexval((unsigned char)mac[i]);
        if (v >= 0) h = (h ^ (unsigned)v) * 16777619u;
    }
    out[0] = '#';
    for (int i = 0; i < 6; ++i) out[1 + i] = hex[h >> (20 - 4 * i) & 15];
    out[7] = 0;
}
static int has_ssid(const char *s,
                    const char *end) { /* "ssid", any case, in [s, end), but "bssid" (hashed) */
    for (const char *p = s; p + 4 <= end; ++p)
        if ((p[0] | 32) == 's' && (p[1] | 32) == 's' && (p[2] | 32) == 'i' && (p[3] | 32) == 'd' &&
            (p == s || (p[-1] | 32) != 'b'))
            return 1;
    return 0;
}
int wifi_scrub(const char *text, unsigned salt, char *out, int size) {
    int n = 0;
    if (size <= 0) return 0;
    for (const char *s = text; s && *s;) {
        const char *e = strchr(s, '\n');
        if (!e) e = s + strlen(s);
        if (!has_ssid(s, e)) {
            for (const char *p = s; p < e && n < size - 8;) {
                if (e - p >= 17 && is_mac(p)) {
                    wifi_hash(salt, p, out + n);
                    n += 7, p += 17;
                } else {
                    out[n++] = *p++;
                }
            }
            if (n < size - 1) out[n++] = '\n';
        }
        s = *e ? e + 1 : e;
    }
    out[n] = 0;
    return n;
}

/* ---- wpa_supplicant ---- */
/* the value of "key=" at a line's start in text, up to the line's end, into v; 1 found */
static int value(const char *text, const char *key, char *v, int n) {
    int k = (int)strlen(key);
    for (const char *s = text; s && *s;) {
        if (!strncmp(s, key, k) && s[k] == '=') {
            int i = 0;
            for (s += k + 1; *s && *s != '\n' && i < n - 1;) v[i++] = *s++;
            v[i] = 0;
            return 1;
        }
        s = strchr(s, '\n');
        if (s) ++s;
    }
    if (n > 0) v[0] = 0;
    return 0;
}
int wifi_state(const char *status, char *state, int n) {
    static const struct {
        const char *name;
        int ws;
    } names[] = { { "COMPLETED", WS_COMPLETED },         { "DISCONNECTED", WS_DISCONNECTED },
                  { "SCANNING", WS_SCANNING },           { "INACTIVE", WS_INACTIVE },
                  { "INTERFACE_DISABLED", WS_DISABLED }, { "DISABLED", WS_DISABLED } };
    char v[32];
    if (!value(status ? status : "", "wpa_state", v, sizeof v)) {
        if (n > 0) state[0] = 0;
        return WS_NONE;
    }
    snprintf(state, n, "%s", v);
    for (unsigned i = 0; i < sizeof names / sizeof names[0]; ++i)
        if (!strcmp(v, names[i].name)) return names[i].ws;
    return WS_JOINING; /* AUTHENTICATING, ASSOCIATING, ASSOCIATED, 4WAY_HANDSHAKE, GROUP_HANDSHAKE
                        */
}
int wifi_status_line(const char *status, unsigned salt, char *out, int size) {
    /* wpa_state first; ssid=, address=, uuid=, p2p_device_address= and ip_address= are never copied
     */
    static const char *const keys[] = {
        "wpa_state",       "freq", "mode", "key_mgmt", "pairwise_cipher", "group_cipher",
        "wifi_generation", "id"
    };
    char v[64], h[8];
    int n = 0;
    out[0] = 0;
    for (unsigned i = 0; i < sizeof keys / sizeof keys[0] && n < size; ++i)
        if (value(status, keys[i], v, sizeof v))
            n += snprintf(out + n, size - n, "%s%s=%s", n ? " " : "", i ? keys[i] : "state", v);
    if (n < size && value(status, "bssid", v, sizeof v) && strlen(v) >= 17 && is_mac(v))
        wifi_hash(salt, v, h), n += snprintf(out + n, size - n, "%sap=%s", n ? " " : "", h);
    if (n < size)
        n += snprintf(out + n, size - n, "%sip=%d", n ? " " : "",
                      value(status, "ip_address", v, sizeof v));
    return n < size ? n : size - 1;
}
/* the field'th tab-separated field of a line into v (n bytes), its length or -1 */
static int tab_field(const char *line, int field, char *v, int n) {
    const char *s = line;
    for (int i = 0; i < field; ++i) {
        while (*s && *s != '\t' && *s != '\n') ++s;
        if (*s != '\t') return -1;
        ++s;
    }
    int k = 0;
    while (*s && *s != '\t' && *s != '\n' && k < n - 1) v[k++] = *s++;
    v[k] = 0;
    return k;
}
static const char *next_line(const char *s) {
    s = strchr(s, '\n');
    return s ? s + 1 : 0;
}
/* a saved network's SSID (LIST_NETWORKS: id, ssid, bssid, flags) matches ssid, and it is not
 * [DISABLED] */
static int saved(const char *networks, const char *ssid) {
    char v[72], f[64];
    if (!networks || !ssid[0]) return 0;
    for (const char *l = next_line(networks); l && *l; l = next_line(l))
        if (tab_field(l, 1, v, sizeof v) >= 0 && !strcmp(v, ssid) &&
            (tab_field(l, 3, f, sizeof f) < 0 || !strstr(f, "[DISABLED]")))
            return 1;
    return 0;
}
void wifi_scan_count(const char *results, const char *networks, struct wifi_scan *s) {
    char v[72];
    memset(s, 0, sizeof *s);
    s->n = -1;
    if (!results || strncmp(results, "bssid", 5)) return; /* no answer, or FAIL */
    s->n = 0;
    for (const char *l = next_line(results); l && *l; l = next_line(l)) {
        if (tab_field(l, 1, v, sizeof v) <= 0) continue;
        int mhz = atoi(v), b = mhz < 3000 ? 0 : mhz < 5935 ? 1 : 2;
        int dbm = tab_field(l, 2, v, sizeof v) > 0 ? atoi(v) : 0;
        ++s->n, ++s->band[b];
        if (dbm && (!s->best[b] || dbm > s->best[b])) s->best[b] = dbm;
        if (tab_field(l, 4, v, sizeof v) >= 0 && saved(networks, v)) ++s->known;
    }
}
int wifi_scan_text(const struct wifi_scan *s, char *out, int size) {
    static const char *const name[3] = { "2.4", "5", "6" };
    int n = snprintf(out, size, "nets=%d", s->n);
    for (int b = 0; b < 3 && n < size; ++b)
        n += s->band[b] ? snprintf(out + n, size - n, " %s=%d/%d", name[b], s->band[b], s->best[b])
                        : snprintf(out + n, size - n, " %s=0", name[b]);
    if (n < size) n += snprintf(out + n, size - n, " known=%d", s->known);
    return n < size ? n : size - 1;
}

/* " key=VALUE" in msg (VALUE up to a space) into v; 1 found */
static int event_field(const char *msg, const char *key, char *v, int n) {
    char k[32];
    snprintf(k, sizeof k, " %s=", key);
    const char *s = strstr(msg, k);
    int i = 0;
    if (!s) return v[0] = 0, 0;
    for (s += strlen(k); *s && *s != ' ' && *s != '\n' && i < n - 1;) v[i++] = *s++;
    v[i] = 0;
    return 1;
}
/* out: "WORD" and each of keys present in msg, " key=value", an address field hashed */
static int event_line(const char *msg, unsigned salt, const char *word, const char *const *keys,
                      char *out, int size) {
    char v[48], h[8];
    int n = snprintf(out, size, "%s", word);
    for (; *keys && n < size; ++keys)
        if (event_field(msg, *keys, v, sizeof v)) {
            if (strlen(v) >= 17 && is_mac(v)) wifi_hash(salt, v, h), snprintf(v, sizeof v, "%s", h);
            n += snprintf(out + n, size - n, " %s=%s", *keys, v);
        }
    return n < size ? n : size - 1;
}
int wifi_event(const char *msg, unsigned salt, char *out, int size, int *reason, int *local) {
    static const char *const disc[] = { "bssid", "reason", "locally_generated", 0 };
    static const char *const rej[] = { "bssid", "status_code", "timeout", 0 };
    static const char *const auth[] = { "auth_type", "auth_transaction", "status_code", 0 };
    static const char *const temp[] = { "id", "auth_failures", "duration", "reason", 0 };
    static const char *const ids[] = { "id", 0 };
    static const char *const ret[] = { "ret", 0 };
    static const char *const regdom[] = { "init", "type", "alpha2", 0 };
    static const char *const none[] = { 0 };
    char v[24], h[8];
    *reason = -1, *local = 0;
    if (size > 0) out[0] = 0;
    if (msg[0] == '<') { /* the level, "<3>" */
        const char *e = strchr(msg, '>');
        if (e) msg = e + 1;
    }
    if (!strncmp(msg, "CTRL-EVENT-CONNECTED", 20)) {
        /* "CTRL-EVENT-CONNECTED - Connection to aa:bb:cc:dd:ee:ff completed [id=0 id_str=]" */
        const char *a = strstr(msg, "Connection to ");
        int n = snprintf(out, size, "ASSOC");
        if (a && strlen(a + 14) >= 17 && is_mac(a + 14))
            wifi_hash(salt, a + 14, h), n += snprintf(out + n, size - n, " ap=%s", h);
        const char *id = strstr(msg, "[id=");
        if (id && n < size) snprintf(out + n, size - n, " id=%d", atoi(id + 4));
        return WE_CONNECTED;
    }
    if (!strncmp(msg, "CTRL-EVENT-DISCONNECTED", 23)) {
        event_line(msg, salt, "DISASSOC", disc, out, size);
        if (event_field(msg, "reason", v, sizeof v)) *reason = atoi(v);
        *local = event_field(msg, "locally_generated", v, sizeof v) && v[0] == '1';
        return WE_DISCONNECTED;
    }
    if (!strncmp(msg, "CTRL-EVENT-TERMINATING", 22))
        return snprintf(out, size, "WPA terminating"), WE_TERMINATING;
    if (!strncmp(msg, "CTRL-EVENT-SCAN-STARTED", 23)) return WE_SCAN_STARTED;
    if (!strncmp(msg, "CTRL-EVENT-SCAN-RESULTS", 23)) return WE_SCAN_RESULTS;
    if (!strncmp(msg, "CTRL-EVENT-SCAN-FAILED", 22))
        return event_line(msg, salt, "SCAN-FAILED", ret, out, size), WE_SCAN_FAILED;
    static const struct {
        const char *ev, *word;
        const char *const *keys;
    } logged[] = { { "CTRL-EVENT-ASSOC-REJECT", "ASSOC-REJECT", rej },
                   { "CTRL-EVENT-AUTH-REJECT", "AUTH-REJECT", auth },
                   { "CTRL-EVENT-SSID-TEMP-DISABLED", "TEMP-DISABLED", temp },
                   { "CTRL-EVENT-SSID-REENABLED", "REENABLED", ids },
                   { "CTRL-EVENT-BEACON-LOSS", "BEACON-LOSS", none },
                   { "CTRL-EVENT-REGDOM-CHANGE", "REGDOM", regdom } };
    for (unsigned i = 0; i < sizeof logged / sizeof logged[0]; ++i)
        if (!strncmp(msg, logged[i].ev, strlen(logged[i].ev))) {
            event_line(msg, salt, logged[i].word, logged[i].keys, out, size);
            return WE_LOG;
        }
    return WE_NONE;
}

/* ---- the deaf radio ---- */
/* not joined though there is something to join: a failed try (WS_JOINING, then DISCONNECTED again)
 * counts too */
static int unjoined(int ws) {
    return ws == WS_DISCONNECTED || ws == WS_SCANNING || ws == WS_JOINING;
}
static int ended(struct wifi_watch *w, unsigned now, int why) {
    int was = w->deaf;
    unsigned at = w->deaf_at;
    memset(w, 0, sizeof *w);
    if (!was) return WA_NONE;
    w->end = why, w->lasted = now - at;
    return WA_END;
}
static int started(struct wifi_watch *w, unsigned now, int kind) {
    w->deaf = kind, w->deaf_at = w->snap_at = now | !now, w->snaps = 1;
    return WA_START;
}
int wifi_step(struct wifi_watch *w, const struct wifi_obs *o) {
    unsigned now = o->now;
    w->end = 0, w->lasted = 0;
    if (o->state == WS_NONE || o->state == WS_DISABLED || o->state == WS_INACTIVE)
        return ended(w, now, WIFI_END_OFF); /* Wi-Fi off, the radio off, or nothing saved to join */
    if (o->state == WS_COMPLETED) return ended(w, now, WIFI_END_ASSOC);
    if (o->nets == 0) {
        w->nojoin_since = 0;
        if (!w->empty_since) w->empty_since = now | !now;
        if ((int)(now - w->empty_since) >= WIFI_EMPTY_MS) {
            if (!w->deaf) return started(w, now, WIFI_DEAF_EMPTY);
            if (w->deaf == WIFI_DEAF_NOJOIN) {
                w->deaf = WIFI_DEAF_EMPTY, w->snap_at = now, ++w->snaps;
                return WA_EMPTY;
            }
        }
    } else if (o->nets > 0) {
        w->empty_since = 0;
        if (w->deaf == WIFI_DEAF_EMPTY)
            return ended(w, now, WIFI_END_SCAN); /* a scan found networks again */
        if (unjoined(o->state) && o->known > 0) {
            if (!w->nojoin_since) w->nojoin_since = now | !now;
            if (!w->deaf && (int)(now - w->nojoin_since) >= WIFI_NOJOIN_MS)
                return started(w, now, WIFI_DEAF_NOJOIN);
        } else if (!w->deaf) {
            w->nojoin_since = 0;
        }
    }
    if (w->deaf && (int)(now - w->snap_at) >= WIFI_REPEAT_MS) {
        w->snap_at = now, ++w->snaps;
        return WA_AGAIN;
    }
    return WA_NONE;
}

/* ---- the boot's evidence ---- */
int wifi_kmsg_keep(const char *msg) {
    static const char *const fail[] = {
        "allocation failure", "vmalloc", "Call Trace", "Oops", "BUG:", "WARNING:", "Out of memory"
    };
    static const char *const plain[] = { "8733", "rtw_", "rtl",  "RTW",  "rtk_",
                                         "wlan", "mmc1", "sdio", "SDIO", "insmod" };
    for (unsigned i = 0; i < sizeof fail / sizeof fail[0]; ++i)
        if (strstr(msg, fail[i])) return 2;
    for (unsigned i = 0; i < sizeof plain / sizeof plain[0]; ++i)
        if (strstr(msg, plain[i])) return 1;
    return 0;
}
static unsigned long hex_at(const char **s) {
    unsigned long v = 0;
    if ((*s)[0] == '0' && (*s)[1] == 'x') *s += 2;
    for (int d; (d = hexval((unsigned char)**s)) >= 0; ++*s) v = v * 16 + (unsigned long)d;
    return v;
}
void wifi_vmalloc_sum(const char *text, struct wifi_vm *v) {
    /* "0xc0000000-0xc0002000    8192 caller+0x10/0x20 pages=1 vmalloc" */
    unsigned long last = 0;
    memset(v, 0, sizeof *v);
    for (const char *l = text; l && *l; l = next_line(l)) {
        const char *s = l;
        if (s[0] != '0' || s[1] != 'x') continue;
        unsigned long lo = hex_at(&s);
        if (*s++ != '-') continue;
        unsigned long hi = hex_at(&s), size = 0;
        while (*s == ' ') ++s;
        while (*s >= '0' && *s <= '9') size = size * 10 + (unsigned long)(*s++ - '0');
        const char *e = strchr(l, '\n');
        int n = e ? (int)(e - l) : (int)strlen(l);
        char row[256];
        snprintf(row, sizeof row, "%.*s", n < 255 ? n : 255, l);
        if (!v->areas)
            v->lo = lo;
        else if (lo > last && lo - last > v->gap)
            v->gap = lo - last;
        if (hi > last) last = hi;
        ++v->areas, v->used += size;
        if (strstr(row, " vmalloc")) v->vmalloc += size;
        if (strstr(row, " ioremap")) v->ioremap += size;
    }
    v->hi = last;
}
int wifi_vm_text(const struct wifi_vm *v, char *out, int size) {
    return snprintf(out, size,
                    "vmalloc areas=%d used=%lukB vmalloc=%lukB ioremap=%lukB largest_gap=%lukB "
                    "from=0x%lx to=0x%lx",
                    v->areas, v->used >> 10, v->vmalloc >> 10, v->ioremap >> 10, v->gap >> 10,
                    v->lo, v->hi);
}
int wifi_mem_brief(const char *meminfo, char *out, int size) {
    static const char *const keys[] = { "MemFree:", "MemAvailable:", "Cached:", "Slab:",
                                        "VmallocUsed:" };
    int n = 0;
    out[0] = 0;
    for (unsigned i = 0; i < sizeof keys / sizeof keys[0] && n < size; ++i) {
        const char *s = meminfo;
        int k = (int)strlen(keys[i]);
        while (s && strncmp(s, keys[i], k)) s = next_line(s);
        if (!s) continue;
        for (s += k; *s == ' ';) ++s;
        n += snprintf(out + n, size - n, "%s%.*s=%d", n ? " " : "", k - 1, keys[i], atoi(s));
    }
    return n < size ? n : size - 1;
}
int wifi_lines_with(const char *text, const char *words, char *out, int size) {
    int n = 0, got = 0;
    out[0] = 0;
    for (const char *l = text; l && *l && n < size - 4; l = next_line(l)) {
        const char *e = strchr(l, '\n');
        if (!e) e = l + strlen(l);
        char row[256];
        int len = (int)(e - l) < 255 ? (int)(e - l) : 255, hit = 0;
        memcpy(row, l, len), row[len] = 0;
        for (const char *w = words; *w && !hit; w += strlen(w) + 1) hit = strstr(row, w) != 0;
        if (!hit) continue;
        if (got++) n += snprintf(out + n, size - n, " | ");
        int start = n;
        for (const char *p = row; *p && n < size - 1; ++p) {
            int blank = *p == ' ' || *p == '\t';
            if (!blank)
                out[n++] = *p;
            else if (n > start && out[n - 1] != ' ')
                out[n++] = ' ';
        }
        while (n > start && out[n - 1] == ' ') --n;
        out[n] = 0;
    }
    return got;
}

int wifi_line(char *out, int size, long wall, unsigned up_s, const char *what) {
    char b[48];
    snprintf(b, sizeof b, "up=%u ", up_s);
    int n = heat_note(out, size, wall, b);
    /* heat_note ends the line; what goes before its newline */
    if (n > 0 && out[n - 1] == '\n') --n;
    n += snprintf(out + n, size - n, "%s\n", what);
    if (n < size) return n;
    out[size - 2] = '\n', out[size - 1] = 0; /* cut, still one line */
    return size - 1;
}
int wifi_flush(card_log *l, const char *dir) {
    return log_flush(l, dir, "q2-wifi", WIFI_FILE_MAX, WIFI_FILES);
}
