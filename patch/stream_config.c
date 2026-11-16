/* Streaming's Configuration (docs/internals.md#streaming-configuration), two levels (approved as
 * 25 v3 B on 2026-11-16, docs/oled-design.md#streaming-configuration): Stop Streaming Services,
 * then TIDAL and Spotify as rows that open their own page (their switch, Tidal Downloads' switch,
 * the cache cap and Erase Spotify Data), then a switch per service that has only its switch. */
#include "navigation.h"

/* Tidal's and Spotify's cache caps, 512 MB to 8 GB, each kept in its folder's .cache-size as
 * "<MB>M", librespot's --cache-size-limit form, which the card's Spotify launcher reads when it
 * starts librespot. Files, not config.ini: tidal.c's trim reads Tidal's on its download thread,
 * and blob_io renames it into place, so a half-written one is never read. */
static const char *const sc_dirs[] = { TIDAL_DIR, SPOT_DIR };
static const char *const sc_sizes[] = { "512 MB", "1 GB", "2 GB", "4 GB", "8 GB" };

/* The choice i, the cap being 512 << i MB: 2 (2 GB) when the file is missing or holds another. */
int stream_cache(int spotify) {
    char path[48], s[12];
    tk_snprintf(path, sizeof path, "%s/.cache-size", sc_dirs[spotify]);
    state_read(path, s, sizeof s);
    unsigned mb = (unsigned)atoi(s + 1);
    for (int i = 0; i < 5; ++i)
        if (mb == 512u << i) return i;
    return 2;
}

/* The services Streaming lists, each shown or hidden by a switch here: 0 stock's Tidal, 1 Spotify,
 * the card apps (addon.c's order), then Tidal Downloads, Internet Radio and AirPlay. Q2POD
 * STREAMHIDE in config.ini holds the hidden ones' keys, comma separated: a fixed row's upper case
 * one, a card app's id (lower case, so never one of those), so a hidden app stays hidden whatever
 * its place. Missing, every row shows. Configuration is never hidden. */
static const char *const svc_fixed[][2] = { { "TIDAL", "TIDAL" },
                                            { "SPOTIFY", "Spotify" },
                                            { "DOWNLOADS", "Tidal Downloads" },
                                            { "RADIO", "Internet Radio" },
                                            { "AIRPLAY", "AirPlay" } };
#define SVC_LINE 200 /* STREAMHIDE's longest, as navigation.c's ROOT_MAX for its own lists */
#define SVC_DOWNLOADS (2 + st.svc_n)
#define SVC_RADIO (3 + st.svc_n)
/* Service i's key (label 0) or row label (1), as Streaming last listed them. */
static const char *svc(int i, int label) {
    if (i >= 2 && i < 2 + st.svc_n) return label ? addon_name(i - 2) : addon_id(i - 2);
    return svc_fixed[i < 2 ? i : i - st.svc_n][label];
}
void svc_hidden(char *s) {
    s[0] = 0; /* as root_read: a missing key leaves s alone */
    toolsReadConfig("/mnt/data/config.ini", "Q2POD", "STREAMHIDE", s, "");
}
char *svc_find(char *hide, int i) {
    const char *key = svc(i, 0);
    return key ? csv_find(hide, key) : 0;
}

/* The rows that read what they set, whichever pages are open: a provider row's Shown or Hidden,
 * the provider page's Cache and Erase Spotify Data. */
static void sc_text(void) {
    char hide[SVC_HIDE];
    svc_hidden(hide);
    for (int p = 0; p < 2; ++p)
        if (st.sc_prov[p])
            list_value(st.sc_prov[p], svc(p, 1), svc_find(hide, p) ? "Hidden" : "Shown");
    if (st.sc_cache) list_value(st.sc_cache, "Cache", sc_sizes[stream_cache(st.sc_of)]);
    if (st.sc_erase)
        widget_set_text_utf8(st.sc_erase, shelf_erase(0) ? "Erasing…" : "Erase Spotify Data");
}
/* netupdate.c's erase ended: .cache-size went with it, so Spotify's Cache reads 2 GB again. */
void stream_erased(void) { sc_text(); }

/* A switch's Centre: the service hidden, or shown again, and saved; its switch, its provider row
 * and its row on Streaming (the page under these) follow at once. ponytail: an uninstalled app's
 * key stays in STREAMHIDE (hidden again if it comes back); a list full of them (SVC_LINE) refuses
 * more. */
