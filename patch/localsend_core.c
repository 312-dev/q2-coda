/* LocalSend v2's protocol with no sockets or files (docs/localsend.md): JSON, a device's info, the devices
 * found, HTTP heads and chunked bodies, the receiving session and the sender's two bodies. q2send
 * (patch/localsend.c) calls it under its lock; test/unit/test_localsend.py builds it for this machine. */
#include "localsend_sys.h"
#include "localsend.h"

/* ---------------------------------------------------------------- JSON */

/* jsmn's walk, strict enough for what peers send: objects, arrays, strings with escapes, and primitives as
 * runs of the characters a number, true, false or null is made of. A key is a string token whose size is 1. */
int ls_json_parse(const char *js, int n, ls_tok *t, int nt) {
    int used = 0, stack[32], depth = 0;
    for (int i = 0; i < n; ++i) {
        char c = js[i];
        if (c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == ',' || c == ':') continue;
        if (used >= nt) return -1;
        ls_tok *k = &t[used];
        k->size = 0;
        if (c == '{' || c == '[') {
            if (depth == 32) return -1;
            k->type = c == '{' ? LS_J_OBJ : LS_J_ARR;
            k->start = i;
            k->end = -1;
            stack[depth++] = used++;
            continue;
        }
        if (c == '}' || c == ']') {
            if (!depth || t[stack[depth - 1]].type != (c == '}' ? LS_J_OBJ : LS_J_ARR)) return -1;
            t[stack[--depth]].end = i + 1;
            continue;
        }
        int s = i;
        if (c == '"') {
            for (++i; i < n && js[i] != '"'; ++i)
                if (js[i] == '\\') ++i;
                else if ((unsigned char)js[i] < 0x20) return -1;
            if (i >= n) return -1;
            k->type = LS_J_STR;
            k->start = s + 1;
            k->end = i;
        } else {
            while (i < n && strchr("+-.0123456789eEtruefalsn", js[i]) && js[i]) ++i;
            if (i == s) return -1;
            k->type = LS_J_PRIM;
            k->start = s;
            k->end = i--;
        }
        used++;
    }
    if (depth || !used) return -1;
    /* sizes: each token's direct children; in an object keys and values alternate */
    for (int p = 0; p < used; ++p) {
        if (t[p].type != LS_J_OBJ && t[p].type != LS_J_ARR) continue;
        int kids = 0;
        for (int q = p + 1; q < used && t[q].start < t[p].end; q = ls_json_skip(t, used, q)) ++kids;
        if (t[p].type == LS_J_OBJ) {
            if (kids % 2) return -1;
            kids /= 2;
        }
        t[p].size = kids;
    }
    return used;
}

/* The index after token i and everything inside it. */
int ls_json_skip(const ls_tok *t, int nt, int i) {
    int end = t[i].end, j = i + 1;
    if (t[i].type == LS_J_OBJ || t[i].type == LS_J_ARR)
        while (j < nt && t[j].start < end) ++j;
    return j;
}

/* The value of key in the object at obj, or -1. */
int ls_json_get(const char *js, const ls_tok *t, int nt, int obj, const char *key) {
    if (obj < 0 || obj >= nt || t[obj].type != LS_J_OBJ) return -1;
    int kl = (int)strlen(key);
    for (int i = obj + 1, k = 0; i < nt && k < t[obj].size; ++k) {
        int v = i + 1;
        if (v >= nt) return -1;
        if (t[i].type == LS_J_STR && t[i].end - t[i].start == kl && !memcmp(js + t[i].start, key, kl)) return v;
        i = ls_json_skip(t, nt, v);
    }
    return -1;
}

static int utf8(char *o, unsigned cp) {
    if (cp < 0x80) return o[0] = (char)cp, 1;
    if (cp < 0x800) return o[0] = (char)(0xc0 | cp >> 6), o[1] = (char)(0x80 | (cp & 63)), 2;
    if (cp < 0x10000)
        return o[0] = (char)(0xe0 | cp >> 12), o[1] = (char)(0x80 | (cp >> 6 & 63)), o[2] = (char)(0x80 | (cp & 63)), 3;
    o[0] = (char)(0xf0 | cp >> 18), o[1] = (char)(0x80 | (cp >> 12 & 63));
    o[2] = (char)(0x80 | (cp >> 6 & 63)), o[3] = (char)(0x80 | (cp & 63));
    return 4;
}

