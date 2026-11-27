/* The Library (docs/ipod.md#rows): its list, and the lists under it that are groups of songs
 * (Artists, Albums) and then a group's songs. Centre on a song plays its list from that song,
 * through the player (ui/player.c). Rows whose page q2ui has not got open demo's (ui_demo_page). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "q2ui.h"

/* ---- a group's songs ---- */
struct songs_st {
    char name[32];
    int kind, group, n;
    const int *songs; /* indices into the library's songs */
    int *ids;
    char lead[12];
};
static int s_count(void *ctx) { return ((struct songs_st *)ctx)->n; }
static void s_row(void *ctx, int i, ui_row *out) {
    struct songs_st *s = ctx;
    out->title = lib_title(s->songs[i]);
    if (s->kind == LIB_ALBUM) {
        /* an album's tracks: the number in its column, no artist */
        int track = lib_track(s->songs[i]);
        snprintf(s->lead, sizeof s->lead, "%d", track > 0 ? track : i + 1);
        out->lead = s->lead;
    }
}
static void s_chosen(void *ctx, int i) {
    struct songs_st *s = ctx;
    player_play(s->ids, s->n, i, 0);
}
static void group_songs_push(int kind, int group) {
    /* the page keeps its source's context for as long as the program runs: a list is opened a
     * few times a session and each is a few words and its ids */
    struct songs_st *s = calloc(1, sizeof *s);
    s->kind = kind, s->group = group;
    s->songs = lib_group_songs(kind, group, &s->n);
    s->ids = malloc((size_t)(s->n ? s->n : 1) * sizeof *s->ids);
    for (int i = 0; i < s->n; ++i) s->ids[i] = lib_ids()[s->songs[i]];
    snprintf(s->name, sizeof s->name, "group.%d.%d", kind, group);
    ui_source src = { .count = s_count, .row = s_row, .chosen = s_chosen, .ctx = s, .name = s->name };
    ui_push(&list_page, &src);
}

/* ---- Artists, Albums ---- */
static int g_count(void *ctx) { return lib_groups((int)(long)ctx); }
static void g_row(void *ctx, int i, ui_row *out) {
    int kind = (int)(long)ctx;
    out->title = lib_group_name(kind, i);
    if (kind == LIB_ALBUM) out->detail = lib_group_artist(i);
    out->flags = ROW_CHEVRON;
}
static void g_chosen(void *ctx, int i) { group_songs_push((int)(long)ctx, i); }
static void groups_push(int kind) {
    ui_source src = { .count = g_count, .row = g_row, .chosen = g_chosen, .ctx = (void *)(long)kind,
                      .name = kind == LIB_ARTIST ? "artists" : "albums", .letters = 1 };
    ui_push(&list_page, &src);
}

/* ---- the Library's own list ---- */
static const struct {
    const char *title, *demo;
} lib_rows[] = { { "Songs", 0 },     { "Playlists", "playlist_page" }, { "Artists", 0 },
                 { "Albums", 0 },    { "Years", "era_page" },           { "Ratings", "ratings_page" },
                 { "Most Played", "mostplayed_page" } };
static int l_count(void *ctx) {
    (void)ctx;
    return (int)(sizeof lib_rows / sizeof *lib_rows);
}
static void l_row(void *ctx, int i, ui_row *out) {
    (void)ctx;
    out->title = lib_rows[i].title, out->flags = ROW_CHEVRON;
}
static void l_chosen(void *ctx, int i) {
    (void)ctx;
    if (lib_rows[i].demo)
        ui_demo_page(lib_rows[i].demo);
    else if (i == 0)
        songs_push();
    else
        groups_push(i == 2 ? LIB_ARTIST : LIB_ALBUM);
}
void library_push(void) {
    ui_source src = { .count = l_count, .row = l_row, .chosen = l_chosen, .name = "library" };
    ui_push(&list_page, &src);
}

/* A route's page by its name (ui_route): 1 when it was one of the Library's and is now open. */
int library_route(const char *name) {
    int kind, group;
    if (!strcmp(name, "library"))
        library_push();
    else if (!strcmp(name, "songs"))
        songs_push();
    else if (!strcmp(name, "artists") || !strcmp(name, "albums"))
        groups_push(name[1] == 'r' ? LIB_ARTIST : LIB_ALBUM);
    else if (sscanf(name, "group.%d.%d", &kind, &group) == 2 && kind >= 0 && kind < LIB_KINDS && group >= 0 &&
             group < lib_groups(kind))
        group_songs_push(kind, group);
    else
        return 0;
    return 1;
}