static int svc_toggle(int i) {
    char hide[SVC_HIDE], title[48];
    const char *key = svc(i, 0);
    if (!key) return 0;
    svc_hidden(hide);
    /* SVC_LINE, not sizeof hide: stock's config line (a 257-byte sprintf, a 255-byte fgets) */
    int hidden = csv_toggle(hide, SVC_LINE, key);
    if (hidden < 0) {
        toast("Could not save setting");
        return 0;
    }
    toolsWriteConfig("/mnt/data/config.ini", "Q2POD", "STREAMHIDE", hide);
    if (st.sc_check[i] && !widget_get_text_utf8(st.sc_check[i], title, sizeof title))
        list_toggle(st.sc_check[i], title, !hidden);
    sc_text();
    /* The page's rows, and a set with home_lib's list on Home's window, by window: Home's comes
     * first and may be the empty one. */
    void *wm = window_manager();
    for (unsigned k = 0, windows = widget_count_children(wm); k < windows; ++k) {
        void *view = widget_lookup(widget_get_child(wm, k), "scroll_view_streamsset", 1);
        for (unsigned c = 0, rows = view ? widget_count_children(view) : 0; c < rows; ++c)
            if (widget_get_prop_int(widget_get_child(view, c), SVC_PROP, 0) == i + 1)
                widget_set_visible(widget_get_child(view, c), !hidden, 0);
        if (view) widget_layout(view);
    }
    return 0;
}

/* A section header, a whole row so the list scrolls in whole rows: what the rows under it are, in
 * 15 px Text Tertiary at the text column (docs/oled-design.md Section header), clear as the rows
 * (the theme's default list_item fills). */
static void sc_header(void *view, int index, const char *s) {
    int rh = page_h(48), px = 18; /* Inter 15, AWTK's size */
    void *head = list_item_create(view, 0, index * rh, 375, rh);
    widget_use_style(head, "s_listitem_black");
    void *label = text(head, theme()->page_x, 0, 375 - 2 * theme()->page_x, rh);
    widget_set_text_utf8(label, s);
    const char *face = theme_face(widget_get_text(label), -1, &px);
    widget_set_prop_str(label, "style:normal:font_name", face ? face : "default");
    widget_set_prop_int(label, "style:normal:font_size", px);
    widget_set_prop_str(label, "style:normal:text_align_h", "left");
    text_ink(label, RGBA(tok(TOK_TERTIARY)), INK_LABEL);
}
/* Service i's switch row at index, titled title; its label is st.sc_check[i] while it shows. */
static void sc_switch(void *view, int index, int i, const char *title, const char *hide,
                      int (*click)(void *, void *)) {
    st.sc_check[i] = widget_get_child(page_row_detail(view, index, "", 0, click), 0);
    list_toggle(st.sc_check[i], title, !svc_find((char *)hide, i));
}

/* Erase Spotify Data's card (docs/oled-design.md#confirmation-card): Erase (row 1) starts it. */
static void sc_erase_answer(void *ctx, int row, int toggle_on) {
    if (row == 1) shelf_erase(1);
    sc_text();
}
/* The provider page's rows: TIDAL's Show in Streaming, Show Downloads and Cache; Spotify's Show
 * in Streaming, Cache and Erase Spotify Data. A Cache row steps to the next size and saves it:
 * Tidal's applies at the next download's trim, Spotify's when librespot next starts. Erase asks
 * first on the confirmation card (one running says to wait: shelf_erase's toast). */