static int hex4(const char *s, unsigned *v) {
    *v = 0;
    for (int i = 0; i < 4; ++i) {
        char c = s[i];
        int d = c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
        if (d < 0) return -1;
        *v = *v << 4 | (unsigned)d;
    }
    return 0;
}

/* String token i unescaped into out (NUL-ended): its length, or -1 when it is not a string, holds a NUL or
 * does not fit. */
int ls_json_str(const char *js, const ls_tok *t, int i, char *out, int cap) {
    if (i < 0 || t[i].type != LS_J_STR) return -1;
    int o = 0;
    for (int p = t[i].start; p < t[i].end; ++p) {
        char tmp[4];
        int k = 1;
        tmp[0] = js[p];
        if (js[p] == '\\') {
            char e = js[++p];
            if (e == 'u') {
                unsigned cp, lo;
                if (p + 4 >= t[i].end || hex4(js + p + 1, &cp)) return -1;
                p += 4;
                if (cp >= 0xd800 && cp < 0xdc00) {
                    if (p + 6 >= t[i].end || js[p + 1] != '\\' || js[p + 2] != 'u' || hex4(js + p + 3, &lo) ||
                        lo < 0xdc00 || lo >= 0xe000)
                        return -1;
                    p += 6;
                    cp = 0x10000 + ((cp - 0xd800) << 10) + (lo - 0xdc00);
                } else if (cp >= 0xdc00 && cp < 0xe000)
                    return -1;
                if (!cp) return -1;
                k = utf8(tmp, cp);
            } else {
                const char *from = "\"\\/bfnrt", *to = "\"\\/\b\f\n\r\t", *at = strchr(from, e);
                if (!at || !e) return -1;
                tmp[0] = to[at - from];
            }
        }
        if (o + k >= cap) return -1;
        memcpy(out + o, tmp, k);
        o += k;
    }
    out[o] = 0;
    return o;
}

int ls_json_num(const char *js, const ls_tok *t, int i, long long *out) {
    if (i < 0 || t[i].type != LS_J_PRIM) return -1;
    char b[24];
    int n = t[i].end - t[i].start;
    if (n <= 0 || n >= (int)sizeof b) return -1;
    memcpy(b, js + t[i].start, n);
    b[n] = 0;
    char *e;
    long long v = strtoll(b, &e, 10);
    if (*e || (b[0] != '-' && (b[0] < '0' || b[0] > '9'))) return -1;
    *out = v;
    return 0;
}

int ls_json_bool(const char *js, const ls_tok *t, int i) {
    if (i < 0 || t[i].type != LS_J_PRIM) return -1;
    int n = t[i].end - t[i].start;
    if (n == 4 && !memcmp(js + t[i].start, "true", 4)) return 1;
    if (n == 5 && !memcmp(js + t[i].start, "false", 5)) return 0;
    return -1;
}

/* s as a JSON string with its quotes: its length, or -1 when it does not fit. Bytes from 0x80 pass as they are
 * (UTF-8); control characters are \u escapes. */
int ls_json_esc(char *out, int cap, const char *s) {
    int o = 0;
    if (cap < 3) return -1;
    out[o++] = '"';
    for (; *s; ++s) {
        unsigned char c = (unsigned char)*s;
        char tmp[8];
        int k = 1;
        tmp[0] = (char)c;
        if (c == '"' || c == '\\') tmp[0] = '\\', tmp[1] = (char)c, k = 2;
        else if (c < 0x20) k = snprintf(tmp, sizeof tmp, "\\u%04x", c);
        if (o + k + 2 > cap) return -1;
        memcpy(out + o, tmp, k);
        o += k;
    }
    out[o++] = '"';
    out[o] = 0;
    return o;
}

/* ---------------------------------------------------------------- info */

static void str_field(const char *js, const ls_tok *t, int nt, int obj, const char *key, char *out, int cap) {
    if (ls_json_str(js, t, ls_json_get(js, t, nt, obj, key), out, cap) < 0) out[0] = 0;
}

