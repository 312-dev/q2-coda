/* A playlist's songs in the order chosen for that playlist (docs/internals.md#playlist-song-order):
 * its row's hold menu, Sort (hold_menu.c), picks Recently Added (the songs added last first),
 * Oldest First (stock's Time added: the stored order, which Edit Playlist's moves also change) or
 * Title. Stock's getMusicByPlayList (0x50b6c8) only hands the query to sort_playlist_first_alphabet
 * (g_playlistsort_flag 0, SYSSET PLAYLISTSORT) or sort_playlist_addtime (anything else); its hook,
 * ringnav_playlist_songs, makes that choice per playlist, so whatever asks for a playlist's songs
 * gets them in its order: the playlist's page (playlist_pages.c), its hold menu's Play and Shuffle,
 * the M3U export (saveExternM3uFile) and SyncLink's play. On-The-Go (cd_num 100) and a playlist
 * with no choice of its own keep stock's flag, as before.
 *
 * The choices are [Q2POD] PLAYLISTSONGS in config.ini, one digit a header id (0 to 99, the string
 * cut after its last choice): 0 none, else PSO_NEWEST, PSO_OLDEST or PSO_TITLE. Read once into
 * ps.order; written only from the UI thread. A playlist's digit goes back to 0 as it is deleted
 * (playlists.c playlist_delete) and as a header is made in its id (ringnav_playlist_create), so a
 * new playlist never takes an old one's order. */
#include "navigation.h"

#define PS_IDS 100 /* header ids 0 to 99; On-The-Go, 100, has no header */
#define PS_KEY "PLAYLISTSONGS"

static struct {
    unsigned char read;
    char order[PS_IDS]; /* each header id's choice, 0 to PSO_N - 1 */
} ps __attribute__((section(".scratch")));

static void ps_read(void) {
    char s[256] = "";
    if (ps.read) return;
    toolsReadConfig("/mnt/data/config.ini", "Q2POD", PS_KEY, s, "");
    for (unsigned i = 0; i < PS_IDS; ++i)
        ps.order[i] = s[i] > '0' && s[i] < '0' + PSO_N ? (char)(s[i] - '0') : 0;
    ps.read = 1;
}

/* Playlist cd's own choice, PSO_NONE when it has none. */
int playlist_songs_chosen(int cd) {
    if (cd < 0 || cd >= PS_IDS) return PSO_NONE;
    ps_read();
    return ps.order[cd];
}
/* The order playlist cd's songs come in: its choice, else stock's Sort (by name at 0). */
int playlist_songs_order(int cd) {
    int chosen = playlist_songs_chosen(cd);
    if (chosen) return chosen;
    return I(g_playlistsort_flag, 0) ? PSO_OLDEST : PSO_TITLE;
}

/* Playlist cd's choice saved (PSO_NONE forgets it); the next read of its songs takes it. */
void playlist_songs_set(int cd, int order) {
    char s[PS_IDS + 1];
    int last = -1;
    if (cd < 0 || cd >= PS_IDS || order < PSO_NONE || order >= PSO_N) return;
    ps_read();
    if (ps.order[cd] == order) return;
    ps.order[cd] = (char)order;
    for (int i = 0; i < PS_IDS; ++i) {
        s[i] = (char)('0' + ps.order[i]);
        if (ps.order[i]) last = i;
    }
    s[last + 1] = 0;
    toolsWriteConfig("/mnt/data/config.ini", "Q2POD", PS_KEY, s);
}

/* getMusicByPlayList's hook: playlist cd's songs staged in its order. Recently Added is the stored
 * order backwards (stock's query is order by id, and a song added gets the next id), so the rows
 * this call staged are reversed in place; any staged before them stay. */
int ringnav_playlist_songs(int cd) {
    int order = playlist_songs_order(cd);
    if (order == PSO_TITLE) return sort_playlist_first_alphabet(cd);
    void *dir = P(tools_pdeq_directory, 0);
    unsigned from = dir ? deque_size(dir) : 0;
    int result = sort_playlist_addtime(cd);
    dir = P(tools_pdeq_directory, 0);
    if (order != PSO_NEWEST || !dir) return result;
    unsigned char rec[REC_SIZE];
    for (unsigned i = from, j = deque_size(dir); j && i < --j; ++i) {
        memcpy(rec, deque_at(dir, i), REC_SIZE);
        memcpy(deque_at(dir, i), deque_at(dir, j), REC_SIZE);
        memcpy(deque_at(dir, j), rec, REC_SIZE);
    }
    return result;
}