static int sub_click(void *ctx, void *event) {
    int row = (int)(long)ctx, sp = st.sc_of;
    if (row == 0) return svc_toggle(sp);
    if (row == 1 && !sp) return svc_toggle(SVC_DOWNLOADS);
    if (row == 2 && sp) {
        static const char *const rows[] = { "Cancel", "Erase" };
        if (shelf_erase(0))
            shelf_erase(1); /* erasing already: says to wait */
        else
            confirm(&(confirm_t){ .title = "Erase Spotify Data?",
                                  .body = "Its login, cache and logs go;\nsign in again to use it.",
                                  .rows = rows,
                                  .n_rows = 2,
                                  .kind = CONFIRM_DESTRUCTIVE,
                                  .back = -1,
                                  .answer = sc_erase_answer });
        return 0;
    }
    if (sp && shelf_erase(0)) return toast("Wait for the one in progress"), 0; /* it rm -rfs */
    char path[48], tmp[52], s[8];
    int n = tk_snprintf(s, sizeof s, "%uM", 512u << (stream_cache(sp) + 1) % 5);
    tk_snprintf(path, sizeof path, "%s/.cache-size", sc_dirs[sp]);
    tk_snprintf(tmp, sizeof tmp, "%s.tmp", path);
    mkdir(sc_dirs[sp], 0755);
    if (!blob_io(path, tmp, s, (unsigned)n, 1)) toast("Could not save setting");
    sc_text();
    return 0;
}
static int sc_closed(void *ctx, void *event) {
    if (ctx == st.sc_sub) {
        st.sc_sub = st.sc_cache = st.sc_erase = 0;
        st.sc_check[0] = st.sc_check[1] = st.sc_check[SVC_DOWNLOADS] = 0;
    }
    if (ctx == st.sc_page) {
        st.sc_page = st.sc_prov[0] = st.sc_prov[1] = 0;
        for (int i = 2; i < SVC_FIXED + st.svc_n; ++i)
            if (i != SVC_DOWNLOADS) st.sc_check[i] = 0;
    }
    return 0;
}
/* TIDAL's (sp 0) or Spotify's page, titled by its name. */
static void sc_open_sub(int sp) {
    if (st.sc_sub || !(st.sc_sub = page_open("streamsvc_page", sc_closed, page_keyup))) return;
    char hide[SVC_HIDE];
    svc_hidden(hide);
    st.sc_of = sp;
    void *view = page_list(st.sc_sub, st.sc_sub, 0, svc(sp, 1), 3, 48);
    sc_switch(view, 0, sp, "Show in Streaming", hide, sub_click);
    if (!sp) sc_switch(view, 1, SVC_DOWNLOADS, "Show Downloads", hide, sub_click);
    st.sc_cache = widget_get_child(page_row_detail(view, sp ? 1 : 2, "", 0, sub_click), 0);
    if (sp) st.sc_erase = widget_get_child(page_row_detail(view, 2, "", 0, sub_click), 0);
    sc_text();
}

/* Configuration's rows, by index: Stop Streaming Services; the Services header; TIDAL and Spotify,
 * each opening its page; the Show in Streaming header; then the card apps', Internet Radio's and
 * AirPlay's switches. Stop Streaming Services ends librespot and its restart loop, a radio
 * station, the card apps (addon.c) and a stock stream (Tidal's) queue, so the keys and the output
 * are local music's again; local music already stopped is left as it is. */
#define SC_FIRST_SWITCH 5
static int sc_click(void *ctx, void *event) {
    int row = (int)(long)ctx;
    if (row == 0) {
        unsigned type = (unsigned)MCL(MCL_TYPE);
        spot_stop();
        radio_stop();
        addon_stop();
        if (type != 1 && (type & 0xf000) != 0xf000 && mclGetPlayStatus() != 1) player_stop();
        toast("Streaming stopped");
        return 0;
    }
    if (row == 2 || row == 3) return sc_open_sub(row - 2), 0;
    int k = row - SC_FIRST_SWITCH; /* the card apps, then Internet Radio and AirPlay */
    return k < 0 ? 0 : svc_toggle(k < st.svc_n ? 2 + k : SVC_RADIO + k - st.svc_n);
}

int stream_config(void *ctx, void *event) {
    if (st.sc_page || !(st.sc_page = page_open("streamconf_page", sc_closed, page_keyup))) return 0;
    int n = st.svc_n + 2; /* the card apps, Internet Radio, AirPlay */
    void *view = page_list(st.sc_page, st.sc_page, 0, "Configuration", SC_FIRST_SWITCH + n, 48);
    page_row_detail(view, 0, "Stop Streaming Services", 0, sc_click);
    sc_header(view, 1, "Services");
    for (int p = 0; p < 2; ++p) {
        void *item = page_row_detail(view, 2 + p, "", 0, sc_click);
        row_into(item, page_h(48));
        st.sc_prov[p] = widget_get_child(item, 0);
    }
    sc_header(view, 4, "Show in Streaming");
    char hide[SVC_HIDE];
    svc_hidden(hide);
    for (int k = 0; k < n; ++k) {
        int i = k < st.svc_n ? 2 + k : SVC_RADIO + k - st.svc_n;
        sc_switch(view, SC_FIRST_SWITCH + k, i, svc(i, 1), hide, sc_click);
    }
    sc_text();
    return 0;
}