int ls_info_parse(const char *js, const ls_tok *t, int nt, int obj, ls_info *o) {
    memset(o, 0, sizeof *o);
    str_field(js, t, nt, obj, "alias", o->alias, sizeof o->alias);
    if (!o->alias[0]) return -1;
    str_field(js, t, nt, obj, "deviceModel", o->model, sizeof o->model);
    str_field(js, t, nt, obj, "deviceType", o->type, sizeof o->type);
    str_field(js, t, nt, obj, "fingerprint", o->fp, sizeof o->fp);
    str_field(js, t, nt, obj, "version", o->version, sizeof o->version);
    char proto[8];
    str_field(js, t, nt, obj, "protocol", proto, sizeof proto);
    o->https = strcmp(proto, "http") != 0;
    long long port;
    o->port = ls_json_num(js, t, ls_json_get(js, t, nt, obj, "port"), &port) || port <= 0 || port > 65535
                  ? LS_PORT
                  : (int)port;
    o->download = ls_json_bool(js, t, ls_json_get(js, t, nt, obj, "download")) == 1;
    o->announce = ls_json_bool(js, t, ls_json_get(js, t, nt, obj, "announce"));
    return 0;
}

int ls_info_text(const char *js, int n, ls_info *out) {
    ls_tok t[64];
    int nt = ls_json_parse(js, n, t, 64);
    if (nt < 1 || t[0].type != LS_J_OBJ) return -1;
    return ls_info_parse(js, t, nt, 0, out);
}

int ls_info_json(char *out, int cap, const ls_info *me, int with_port, int announce) {
    char a[2 * LS_ALIAS + 8], m[80];
    if (ls_json_esc(a, sizeof a, me->alias) < 0 || ls_json_esc(m, sizeof m, me->model) < 0) return -1;
    int n = snprintf(out, cap, "{\"alias\":%s,\"version\":\"" LS_VERSION "\",\"deviceModel\":%s,"
                     "\"deviceType\":\"%s\",\"fingerprint\":\"%s\"", a, m, me->type[0] ? me->type : "headless", me->fp);
    if (n < 0 || n >= cap) return -1;
    if (with_port) n += snprintf(out + n, cap - n, ",\"port\":%d,\"protocol\":\"https\"", me->port);
    if (n >= cap) return -1;
    n += snprintf(out + n, cap - n, ",\"download\":false%s}", announce < 0 ? "" : announce ? ",\"announce\":true" : ",\"announce\":false");
    return n < cap ? n : -1;
}

/* ---------------------------------------------------------------- peers */

int ls_peer_find(const ls_peer *tab, int n, const char *fp) {
    for (int i = 0; i < n; ++i)
        if (!strcmp(tab[i].info.fp, fp)) return i;
    return -1;
}

int ls_peer_seen(ls_peer *tab, int *n, const ls_info *i, const char *ip, uint32_t now, const char *self) {
    if (!i->fp[0] || !strcmp(i->fp, self)) return -1;
    int k = ls_peer_find(tab, *n, i->fp);
    if (k < 0) {
        if (*n < LS_MAX_PEERS)
            k = (*n)++;
        else {
            k = 0;
            for (int j = 1; j < *n; ++j)
                if ((int32_t)(tab[j].seen_ms - tab[k].seen_ms) < 0) k = j;
        }
    }
    tab[k].info = *i;
    snprintf(tab[k].ip, sizeof tab[k].ip, "%s", ip);
    tab[k].seen_ms = now;
    return k;
}

int ls_peer_expire(ls_peer *tab, int *n, uint32_t now) {
    int gone = 0;
    for (int i = 0; i < *n;)
        if (now - tab[i].seen_ms > LS_PEER_TTL_MS) {
            memmove(tab + i, tab + i + 1, (*n - i - 1) * sizeof *tab);
            --*n, ++gone;
        } else
            ++i;
    return gone;
}

/* ---------------------------------------------------------------- HTTP */

static int lower(int c) { return c >= 'A' && c <= 'Z' ? c + 32 : c; }

static int head_is(const char *a, int n, const char *name) {
    int k = (int)strlen(name);
    if (n != k) return 0;
    for (int i = 0; i < k; ++i)
        if (lower(a[i]) != name[i]) return 0;
    return 1;
}

static int copy(char *out, int cap, const char *s, int n) {
    if (n >= cap) return -1;
    memcpy(out, s, n);
    out[n] = 0;
    return 0;
}

