/* A card app's streaming song plays from its temporary download, the app's stream cache:
 * /mnt/mmc/.<app>/stream/<account>/<quality>/<x>/<title> [<sid>].<suffix>, a file no library record has
 * (the player's id 0) and catalogue.avail need not name. Its server id is between the name's last
 * brackets. Shared by the payload's catalogue.c (cat_copy_of: the UI's covers, play counts and
 * hearts) and coda-rc's coda_rc_lib.c (lib_stream_song), so both read the same paths alike
 * (docs/internals.md#car-remote-streams). No libc: each side has its own. */
#ifndef STREAM_PATH_H
#define STREAM_PATH_H

/* path's app id (after "/mnt/mmc/.", *appn bytes) and sid (*sidn bytes, letters, digits, '-' and
 * '_' only), pointing into path; 0 for any other path. */
static inline int stream_path_sid(const char *path, const char **app, int *appn, const char **sid,
                                  int *sidn) {
    static const char root[] = "/mnt/mmc/.", dir[] = "/stream/";
    int i = 0, k;
    if (!path) return 0;
    for (; root[i]; ++i)
        if (path[i] != root[i]) return 0;
    const char *a = path + i, *s = a, *name = 0, *o = 0, *c = 0;
    while (*s && *s != '/') ++s;
    if (s == a) return 0;
    for (k = 0; dir[k]; ++k)
        if (s[k] != dir[k]) return 0;
    for (const char *p = s; *p; ++p)
        if (*p == '/') name = p + 1;
    for (const char *p = name; *p; ++p)
        if (*p == '[') o = p;
    for (const char *p = o; p && *p && !c; ++p)
        if (*p == ']') c = p;
    if (!c || c == o + 1) return 0;
    for (const char *p = o + 1; p < c; ++p)
        if (!(*p >= '0' && *p <= '9') && !((*p | 32) >= 'a' && (*p | 32) <= 'z') && *p != '-' &&
            *p != '_')
            return 0;
    *app = a, *appn = (int)(s - a), *sid = o + 1, *sidn = (int)(c - o - 1);
    return 1;
}

#endif
