/* Cover Flow with no albums (docs/oled-design.md#empty-and-offline): the empty state (feedback.c
 * empty_state), by what is missing, in place of stock's "Update Local Music first". Music on the
 * card but none in the library (CF_UNSCANNED): "No Albums Yet", "Music on the card isn't
 * scanned", Scan Music Now (stock's scan, its own dialog). No card music and a catalogue app
 * signed in (CF_STREAMING, catalogue.c catalogue_signed): "No Albums", "Streaming albums can show
 * here", no row: its albums join by themselves once its catalogue is in. Neither (CF_NONE): "No
 * Albums", "Copy music to the SD card", no row; with no card in, "No SD Card", "Insert an SD card
 * with music" (feedback.c empty_card). coverflow.c loads the covers again once the library changes.
 */
#include "navigation.h"

enum { CF_NONE, CF_STREAMING, CF_UNSCANNED };
#define CF_WALK_MAX 4000 /* card entries looked at for a song, at most */
#define CF_WALK_DEPTH 6

static int cf_cause __attribute__((section(".scratch")));

static int audio_name(const char *d) {
    static const char *const exts[] = { "flac", "mp3", "m4a", "aac", "wav", "ape",  "ogg",
                                        "opus", "dsf", "dff", "wma", "aif", "aiff", "wv" };
    const char *dot = strrchr(d, '.');
    for (unsigned i = 0; dot && i < sizeof exts / sizeof *exts; ++i)
        if (!strcasecmp(dot + 1, exts[i])) return 1;
    return 0;
}
/* A song file under path (a folder of n bytes in a 512 byte buffer), dot-folders left out, at most
 * CF_WALK_DEPTH deep and *budget entries in all. */
static int walk(char *path, unsigned n, int depth, int *budget) {
    void *dir = opendir(path);
    int found = 0;
    for (struct dirent *e; dir && !found && *budget > 0 && (e = readdir(dir));) {
        const char *d = e->d_name;
        --*budget;
        if (d[0] == '.') continue;
        if (e->d_type == 8 || !e->d_type) found = audio_name(d);
        if (!found && (e->d_type == 4 || !e->d_type) && depth < CF_WALK_DEPTH &&
            n + 1 + strlen(d) < 511) {
            tk_snprintf(path + n, 512 - n, "/%s", d);
            found = walk(path, n + 1 + strlen(d), depth + 1, budget);
            path[n] = 0;
        }
    }
    if (dir) closedir(dir);
    return found;
}
static int card_has_music(void) {
    char path[512] = "/mnt/mmc";
    int budget = CF_WALK_MAX;
    return walk(path, strlen(path), 0, &budget);
}

/* The page's row: Scan Music Now. coverflow.c's poll loads the covers again once it has changed
 * the library. */
static int row_click(void *ctx, void *event) {
    if (cf_cause == CF_UNSCANNED) {
        int scan[4] = { 0 };
        navigator_to_with_context("dialog/updatemusic_dialog", scan);
    }
    return 0;
}

/* Cover Flow's page with no albums under a theme with the house's empty pages (OLED's
 * shuffle_row): built into body, 1; 0 under another theme, which keeps its own words. */
int cf_empty(void *page, void *body) {
    if (!theme()->shuffle_row) return 0;
    char id[16], name[32];
    int signed_in = catalogue_signed(id, name, sizeof name);
    cf_cause = card_has_music() ? CF_UNSCANNED : signed_in ? CF_STREAMING : CF_NONE;
    const char *row = cf_cause == CF_UNSCANNED ? "Scan Music Now" : (const char *)0;
    empty_t e = { "cards", cf_cause == CF_UNSCANNED ? "No Albums Yet" : "No Albums",
                  cf_cause == CF_UNSCANNED   ? "Music on the card isn't scanned"
                  : cf_cause == CF_STREAMING ? "Streaming albums can show here"
                                             : "Copy music to the SD card",
                  row };
    if (cf_cause == CF_NONE) empty_card(&e); /* no card in: "No SD Card" */
    return empty_list(page, body, "Cover Flow", &e, row_click) != (void *)0;
}
