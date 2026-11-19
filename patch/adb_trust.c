/* Which computers get adb (docs/adb.md#authorization, docs/oled-design.md#adb), a --dev build's:
 *
 * - The prompt. adbd asks about a computer whose key it does not hold (usb_ui.c auth_poll, its
 *   socket); adb_prompt_open asks on the confirmation card's wide form over whatever shows: the
 *   laptop glyph, "Allow this computer?", "<user@host> wants developer access over USB." (the key's
 *   comment), the key's short fingerprint, the switch "Always allow this computer" (on), then Deny
 *   and Allow, Allow lit. Allow answers OK, first adding the key to AUTH_KEYS and its day to
 *   AUTH_ADDED when the switch is on (else this connection alone), then the status pill
 *   "<host> allowed" for 2 s. Deny, Return or ADB_ASK_MS with no answer answers NO: nothing lets a
 *   computer in but a press on the Q2.
 * - Settings > General > Developer Options: Trusted Computers and how many. Trusted Computers lists
 *   AUTH_KEYS' computers with the day each was added; Centre on one asks, then removes it, and
 *   Remove All (red) all of them. A computer removed keeps a connection it has until the cable
 *   comes out; adbd reads AUTH_KEYS again at each connection, so the next one asks. The card's own
 *   adb_keys (/mnt/mmc) is the developer's file and is not listed. */
#include "navigation.h"

#if Q2POD_DEV
#define AUTH_KEYS "/mnt/data/adb_keys"        /* tools/adbd.patch ADB_KEYS */
#define AUTH_ADDED "/mnt/data/adb_keys.added" /* id[8] when[4] little-endian per computer */
#define AUTH_ID 8                             /* a computer's id: its key's MD5, the first bytes */
#define AUTH_REC (AUTH_ID + 4)
#define AUTH_LINE_MAX 2048 /* a 2048-bit adbkey.pub line is about 720 bytes */
#define ADB_ASK_MS 30000   /* the card's wait before it answers Deny */
#define ADB_TRUST_MAX 12   /* computers listed */
#define ADB_NAME_MAX 40
#define ADB_CAP_H 26 /* a caption's gap item under the rows */
#define ADB_CAP_PX TYPE(13)
#define ADB_CAP_LINE 17

typedef struct {
    unsigned char id[AUTH_ID];
    char name[ADB_NAME_MAX];
    unsigned when; /* wall clock; 0 unknown */
} adb_computer;

static struct {
    const char *line; /* the key adbd asked about (usb_ui.c's), while the card is up */
    unsigned char id[AUTH_ID];
    char who[ADB_NAME_MAX], note[48];
    void *dev, *dev_view, *trust, *trust_view, *cap[2];
    adb_computer pc[ADB_TRUST_MAX];
    int n, removing; /* the computers listed; the one the card asks to remove, -1 all */
} at __attribute__((section(".scratch")));

/* ---- keys ---- */

static int b64(int c) {
    return c >= 'A' && c <= 'Z'   ? c - 'A'
           : c >= 'a' && c <= 'z' ? c - 'a' + 26
           : c >= '0' && c <= '9' ? c - '0' + 52
           : c == '+'             ? 62
           : c == '/'             ? 63
                                  : -1;
}
/* The MD5 of line's key (its first word, base64-decoded), as Android's fingerprint takes it. */
static void key_md5(const char *line, unsigned char md[16]) {
    unsigned char raw[600];
    unsigned n = 0, acc = 0, bits = 0;
    for (const char *p = line; *p && *p != ' ' && *p != '\t' && n < sizeof raw; ++p) {
        int v = b64((unsigned char)*p);
        if (v < 0) continue;
        acc = acc << 6 | (unsigned)v, bits += 6;
        if (bits >= 8) raw[n++] = (unsigned char)(acc >> (bits -= 8));
    }
    unsigned ctx[24];
    MD5_Init(ctx);
    MD5_Update(ctx, raw, n);
    MD5_Final(md, ctx);
}

