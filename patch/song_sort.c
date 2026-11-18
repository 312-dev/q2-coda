/* Songs' order, Playback > Library > Sorting's Songs picker (library_settings.c): Title, Artist,
 * Album or Recently Added, each either way. Stock orders Songs by g_sort_flag (SYSSET SORTSELECT, 0
 * its alphabetical title order, 1 and 2 file name and format, 3 and 4 time added newest and oldest
 * first), which getAllMusic's query takes. Recently Added is the flag's 3 or 4; on the title order
 * (0) this keeps Title, Artist or Album as [IPOD] SONGSORT (SORT_*) and its direction as SONGDOWN
 * (1: descending) and, under a modal set, orders the loaded list (song_order): Artist and Album
 * stably over the title order, Title descending reversed. Other themes keep stock's picker and
 * stock's order. */
#include "navigation.h"

static struct {
    unsigned char read, sort, down, due;
} so __attribute__((section(".scratch")));

static void sort_read(void) {
    if (so.read) return;
    so.sort = (unsigned char)config_value("IPOD", "SONGSORT", SORT_N, SORT_TITLE);
    so.down = (unsigned char)config_value("IPOD", "SONGDOWN", 2, 0);
    so.read = 1;
}
int song_sort(void) {
    int flag = I(g_sort_flag, 0);
    if (flag == 3 || flag == 4) return SORT_ADDED;
    sort_read();
    return so.sort == SORT_ADDED ? SORT_TITLE : so.sort;
}
int song_down(void) {
    int flag = I(g_sort_flag, 0);
    if (flag == 3 || flag == 4) return flag == 3;
    sort_read();
    return so.down;
}

/* Sets the order and its direction and saves them and stock's flag; song_sort_commit has Songs
 * reload in it. */
void song_sort_set(int sort, int down) {
    sort_read();
    so.sort = (unsigned char)sort, so.down = (unsigned char)!!down;
    int flag = sort == SORT_ADDED ? (down ? 3 : 4) : 0;
    *(int *)g_sort_flag = flag;
    write_int_config(flag, "SYSSET", "SORTSELECT");
    write_int_config(so.sort, "IPOD", "SONGSORT");
    write_int_config(so.down, "IPOD", "SONGDOWN");
    so.due = 1;
}

/* After a change, Songs reloads when it is on top again (g_sort_changeflag, as after stock's
 * picker). 1 when there was a change. */
int song_sort_commit(void) {
    int due = so.due;
    if (due) g_sort_changeflag = 1;
    so.due = 0;
    return due;
}

/* Rows the fast-scroll letter can't follow: an order other than the title's ascending (stock's time
 * added the caller knows). */
int song_sort_own(void) { return song_sort() != SORT_TITLE || song_down(); }

/* a before b (below 0) or after it in the order: an untagged name after the rest either way, the
 * direction on the name alone, so an album keeps its discs and tracks in their own order */
static int rec_cmp(void *a, void *b, int sort, int down) {
    int key = sort == SORT_ARTIST ? REC_ARTIST : REC_ALBUM;
    const char *ka = P(a, key), *kb = P(b, key);
    int ea = !ka || !*ka, eb = !kb || !*kb;
    if (ea != eb) return ea - eb;
    int c = ea ? 0 : strcasecmp(ka, kb);
    if (c || sort != SORT_ALBUM) return down ? -c : c;
    c = I(a, REC_DISC) - I(b, REC_DISC);
    return c ? c : I(a, REC_TRACK) - I(b, REC_TRACK);
}

/* list (stSongInfo records, as loaded) in the chosen order. Artist and Album: a stable merge sort
 * of indices, then the records moved into place (rec_permute), as playback.c's Play Next moves
 * rows. */
void song_order(void *list) {
    unsigned n = list ? deque_size(list) : 0;
    int sort = song_sort(), down = song_down();
    if (n < 2 || I(g_sort_flag, 0) || (sort == SORT_TITLE && !down)) return;
    unsigned *at = calloc(2 * n, sizeof *at), *tmp;
    if (!at) return;
    tmp = at + n;
    for (unsigned i = 0; i < n; ++i) at[i] = i;
    if (sort == SORT_TITLE) {
        for (unsigned i = 0; i < n; ++i) at[i] = n - 1 - i;
    } else
        for (unsigned w = 1; w < n; w *= 2) {
            for (unsigned lo = 0; lo < n; lo += 2 * w) {
                unsigned mid = lo + w < n ? lo + w : n, hi = lo + 2 * w < n ? lo + 2 * w : n;
                unsigned i = lo, j = mid, k = lo;
                while (i < mid && j < hi) {
                    int c = rec_cmp(deque_at(list, at[i]), deque_at(list, at[j]), sort, down);
                    tmp[k++] = c > 0 ? at[j++] : at[i++];
                }
                while (i < mid) tmp[k++] = at[i++];
                while (j < hi) tmp[k++] = at[j++];
            }
            memcpy(at, tmp, n * sizeof *at);
        }
    rec_permute(list, at);
    free(at);
}

/* Place i of list takes record at[i] (at: a permutation of 0..n-1, n the list's size): each cycle
 * followed once, at[] marked done (n) as it goes. Records move whole, their strings with them. */
void rec_permute(void *list, unsigned *at) {
    unsigned n = deque_size(list);
    unsigned char rec[REC_SIZE];
    for (unsigned i = 0; i < n; ++i) {
        if (at[i] == i || at[i] == n) continue;
        memcpy(rec, deque_at(list, i), REC_SIZE);
        unsigned j = i;
        while (at[j] != i) {
            unsigned from = at[j];
            memcpy(deque_at(list, j), deque_at(list, from), REC_SIZE);
            at[j] = n;
            j = from;
        }
        memcpy(deque_at(list, j), rec, REC_SIZE);
        at[j] = n;
    }
}
