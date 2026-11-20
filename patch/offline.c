/* Sync Offline (docs/internals.md#sync-offline, the user's ruling of 2026-11-19): a row in the hold
 * menu of a Library song, album, artist or playlist that a signed-in card app's server has, and in
 * Now Playing's ⋯ for the song playing, that chooses the item for the app's next Sync, or takes it
 * out. Its label is the item's state in the app's own list, /mnt/mmc/.<id>/offline.txt, a line per
 * chosen item ("<kind>\t<id>\t<name>"): Sync Offline, or Stop Syncing Offline for an item there;
 * no row while the app syncs its Entire Library ("library\tall"), which holds everything already.
 * The item's identity is catalogue.c's (catalogue_offline_key); the request and its toast are
 * addon.c's (addon_offline_ask). One menu is up at a time, so the item worked out for its row is
 * kept for the press. */
#include "navigation.h"

#define OFF_LIST_MAX (64 << 10) /* offline.txt read this far: about 2,000 chosen items */

static struct {
    char app[16], key[256];
    int on; /* the item is in offline.txt */
} om __attribute__((section(".scratch")));

/* The item's state in app's offline.txt: 0 not there, 1 there, 2 Entire Library chosen. key is
 * "<kind> <id>", or "artistname <name>", which matches an artist line's name in any case. */
int offline_state(const char *app, const char *key) {
    char path[64], buf[1024], line[512];
    tk_snprintf(path, sizeof path, "/mnt/mmc/.%s/offline.txt", app);
    void *f = fopen(path, "rb");
    if (!f) return 0;
    const char *sp = strstr(key, " ");
    unsigned kn = sp ? (unsigned)(sp - key) : 0, k = 0;
    int byname = kn == 10 && !strncmp(key, "artistname", 10), state = 0;
    long read = 0;
    for (unsigned got; !state && read < OFF_LIST_MAX && (got = fread(buf, 1, sizeof buf, f)) > 0;) {
        read += (long)got;
        for (unsigned i = 0; i < got && !state; ++i) {
            if (buf[i] != '\n') {
                if (k < sizeof line - 1) line[k++] = buf[i];
                continue;
            }
            if (k && line[k - 1] == '\r') --k;
            line[k] = 0, k = 0;
            char *t1 = strstr(line, "\t"), *t2 = t1 ? strstr(t1 + 1, "\t") : 0;
            if (!t1 || !t2) continue;
            *t1 = *t2 = 0;
            if (!strcmp(line, "library") && !strcmp(t1 + 1, "all"))
                state = 2;
            else if (byname)
                state = !strcmp(line, "artist") && !strcasecmp(t2 + 1, sp + 1);
            else
                state =
                    sp && strlen(line) == kn && !strncmp(line, key, kn) && !strcmp(t1 + 1, sp + 1);
        }
    }
    fclose(f);
    return state;
}

/* The hold menu's (or ⋯'s) row for an item, OFF_* kind (catalogue_offline_key's r and list): its
 * label, or 0 for none (no signed-in server has it, its app takes no Sync Offline, or the app syncs
 * everything). Kept for offline_press. */
const char *offline_row(int kind, void *r, int list) {
    om.app[0] = 0;
    if (!kind || !catalogue_offline_key(kind, r, list, om.app, om.key, sizeof om.key) ||
        !addon_offline_ok(om.app)) {
        om.app[0] = 0;
        return (const char *)0;
    }
    int state = offline_state(om.app, om.key);
    if (state == 2) {
        om.app[0] = 0;
        return (const char *)0;
    }
    om.on = state;
    return om.on ? "Stop Syncing Offline" : "Sync Offline";
}
/* The row's symbol (tools/art.py MENU_ICONS), its state's */
const char *offline_icon(void) { return om.on ? "cloud-slash" : "cloud-arrow-down"; }

/* The row pressed: the app asked to add the item or take it out; it toasts its answer. */
void offline_press(void) {
    if (!om.app[0]) return;
    if (!addon_offline_ask(om.app, !om.on, om.key)) toast("Couldn't reach the app");
    om.app[0] = 0;
}

/* Now Playing's ⋯ (onlinematch.c more_rows): a Sync Offline row for the song playing, closing the
 * page as it asks. */
static int np_click(void *ctx, void *event) {
    (void)event;
    offline_press();
    if (ctx) window_close(ctx);
    return 0;
}
void offline_np_row(void *win, void *view) {
    unsigned pos, count;
    void *r = queue_now(&pos, &count);
    const char *text = r ? offline_row(OFF_SONG, r, -1) : (const char *)0;
    if (!text) return;
    void *label = list_row(view, "player_onlinesearch", np_click, win);
    widget_set_text_utf8(label, text);
    widget_resize(label, 253, I(label, W_H));
    widget_set_prop_int(P(label, W_PARENT), "_offline", 1); /* modal_more's symbol (menu_modal.c) */
}