/* line's comment (adb writes user@host after the key) into out, or "" */
static void key_name(const char *line, char *out, unsigned n) {
    const char *c = line;
    while (*c && *c != ' ' && *c != '\t') ++c;
    while (*c == ' ' || *c == '\t') ++c;
    unsigned k = 0;
    while (c[k] && c[k] != '\r' && c[k] != '\n') ++k;
    while (k && c[k - 1] == ' ') --k;
    tk_snprintf(out, n, "%.*s", (int)k, c);
}

/* Each line of AUTH_KEYS to each(line, ctx), read a block at a time; a line longer than
 * AUTH_LINE_MAX is no adb key (adbd refuses it too) and is passed cut. */
static void keys_each(void (*each)(const char *line, void *ctx), void *ctx) {
    void *f = fopen(AUTH_KEYS, "rb");
    if (!f) return;
    char block[256], line[AUTH_LINE_MAX];
    unsigned len = 0, n;
    while ((n = fread(block, 1, sizeof block, f)) > 0)
        for (unsigned i = 0; i < n; ++i) {
            if (block[i] != '\n') {
                if (len < sizeof line - 1) line[len++] = block[i];
                continue;
            }
            line[len] = 0, len = 0;
            each(line, ctx);
        }
    fclose(f);
    if (len) line[len] = 0, each(line, ctx);
}

/* AUTH_ADDED's records into rec (max of them); their count */
static int added_read(unsigned char *rec, int max) {
    void *f = fopen(AUTH_ADDED, "rb");
    if (!f) return 0;
    int n = (int)(fread(rec, AUTH_REC, (unsigned)max, f));
    fclose(f);
    return n < 0 ? 0 : n;
}
static unsigned rec_when(const unsigned char *r) {
    return r[AUTH_ID] | r[AUTH_ID + 1] << 8 | r[AUTH_ID + 2] << 16 | (unsigned)r[AUTH_ID + 3] << 24;
}

/* id added now: its record in AUTH_ADDED, any older one for it dropped */
static void added_note(const unsigned char *id) {
    unsigned char rec[(ADB_TRUST_MAX + 1) * AUTH_REC];
    int n = added_read(rec, ADB_TRUST_MAX), k = 0;
    for (int i = 0; i < n; ++i)
        if (memcmp(rec + i * AUTH_REC, id, AUTH_ID))
            memmove(rec + k++ * AUTH_REC, rec + i * AUTH_REC, AUTH_REC);
    unsigned t = (unsigned)wall_time();
    unsigned char *r = rec + k++ * AUTH_REC;
    memcpy(r, id, AUTH_ID);
    for (int b = 0; b < 4; ++b) r[AUTH_ID + b] = (unsigned char)(t >> 8 * b);
    void *f = fopen(AUTH_ADDED ".tmp", "wb");
    if (!f) return;
    int ok = fwrite(rec, AUTH_REC, (unsigned)k, f) == (unsigned)k;
    if (fclose(f) || !ok || rename(AUTH_ADDED ".tmp", AUTH_ADDED)) unlink(AUTH_ADDED ".tmp");
}

/* The computers AUTH_KEYS holds, at most ADB_TRUST_MAX, each once, with the day AUTH_ADDED has. */
static void list_one(const char *line, void *ctx) {
    (void)ctx;
    unsigned char md[16];
    if (at.n >= ADB_TRUST_MAX || b64((unsigned char)*line) < 0) return;
    key_md5(line, md);
    for (int i = 0; i < at.n; ++i)
        if (!memcmp(at.pc[i].id, md, AUTH_ID)) return;
    adb_computer *c = at.pc + at.n++;
    memcpy(c->id, md, AUTH_ID);
    key_name(line, c->name, sizeof c->name);
    if (!c->name[0]) tk_snprintf(c->name, sizeof c->name, "%s", "Unnamed computer");
    c->when = 0;
}
static void list_read(void) {
    unsigned char rec[ADB_TRUST_MAX * AUTH_REC];
    at.n = 0;
    keys_each(list_one, 0);
    int n = added_read(rec, ADB_TRUST_MAX);
    for (int i = 0; i < at.n; ++i)
        for (int k = 0; k < n; ++k)
            if (!memcmp(rec + k * AUTH_REC, at.pc[i].id, AUTH_ID))
                at.pc[i].when = rec_when(rec + k * AUTH_REC);
}