int ls_http_head(const char *buf, int n, ls_http *h, int resp) {
    int end = -1;
    for (int i = 3; i < n; ++i)
        if (buf[i] == '\n' && buf[i - 1] == '\r' && buf[i - 2] == '\n' && buf[i - 3] == '\r') {
            end = i + 1;
            break;
        }
    if (end < 0) return n >= 16384 ? -1 : 0;
    memset(h, 0, sizeof *h);
    h->length = -1;
    const char *p = buf, *eol = strstr(p, "\r\n");
    if (!eol || eol - p > 1100) return -1;
    if (resp) {
        if (eol - p < 12 || memcmp(p, "HTTP/1.", 7)) return -1;
        h->status = (p[9] - '0') * 100 + (p[10] - '0') * 10 + (p[11] - '0');
        if (h->status < 100 || h->status > 599) return -1;
    } else {
        const char *sp = memchr(p, ' ', eol - p), *sp2 = sp ? memchr(sp + 1, ' ', eol - sp - 1) : 0;
        if (!sp || !sp2 || copy(h->method, sizeof h->method, p, (int)(sp - p))) return -1;
        const char *tgt = sp + 1, *q = memchr(tgt, '?', sp2 - tgt);
        if (copy(h->path, sizeof h->path, tgt, (int)((q ? q : sp2) - tgt))) return -1;
        if (q && copy(h->query, sizeof h->query, q + 1, (int)(sp2 - q - 1))) return -1;
        if (memcmp(sp2 + 1, "HTTP/1.", 7)) return -1;
    }
    for (p = eol + 2; p < buf + end - 2; p = eol + 2) {
        eol = strstr(p, "\r\n");
        const char *colon = memchr(p, ':', eol - p);
        if (!colon) return -1;
        const char *v = colon + 1;
        while (v < eol && (*v == ' ' || *v == '\t')) ++v;
        int vn = (int)(eol - v);
        while (vn && (v[vn - 1] == ' ' || v[vn - 1] == '\t')) --vn;
        int kn = (int)(colon - p);
        if (head_is(p, kn, "content-length")) {
            char b[24];
            if (copy(b, sizeof b, v, vn)) return -1;
            char *e;
            h->length = strtoll(b, &e, 10);
            if (*e || h->length < 0 || !vn) return -1;
        } else if (head_is(p, kn, "transfer-encoding")) {
            h->chunked = vn == 7 && head_is(v, 7, "chunked");
            if (!h->chunked) return -1; /* no other coding is ever sent */
        } else if (head_is(p, kn, "content-type"))
            copy(h->ctype, sizeof h->ctype, v, vn < (int)sizeof h->ctype ? vn : (int)sizeof h->ctype - 1);
    }
    if (h->chunked) h->length = -1;
    return end;
}

static int unhex(int c) {
    return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
}

int ls_query(const char *q, const char *key, char *out, int cap) {
    int kl = (int)strlen(key);
    for (const char *p = q; p && *p; p = strchr(p, '&') ? strchr(p, '&') + 1 : 0) {
        if (strncmp(p, key, kl) || p[kl] != '=') continue;
        int o = 0;
        for (const char *v = p + kl + 1; *v && *v != '&'; ++v) {
            int c = (unsigned char)*v;
            if (c == '%') {
                int a = unhex(v[1]), b = a < 0 ? -1 : unhex(v[2]);
                if (b < 0) return -1;
                c = a << 4 | b;
                v += 2;
            } else if (c == '+')
                c = ' ';
            if (!c || o + 1 >= cap) return -1;
            out[o++] = (char)c;
        }
        out[o] = 0;
        return o;
    }
    return -1;
}

/* States: the size line, the data, the CRLF after it, the trailer lines, done. */
enum { CH_SIZE, CH_DATA, CH_CRLF, CH_TRAILER, CH_DONE };

int ls_chunk_feed(ls_chunk *c, unsigned char *buf, int n) {
    int o = 0;
    for (int i = 0; i < n && c->st != CH_DONE;) {
        if (c->st == CH_DATA) {
            int k = n - i;
            if ((unsigned long long)k > c->left) k = (int)c->left;
            memmove(buf + o, buf + i, k);
            o += k, i += k;
            c->left -= k;
            if (!c->left) c->st = CH_CRLF, c->ln = 0;
            continue;
        }
        char ch = (char)buf[i++];
        if (c->st == CH_CRLF) {
            if (ch != (c->ln ? '\n' : '\r')) return -1;
            if (c->ln++) c->st = CH_SIZE, c->ln = 0;
            continue;
        }
        if (ch != '\n') {
            if (c->ln < (int)sizeof c->line - 1) c->line[c->ln++] = ch;
            else if (c->st == CH_SIZE) return -1;
            continue;
        }
        int len = c->ln && c->line[c->ln - 1] == '\r' ? c->ln - 1 : c->ln;
        c->ln = 0;
        if (c->st == CH_TRAILER) {
            if (!len) c->st = CH_DONE;
            continue;
        }
        unsigned long long v = 0;
        int d = 0, k = 0;
        for (; k < len && (d = unhex(c->line[k])) >= 0; ++k) {
            if (v >> 59) return -1;
            v = v << 4 | (unsigned)d;
        }
        if (!k || (k < len && c->line[k] != ';' && c->line[k] != ' ')) return -1;
        c->left = v;
        c->st = v ? CH_DATA : CH_TRAILER;
    }
    return o;
}

