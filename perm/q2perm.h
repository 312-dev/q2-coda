/* Vamp API 1's run-time permissions (docs/platform/packages.md#users-and-groups), shared by the services that check them
 * (q2playd, q2sysd, q2netd) and by q2pkgd, which makes the users and groups. Header only: each service is one file
 * built alone, so this is included with -I perm.
 *
 * A package's user (uid from Q2PERM_UID_MIN, named "app-<id>") is a member of one group per permission its manifest
 * names, each group named as the permission. A service asks, of a client on its socket: is the caller root, or a
 * user that /etc/group lists in the service's group? The caller's uid is SO_PEERCRED's, taken when it connected;
 * its name and groups are read from the files (/etc/passwd and /etc/group, which on the image are links to the copies
 * q2pkgd composes in /run/vamp), not from the process, so a package cannot add a group to itself. */
#ifndef Q2PERM_H
#define Q2PERM_H
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

#define Q2PERM_UID_MIN 10000 /* the first package's user; below it the system's */

/* each permission's group and its gid, the order of docs/platform/packages.md's table */
static const struct { const char *name; int gid; } Q2PERM_GROUPS[] = {
    {"playback", 2000},    {"library", 2001},     {"library-write", 2002}, {"settings", 2003},
    {"network-hold", 2004}, {"screen", 2005},     {"music-files", 2006},  {"background", 2007},
    {"notices", 2008},     {"ask", 2009},         {0, 0}};

/* the uid of the client on a connected Unix socket; -1 when the kernel will not say */
static inline long q2perm_peer_uid(int fd) {
#ifdef SO_PEERCRED
    struct ucred cr;
    socklen_t n = sizeof cr;
    if (!getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &cr, &n) && n == sizeof cr) return (long)cr.uid;
#endif
    (void)fd;
    return -1;
}

/* in a passwd file, the name of uid (into name), and its primary gid; 0 found */
static inline int q2perm_user(const char *passwd, long uid, char *name, size_t room, long *gid) {
    FILE *f = fopen(passwd, "r");
    char l[512];
    int found = -1;
    while (f && found && fgets(l, sizeof l, f)) {
        char *c1 = strchr(l, ':'), *c2 = c1 ? strchr(c1 + 1, ':') : 0, *c3 = c2 ? strchr(c2 + 1, ':') : 0, *end;
        if (!c3 || strtol(c2 + 1, &end, 10) != uid || end != c3 || c2 + 1 == c3) continue;
        snprintf(name, room, "%.*s", (int)(c1 - l), l);
        *gid = strtol(c3 + 1, 0, 10);
        found = 0;
    }
    if (f) fclose(f);
    return found;
}

/* in a group file, whether the group named grp has gid, or lists user among its members */
static inline int q2perm_member(const char *group, const char *grp, const char *user, long gid) {
    FILE *f = fopen(group, "r");
    char l[4096];
    int yes = 0;
    size_t k = strlen(grp), u = strlen(user);
    while (f && !yes && fgets(l, sizeof l, f)) {
        l[strcspn(l, "\r\n")] = 0;
        char *c1 = strchr(l, ':'), *c2 = c1 ? strchr(c1 + 1, ':') : 0, *c3 = c2 ? strchr(c2 + 1, ':') : 0;
        if (!c3 || (size_t)(c1 - l) != k || strncmp(l, grp, k)) continue;
        if (strtol(c2 + 1, 0, 10) == gid && c2 + 1 != c3) yes = 1;
        for (char *m = c3 + 1; !yes && *m; m += strcspn(m, ",") + (m[strcspn(m, ",")] == ',')) /* members, comma-separated */
            if (strcspn(m, ",") == u && !strncmp(m, user, u)) yes = 1;
    }
    if (f) fclose(f);
    return yes;
}

/* Whether the client on fd may use permission perm: root always, any other uid when its user is in perm's group.
 * who gets "uid N (name)" for the log line a refusal writes. passwd and group are the files' paths (a test's
 * --root before them). */
static inline int q2perm_allowed(int fd, const char *passwd, const char *group, const char *perm, char *who, size_t whon) {
    long uid = q2perm_peer_uid(fd), gid = -1;
    char name[64] = "";
    if (uid == 0) return 1;
    int known = uid >= 0 && !q2perm_user(passwd, uid, name, sizeof name, &gid);
    snprintf(who, whon, "uid %ld (%s)", uid, known ? name : "no such user");
    return known && q2perm_member(group, perm, name, gid);
}

/* A service's socket: mode 0660 and its permission's group, when /etc/group has it and this process may give it
 * (root may; a test's own uid may not, and keeps the socket its own) */
static inline void q2perm_socket(const char *sock, const char *group, const char *perm) {
    FILE *f = fopen(group, "r");
    char l[4096];
    long gid = -1;
    size_t k = strlen(perm);
    while (f && gid < 0 && fgets(l, sizeof l, f)) {
        char *c1 = strchr(l, ':'), *c2 = c1 ? strchr(c1 + 1, ':') : 0;
        if (c2 && (size_t)(c1 - l) == k && !strncmp(l, perm, k)) gid = strtol(c2 + 1, 0, 10);
    }
    if (f) fclose(f);
    if (gid >= 0 && chown(sock, (uid_t)-1, (gid_t)gid) && geteuid() == 0) perror(sock);
    chmod(sock, 0660);
}
#endif