/* AUTH_KEYS again without the lines of id (all of them with 0), AUTH_ADDED's record with it. */
static void drop_one(const char *line, void *f) {
    unsigned char md[16];
    if (!*line) return;
    key_md5(line, md);
    if (!memcmp(md, at.id, AUTH_ID)) return;
    fwrite(line, 1, strlen(line), f);
    fwrite("\n", 1, 1, f);
}
static void keys_remove(const unsigned char *id) {
    if (!id) {
        unlink(AUTH_KEYS);
        unlink(AUTH_ADDED);
        return;
    }
    memcpy(at.id, id, AUTH_ID);
    void *f = fopen(AUTH_KEYS ".tmp", "wb");
    if (!f) return;
    keys_each(drop_one, f);
    if (fclose(f) || rename(AUTH_KEYS ".tmp", AUTH_KEYS)) {
        unlink(AUTH_KEYS ".tmp");
        return;
    }
    unsigned char rec[ADB_TRUST_MAX * AUTH_REC];
    int n = added_read(rec, ADB_TRUST_MAX), k = 0;
    for (int i = 0; i < n; ++i)
        if (memcmp(rec + i * AUTH_REC, id, AUTH_ID))
            memmove(rec + k++ * AUTH_REC, rec + i * AUTH_REC, AUTH_REC);
    f = fopen(AUTH_ADDED ".tmp", "wb");
    if (!f) return;
    int ok = fwrite(rec, AUTH_REC, (unsigned)k, f) == (unsigned)k;
    if (fclose(f) || !ok || rename(AUTH_ADDED ".tmp", AUTH_ADDED)) unlink(AUTH_ADDED ".tmp");
}

/* ---- the prompt ---- */

static void trust_fill(void);
static void dev_fill(void);

/* The card's answer: Allow (row 1), with the switch on kept in AUTH_KEYS before adbd hears OK. */
static void prompt_answer(void *ctx, int row, int always) {
    (void)ctx;
    const char *line = at.line;
    at.line = 0;
    if (row != 1 || !line) {
        usb_ui_auth_answer("NO");
        return;
    }
    if (always) {
        void *f = fopen(AUTH_KEYS, "ab");
        if (f) {
            fwrite(line, 1, strlen(line), f);
            fwrite("\n", 1, 1, f);
            fclose(f);
            added_note(at.id);
        }
    }
    usb_ui_auth_answer("OK");
    char host[ADB_NAME_MAX + 16];
    const char *h = at.who; /* the host, after user@ */
    while (*h && *h != '@') ++h;
    tk_snprintf(host, sizeof host, "%s allowed",
                *h && h[1]  ? h + 1
                : at.who[0] ? at.who
                            : "Computer");
    status_pill_show("adbpill", host);
    if (at.trust) trust_fill();
    if (at.dev) dev_fill();
}

/* adbd's question about line (a key and its comment): the card, or 0 when another card is up
 * (adbd is answered NO by the caller, and the computer asks again). */