int ls_chunk_done(const ls_chunk *c) { return c->st == CH_DONE; }

/* ---------------------------------------------------------------- files and the session */

int ls_safe_name(const char *in, char *out, int cap) {
    if (cap > LS_PATH) cap = LS_PATH;
    int o = 0;
    if (!*in || *in == '/') return -1;
    while (*in) {
        const char *s = in, *e = strchr(in, '/');
        if (!e) e = in + strlen(in);
        int n = (int)(e - s);
        if (!n || *s == '.') return -1;
        int start = o;
        if (o) {
            if (o + 1 >= cap) return -1;
            out[o++] = '/';
            start = o;
        }
        if (n > 255) return -1;
        for (int k = 0; k < n; ++k) {
            unsigned char c = (unsigned char)s[k];
            if (c < 0x20 || c == 0x7f) return -1;
            if (strchr("\\:*?\"<>|", c)) c = '_';
            if (o + 1 >= cap) return -1;
            out[o++] = (char)c;
        }
        while (o > start && (out[o - 1] == ' ' || out[o - 1] == '.')) --o;
        if (o == start) return -1;
        in = *e ? e + 1 : e;
        if (*e && !*in) return -1; /* a trailing slash names a folder, not a file */
    }
    out[o] = 0;
    return o;
}

static void hex(char *out, const unsigned char *b, int n) {
    for (int i = 0; i < n; ++i) snprintf(out + 2 * i, 3, "%02x", b[i]);
}

void ls_session_free(ls_session *s) {
    free(s->f);
    memset(s, 0, sizeof *s);
}

int ls_prepare_parse(const char *js, int n, ls_session *s) {
    memset(s, 0, sizeof *s);
    int cap = 64 + 12 * LS_MAX_FILES;
    ls_tok *t = malloc(cap * sizeof *t);
    if (!t) return 500;
    int nt = ls_json_parse(js, n, t, cap), code = 400;
    int info = nt > 0 && t[0].type == LS_J_OBJ ? ls_json_get(js, t, nt, 0, "info") : -1;
    int files = nt > 0 ? ls_json_get(js, t, nt, 0, "files") : -1;
    if (info < 0 || files < 0 || t[files].type != LS_J_OBJ || ls_info_parse(js, t, nt, info, &s->from) ||
        t[files].size < 1 || t[files].size > LS_MAX_FILES)
        goto out;
    s->f = calloc(t[files].size, sizeof *s->f);
    if (!s->f) {
        code = 500;
        goto out;
    }
    for (int i = files + 1, k = 0; k < t[files].size; ++k) {
        int v = i + 1;
        if (v >= nt || t[v].type != LS_J_OBJ) goto out;
        ls_file *f = &s->f[s->n++];
        char raw[2 * LS_PATH];
        long long size;
        if (ls_json_str(js, t, ls_json_get(js, t, nt, v, "id"), f->id, sizeof f->id) < 1 ||
            ls_json_str(js, t, ls_json_get(js, t, nt, v, "fileName"), raw, sizeof raw) < 1 ||
            ls_json_num(js, t, ls_json_get(js, t, nt, v, "size"), &size) || size < 0)
            goto out;
        for (int j = 0; j < s->n - 1; ++j)
            if (!strcmp(s->f[j].id, f->id)) goto out;
        f->size = size;
        str_field(js, t, nt, v, "fileType", f->type, sizeof f->type);
        str_field(js, t, nt, v, "sha256", f->sha, sizeof f->sha);
        if (ls_safe_name(raw, f->name, sizeof f->name) > 0) {
            unsigned char r[16];
            ls_random(r, sizeof r);
            hex(f->token, r, 16);
            f->state = LS_F_WANT;
            s->want++;
            s->total += size;
        }
        i = ls_json_skip(t, nt, v);
    }
    unsigned char r[16];
    ls_random(r, sizeof r);
    hex(s->id, r, 16);
    code = 0;
out:
    free(t);
    if (code) ls_session_free(s);
    return code;
}

