/* The Library (docs/ipod.md#rows): its list, and the lists under it that are groups of songs
 * (Artists, Albums) and then a group's songs. Centre on a song plays its list from that song,
 * through the player (ui/player.c). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "q2ui.h"

/* ---- a group's songs, or a playlist's (kind -1) ---- */
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
    } else if (s->kind < 0)
        out->value = lib_artist(s->songs[i]); /* a playlist's: its artist on the right column */
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

/* ---- Playlists, and a playlist's songs ---- */
#define PLAYLISTS_MAX 101 /* stock's ids 1 to 100, and On-The-Go */
#define PLAYLIST_SONGS 5000
static void playlist_push(int list) {
    struct songs_st *s = calloc(1, sizeof *s);
    int *songs = malloc(PLAYLIST_SONGS * sizeof *songs);
    s->kind = -1, s->group = list;
    s->n = lib_playlist_songs(list, songs, PLAYLIST_SONGS);
    s->songs = songs;
    s->ids = malloc((size_t)(s->n ? s->n : 1) * sizeof *s->ids);
    for (int i = 0; i < s->n; ++i) s->ids[i] = lib_ids()[songs[i]];
    snprintf(s->name, sizeof s->name, "playlist.%d", list);
    ui_source src = { .count = s_count, .row = s_row, .chosen = s_chosen, .ctx = s, .name = s->name };
    ui_push(&list_page, &src);
}
static struct {
    int n, ids[PLAYLISTS_MAX];
    char names[PLAYLISTS_MAX][64];
} pls;
static int p_count(void *ctx) {
    (void)ctx;
    return pls.n;
}
static void p_row(void *ctx, int i, ui_row *out) {
    (void)ctx;
    out->title = pls.names[i], out->flags = ROW_CHEVRON;
}
static void p_chosen(void *ctx, int i) {
    (void)ctx;
    playlist_push(pls.ids[i]);
}
static void playlists_push(void) {
    pls.n = lib_playlists(pls.ids, pls.names, PLAYLISTS_MAX); /* read again each time: demo edits them */
    ui_source src = { .count = p_count, .row = p_row, .chosen = p_chosen, .name = "playlists" };
    ui_push(&list_page, &src);
}

/* ---- Artists, Albums ---- */
static int g_count(void *ctx) { return lib_groups((int)(long)ctx); }
/* An album's cover, under each artist its songs name until one is there (ui/art.c) */
static const image_t *album_cover(int g) {
    int n, k = 0;
    const int *songs = lib_group_songs(LIB_ALBUM, g, &n);
    const char *seen[ART_TRIES];
    unsigned keys[ART_TRIES];
    const char *album = lib_album(songs[0]);
    for (int i = 0; i < n && k < ART_TRIES; ++i) {
        const char *artist = lib_artist(songs[i]);
        int dup = 0;
        for (int j = 0; j < k; ++j) dup |= !strcmp(seen[j], artist);
        if (!dup) seen[k] = artist, keys[k++] = art_key(artist, album);
    }
    return art_cover(keys, k);
}
static void g_row(void *ctx, int i, ui_row *out) {
    int kind = (int)(long)ctx;
    out->title = lib_group_name(kind, i);
    if (kind == LIB_ALBUM) out->sub = lib_group_artist(i), out->art = album_cover(i); /* two lines, the cover leading */
    out->flags = ROW_CHEVRON;
}
static void g_chosen(void *ctx, int i) { group_songs_push((int)(long)ctx, i); }
static void groups_push(int kind) {
    ui_source src = { .count = g_count, .row = g_row, .chosen = g_chosen, .ctx = (void *)(long)kind,
                      .name = kind == LIB_ARTIST ? "artists" : "albums", .letters = 1, .row_h = kind == LIB_ALBUM ? 52 : 0 };
    ui_push(&list_page, &src);
}

/* ---- the Library's own list ---- */
static const struct {
    const char *title, *demo;
} lib_rows[] = { { "Songs", 0 },          { "Artists", 0 },  { "Albums", 0 },
                 { "Playlists", 0 },      { "Years", "era_page" }, { "Ratings", "ratings_page" },
                 { "Most Played", "mostplayed_page" } };
/* Years, Ratings and Most Played are demo's pages, opened through the gate (page_home.c).
 * Playlists is q2ui's own, read from stock's table: no stock page name opens demo's.
 * ponytail: Genres, Composers, Recently Added and Long Songs are reached through Home's Old Menus
 * until each has a page here or an opener the gate is seen to run. */
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
    else if (i == 3)
        playlists_push();
    else
        groups_push(i == 1 ? LIB_ARTIST : LIB_ALBUM);
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
    else if (!strcmp(name, "playlists"))
        playlists_push();
    else if (sscanf(name, "playlist.%d", &group) == 1 && group >= 0 && group <= 100)
        playlist_push(group);
    else if (sscanf(name, "group.%d.%d", &kind, &group) == 2 && kind >= 0 && kind < LIB_KINDS && group >= 0 &&
             group < lib_groups(kind))
        group_songs_push(kind, group);
    else
        return 0;
    return 1;
}