int adb_prompt_open(const char *line) {
    static const char *const rows[] = { "Deny", "Allow" };
    unsigned char md[16];
    key_md5(line, md);
    memcpy(at.id, md, AUTH_ID);
    key_name(line, at.who, sizeof at.who);
    tk_snprintf(at.note, sizeof at.note, "Key %02X:%02X:%02X:%02X \xe2\x80\xa6 %02X:%02X", md[0],
                md[1], md[2], md[3], md[14], md[15]);
    at.line = line;
    awake_screen();
    int shown = confirm(&(confirm_t){ .title = "Allow this computer?",
                                      .name = at.who[0] ? at.who : "A computer",
                                      .body = "%s wants developer access over USB.",
                                      .rows = rows,
                                      .n_rows = 2,
                                      .kind = CONFIRM_NEUTRAL, /* opens on Allow; Return is Deny */
                                      .back = 0,
                                      .toggle = "Always allow this computer",
                                      .toggle_on = 1,
                                      .timeout_ms = ADB_ASK_MS,
                                      .timeout_row = 0,
                                      .answer = prompt_answer,
                                      .glyph = ipod_light() ? "adbcard_l" : "adbcard_d",
                                      .note = at.note,
                                      .across = 1 });
    if (!shown) at.line = 0;
    return shown;
}

/* adbd went with the card up: it closes, answering nothing. */
void adb_prompt_close(void) {
    if (!at.line) return;
    at.line = 0;
    confirm_close();
}

/* ---- Developer Options and Trusted Computers ---- */

static int trust_closed(void *ctx, void *event) {
    (void)event;
    if (ctx == at.trust) at.trust = at.trust_view = at.cap[0] = at.cap[1] = (void *)0;
    if (at.dev) dev_fill();
    return 0;
}

static void remove_answer(void *ctx, int row, int toggle_on) {
    (void)ctx, (void)toggle_on;
    if (row != 1) return;
    keys_remove(at.removing < 0 ? (const unsigned char *)0 : at.pc[at.removing].id);
    if (at.trust) trust_fill();
}

/* A computer's row (index i) or Remove All (index n): asks on the card, Cancel first. */
static int trust_click(void *ctx, void *event) {
    (void)event;
    static const char *const one[] = { "Cancel", "Remove" }, *const all[] = { "Cancel",
                                                                              "Remove All" };
    int i = (int)(long)ctx;
    if (i < 0 || i > at.n) return 0;
    at.removing = i < at.n ? i : -1;
    confirm(&(confirm_t){ .title = i < at.n ? "Remove %s?" : "Remove All Computers?",
                          .name = i < at.n ? at.pc[i].name : 0,
                          .body = i < at.n ? "It asks again the next time it connects."
                                           : "Each asks again the next time it connects.",
                          .rows = i < at.n ? one : all,
                          .n_rows = 2,
                          .kind = CONFIRM_DESTRUCTIVE,
                          .back = -1,
                          .answer = remove_answer });
    return 0;
}

/* A gap item in view h tall, nothing for the wheel to stop on; a caption is painted on it. */
static void *gap_item(void *view, int h) {
    void *g = list_item_create(view, 0, 0, 375, h);
    widget_use_style(g, "s_listitem_black");
    return g;
}

/* view's rows stacked at their own heights (item_height 0), the list as tall as they are, up to
 * the screen's foot (carremote_page.c cr_fit's way). */
static void fit(void *view) {
    int y = 0;
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i)
        y += I(widget_get_child(view, i), W_H);
    void *list = P(view, W_PARENT);
    int room = 320 - (feedback_below(list) - I(list, W_H)), h = y < room ? y : room;
    widget_set_prop_int(list, "item_height", 0);
    widget_resize(list, 375, h);
    widget_resize(view, 375, h);
    widget_layout(list);
}

/* A row's value, Text Secondary: on the right column, or before the chevron of a row that opens a
 * page (coverflow.c row_into's labels end at x 316) */
#define ADB_VALUE_W 90
static void row_value(void *item, const char *s, int into) {
    int right = into ? 312 : 375 - geom_val(V_RIGHT_X), w = ADB_VALUE_W;
    void *v = label(item, right - w, 0, w, I(item, W_H), "s_scrlabel_white20r", TYPE(20),
                    0xff000000u | tok(TOK_SECONDARY));
    widget_set_text_utf8(v, s);
}

/* Trusted Computers: a row a computer and the day it was added, "Center to remove a computer",
 * Remove All in red and what removing does; with none, the page says so. */
