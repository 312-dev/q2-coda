/* Library > Songs (docs/oled-design.md#song-rows): the Shuffle row, pinned under the status row,
 * and under it every song by title, its artist inline after it. */
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
    out->title = lib_title(i - 1), out->detail = lib_artist(i - 1);
}

/* Centre: the whole list is the queue, from the song chosen; the Shuffle row shuffles it. */
static void chosen(void *ctx, int i) {
    (void)ctx;
    if (lib_songs()) player_play(lib_ids(), lib_songs(), i ? i - 1 : 0, !i);
}

void songs_push(void) {
    ui_source src = { .count = count, .row = row, .chosen = chosen, .name = "songs", .pinned = 1, .letters = 1 };
    ui_push(&list_page, &src);
}