int ls_prepare_reply(const ls_session *s, char *out, int cap) {
    int n = snprintf(out, cap, "{\"sessionId\":\"%s\",\"files\":{", s->id), first = 1;
    for (int i = 0; i < s->n && n < cap; ++i) {
        if (s->f[i].state == LS_F_SKIP) continue;
        char id[2 * LS_ID + 8];
        if (ls_json_esc(id, sizeof id, s->f[i].id) < 0) return -1;
        n += snprintf(out + n, cap - n, "%s%s:\"%s\"", first ? "" : ",", id, s->f[i].token);
        first = 0;
    }
    if (n < cap) n += snprintf(out + n, cap - n, "}}");
    return n < cap ? n : -1;
}

int ls_upload_lookup(const ls_session *s, const char *query, int *idx) {
    char sid[LS_TOKEN + 8], fid[LS_ID], tok[LS_TOKEN + 8];
    if (ls_query(query, "sessionId", sid, sizeof sid) < 0 || ls_query(query, "fileId", fid, sizeof fid) < 0 ||
        ls_query(query, "token", tok, sizeof tok) < 0)
        return 400;
    if (!s->id[0] || strcmp(sid, s->id)) return 403;
    for (int i = 0; i < s->n; ++i) {
        if (strcmp(s->f[i].id, fid)) continue;
        if (s->f[i].state == LS_F_SKIP || strcmp(s->f[i].token, tok)) return 403;
        if (s->f[i].state == LS_F_DONE || s->f[i].state == LS_F_BUSY) return 409;
        *idx = i;
        return 0;
    }
    return 403;
}

int ls_prepare_body(char *out, int cap, const ls_info *me, const ls_file *f, int n) {
    int k = snprintf(out, cap, "{\"info\":");
    int m = ls_info_json(out + k, cap - k, me, 1, -1);
    if (m < 0) return -1;
    k += m;
    k += snprintf(out + k, cap - k, ",\"files\":{");
    for (int i = 0; i < n && k < cap; ++i) {
        char id[2 * LS_ID + 8], name[2 * LS_PATH + 8], type[112];
        if (ls_json_esc(id, sizeof id, f[i].id) < 0 || ls_json_esc(name, sizeof name, f[i].name) < 0 ||
            ls_json_esc(type, sizeof type, f[i].type[0] ? f[i].type : "application/octet-stream") < 0)
            return -1;
        k += snprintf(out + k, cap - k, "%s%s:{\"id\":%s,\"fileName\":%s,\"size\":%lld,\"fileType\":%s", i ? "," : "",
                      id, id, name, f[i].size, type);
        if (k < cap && f[i].sha[0]) k += snprintf(out + k, cap - k, ",\"sha256\":\"%s\"", f[i].sha);
        if (k < cap) k += snprintf(out + k, cap - k, "}");
    }
    if (k < cap) k += snprintf(out + k, cap - k, "}}");
    return k < cap ? k : -1;
}

int ls_prepare_answer(const char *js, int n, ls_session *s) {
    int cap = 8 + 2 * (s->n + 2);
    ls_tok *t = malloc(cap * sizeof *t);
    if (!t) return -1;
    int nt = ls_json_parse(js, n, t, cap), rc = -1;
    int sid = nt > 0 ? ls_json_get(js, t, nt, 0, "sessionId") : -1;
    int files = nt > 0 ? ls_json_get(js, t, nt, 0, "files") : -1;
    if (ls_json_str(js, t, sid, s->id, sizeof s->id) > 0 && files >= 0 && t[files].type == LS_J_OBJ) {
        s->want = 0;
        for (int i = 0; i < s->n; ++i) {
            ls_file *f = &s->f[i];
            if (ls_json_str(js, t, ls_json_get(js, t, nt, files, f->id), f->token, sizeof f->token) > 0)
                f->state = LS_F_WANT, s->want++;
            else
                f->state = LS_F_SKIP;
        }
        rc = 0;
    }
    free(t);
    return rc;
}