static void trust_fill(void) {
    if (!at.trust) return;
    list_read();
    int sel = at.trust_view ? widget_get_prop_int(at.trust_view, "_ringnav_index", 0) : 0;
    at.cap[0] = at.cap[1] = (void *)0;
    void *view = at.trust_view = page_list_or(at.trust, at.trust, "Trusted Computers",
                                              "No trusted computers", at.n ? at.n + 3 : 0, 48);
    if (!at.n) return;
    for (int i = 0; i < at.n; ++i) {
        char day[24];
        void *item = page_row_detail(view, i, at.pc[i].name, 0, trust_click);
        void *name = widget_get_child(item, 0);
        widget_move_resize(name, I(name, W_X), I(name, W_Y),
                           375 - geom_val(V_RIGHT_X) - ADB_VALUE_W - 8 - I(name, W_X),
                           I(name, W_H));
        /* a long name rests on its ellipsis and scrolls only on the selection (artist_page.c
         * cover_row) */
        widget_set_prop_int(name, "loop", 0);
        hscroll_label_set_only_focus(name, 1);
        if (day_or_date(day, sizeof day, at.pc[i].when)) row_value(item, day, 0);
    }
    at.cap[0] = gap_item(view, ADB_CAP_H);
    void *all = page_row_detail(view, at.n, "Remove All", 0, trust_click);
    widget_set_prop_int(widget_get_child(all, 0), "style:normal:text_color",
                        (int)RGBA(tok(TOK_NEGATIVE)));
    at.cap[1] = gap_item(view, ADB_CAP_H + ADB_CAP_LINE);
    fit(view);
    ringnav_select(view, sel <= at.n ? sel : at.n, at.n + 1); /* the gaps are passed */
}

static int trust_open(void *ctx, void *event) {
    (void)ctx, (void)event;
    if (at.trust || !(at.trust = page_open("adbtrust_page", trust_closed, page_keyup))) return 0;
    at.trust_view = 0;
    trust_fill();
    return 0;
}

static int dev_closed(void *ctx, void *event) {
    (void)event;
    if (ctx == at.dev) at.dev = at.dev_view = (void *)0;
    return 0;
}

/* Developer Options: Trusted Computers and how many. */
static void dev_fill(void) {
    if (!at.dev) return;
    char n[12];
    list_read();
    void *view = at.dev_view = page_list(at.dev, at.dev, 0, "Developer Options", 1, 48);
    void *item = page_row_detail_into(view, 0, "Trusted Computers", 0, trust_open);
    tk_snprintf(n, sizeof n, "%d", at.n);
    row_value(item, n, 1);
    ringnav_select(view, 0, 1);
}

int adb_dev_open(void *ctx, void *event) {
    (void)ctx, (void)event;
    if (at.dev || !(at.dev = page_open("devopts_page", dev_closed, page_keyup))) return 0;
    dev_fill();
    return 0;
}

/* ringnav_paint's: Trusted Computers' captions on their gap items, in 13 px Text Tertiary on the
 * text column. */
void adb_trust_paint(void *w, void *canvas) {
    if (!w || !at.trust || (w != at.cap[0] && w != at.cap[1]) || !P(canvas, CANVAS_LCD)) return;
    static const char *const caps[2][2] = { { "Center to remove a computer", 0 },
                                            { "A computer you remove asks again",
                                              "the next time it connects." } };
    const char *const *c = caps[w == at.cap[1]];
    int x = geom_val(V_TEXT_X) - page_ox(canvas, w),
        cw = 375 - geom_val(V_TEXT_X) - geom_val(V_RIGHT_X);
    for (int k = 0; k < 2 && c[k]; ++k)
        ui_line(canvas, c[k], x, cw, ADB_CAP_H / 2 + k * ADB_CAP_LINE, 2, "inter", ADB_CAP_PX,
                tok(TOK_TERTIARY));
}
#endif
