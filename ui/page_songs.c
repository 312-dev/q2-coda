/* Library > Songs (docs/oled-design.md#song-rows): the Shuffle row, pinned under the status row,
 * and under it every song by title, its artist inline after it (or as Song Artist puts it) and, with Song
 * Length's All Lists, its length on the right column. */
#include <stdlib.h>

#include "q2ui.h"

static int count(void *ctx) {
    (void)ctx;
    return lib_songs() + 1;
}
static void row(void *ctx, int i, ui_row *out) {
    (void)ctx;
    if (!i) {
        out->title = "Shuffle", out->icon = icon("shuffle"), out->flags = ROW_ACCENT;
        return;
    }
    static char len[16];
    out->title = lib_title(i - 1), out->detail = lib_artist(i - 1);
    if (config_song_length() == SONG_LENGTH_ALL) out->length = lib_length(i - 1, len, sizeof len);
}

/* Centre: the whole list is the queue, from the song chosen; the Shuffle row shuffles it. Now
 * Playing opens over the list, as demo's does when a song is chosen; Return there gives the list back. */
static void chosen(void *ctx, int i) {
    (void)ctx;
    if (lib_songs() && !player_play(lib_ids(), lib_songs(), i ? i - 1 : 0, !i)) ui_push(&page_now, 0);
}

/* row i's song by its id, for the playing song's row (list.c); the Shuffle row none */
static int song(void *ctx, int i) {
    (void)ctx;
    return i ? lib_ids()[i - 1] : 0;
}

/* Centre held on a song: its hold menu (page_library.c song_menu) */
static void held(void *ctx, int i) {
    (void)ctx;
    if (i) song_menu(i - 1);
}

void songs_push(void) {
    ui_source src = { .count = count, .row = row, .chosen = chosen, .held = held, .song = song, .name = "songs", .pinned = 1, .letters = 1 };
    ui_push(&list_page, &src);
}
