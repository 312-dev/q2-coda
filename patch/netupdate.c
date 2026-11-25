/* Software Update's online half (docs/internals.md#network-update; its page is sw_update.c's): the
 * check, the latest q2-coda release's tag and size, and the download, which fetches it from GitHub
 * to the card's update.tar, checks its signature (docs/internals.md#release-signatures), then hands
 * over to the card's update. */
#include "peq.h"

/* The tag in a releases/latest redirect, ".../releases/tag/1.3.4": what follows the last '/', 1 to 31
 * of A-Za-z0-9._- (it goes into the download's URL and the signed message), else 0. */
const char *netupdate_tag(const char *location) {
    const char *slash = location ? strrchr(location, '/') : 0, *t = slash ? slash + 1 : 0;
    if (!slash || !strstr(location, "/releases/tag/") || !*t || strlen(t) > 31) return 0;
    for (const char *c = t; *c; ++c)
        if (!((*c >= 'a' && *c <= 'z') || (*c >= 'A' && *c <= 'Z') || (*c >= '0' && *c <= '9') ||
              *c == '.' || *c == '_' || *c == '-'))
            return 0;
    return t;
}

/* Q2 Shelf (docs/internals.md#q2-shelf): the apps in sources.list (fetched from q2-coda's repo,
 * signed by its release key, cached on the card), each from the release zip the list pins by its
 * SHA-256, unpacked by unzip into the card's staging folder, then moved into place file by file. */
#ifdef PEQ_HOST
#include <sys/stat.h>
/* Info-ZIP's takes the same options; quiet, as it warns of -x patterns nothing matched. */
#define UNZIP "unzip 2>/dev/null"
#else
#define UNZIP "busybox unzip" /* stock busybox 1.31.1 */
#endif
#define CARD PEQ_ROOT "/mnt/mmc"
#define SH_DIR CARD "/.q2"
#define STAGING SH_DIR "/staging"
#define SH_CAP (1u << 30) /* the most an install may unpack */
#define SH_MAX 16

enum { SH_OK, SH_NET, SH_SPACE, SH_CARD, SH_MISSING, SH_BAD, SH_CANCELLED, SH_BUSY, SH_UNSIGNED,
       SH_FORGED };
/* repo: its GitHub owner/name; id: the repo's name less "q2-", for the card folder .<id>, the tag
 * and file list; keep: unzip -x patterns an update never writes (the user's); bin: the app's
 * binary, present without a tag file meaning a manual install, and moved after every other file:
 * power-on runs Rockbox's. dropped: gone from the list but installed, so it can only be removed.
 * The pin, the one zip an install takes: latest the release's tag, asset its zip's name, size its
 * bytes and sha256 their SHA-256; none (latest empty) on a dropped app. */
typedef struct {
    char name[32], id[16], repo[80], dir[17], bin[40], latest[32], dropped;
    const char *keep;
    char asset[64];
    unsigned char sha256[32];
    unsigned size;
} shelf_app;
shelf_app shelf_apps[SH_MAX];
int shelf_n;
#define apps shelf_apps

/* Q2 Muse's own, whose folders, binaries and kept files differ from a card app's (addon.c: .<id>,
 * .<id>/q2-<id>, nothing kept); matched by the repo's name, so a fork is the same app. */
static const struct {
    const char *repo, *id, *dir, *keep, *bin;
} special[] = {
    { "q2-rockbox", "rockbox", ".rockbox", ".rockbox/config.cfg", ".rockbox/rockbox" },
    { "q2-rockbox-themes", "themes", ".rockbox", ".rockbox/config.cfg", "" },
    { "q2-librespot", "spotify", ".spotify", "'.spotify/cache/*' .spotify/.cache-size",
      ".spotify/librespot" },
};
/* The firmware's own card folders, never a card app's: a remove deletes .<id> whole. */
static const char *const reserved[] = { "coverflow", "downpic", "q2", "q2muse", "q2pod", "sldp", "tidal" };

/* Whether id is a special[] app's, which a remove never deletes whole. */
static int special_id(const char *id) {
    for (unsigned k = 0; k < sizeof special / sizeof *special; ++k)
        if (!strcmp(special[k].id, id)) return 1;
    return 0;
}

/* Whether s's n bytes are all a-z0-9- (kind 0, an id), or A-Z too (1, an owner), or ._ too (2). */
static int only(const char *s, unsigned n, int kind) {
    for (unsigned i = 0; i < n; ++i) {
        char c = s[i];
        if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' ||
              (kind && c >= 'A' && c <= 'Z') || (kind > 1 && (c == '.' || c == '_'))))
            return 0;
    }
    return n > 0;
}

/* The word at *p (to the next space or tab, before end) and its length; *p past it and the blanks. */
static unsigned word(const char **p, const char *end, const char **w) {
    const char *q = *w = *p;
    while (q < end && *q != ' ' && *q != '\t') ++q;
    unsigned n = q - *w;
    while (q < end && (*q == ' ' || *q == '\t')) ++q;
    *p = q;
    return n;
}

/* 2n lowercase hex digits at s into out's n bytes; whether all were. */
static int unhex(const char *s, unsigned char *out, unsigned n) {
    for (unsigned i = 0; i < 2 * n; ++i) {
        int d = s[i] >= '0' && s[i] <= '9' ? s[i] - '0' : s[i] >= 'a' && s[i] <= 'f' ? s[i] - 'a' + 10 : -1;
        if (d < 0) return 0;
        out[i / 2] = (unsigned char)(i & 1 ? out[i / 2] << 4 | d : d);
    }
    return 1;
}

/* The pin after an app's repo, "TAG ASSET SIZE SHA256" (the tag 1 to 31 and the asset, ending in
 * .zip, 5 to 63 of A-Za-z0-9._-: they go into the download's URL; the size 1 to SH_CAP), into a;
 * *p past it when it is one. */
static int parse_pin(const char **p, const char *end, shelf_app *a) {
    const char *q = *p, *w[4];
    unsigned n[4], size = 0;
    for (int k = 0; k < 4; ++k) n[k] = word(&q, end, &w[k]);
    if (!n[0] || n[0] >= sizeof a->latest || !only(w[0], n[0], 2) || n[1] < 5 ||
        n[1] >= sizeof a->asset || !only(w[1], n[1], 2) || memcmp(w[1] + n[1] - 4, ".zip", 4) ||
        !n[2] || n[2] > 10 || n[3] != 64 || !unhex(w[3], a->sha256, 32))
        return 0;
    for (unsigned k = 0; k < n[2]; ++k) {
        if (w[2][k] < '0' || w[2][k] > '9' || size > (SH_CAP - (w[2][k] - '0')) / 10) return 0;
        size = size * 10 + (w[2][k] - '0');
    }
    if (!size) return 0;
    memcpy(a->latest, w[0], n[0]), a->latest[n[0]] = 0;
    memcpy(a->asset, w[1], n[1]), a->asset[n[1]] = 0;
    a->size = size, *p = q;
    return 1;
}

/* sources.list's lines, "owner/repo TAG ASSET SIZE SHA256 [Name]" ("#" a comment, "-" before it a
 * dropped app, which needs no pin), into out: at most max, each owner, repo and id checked (they go
 * into URLs and shell commands; the id as addon.c's, 1 to 15 of a-z0-9-), a line without its pin, or
 * with a bad or repeated id, skipped, as is an app the firmware carries as its own (addon_builtin):
 * never offered, installed, updated or removed here; "serial" and "signature" lines are no app's.
 * The count. */
int shelf_sources(const char *text, shelf_app *out, int max) {
    int n = 0;
    for (const char *p = text, *e; p && *p && n < max; p = *e ? e + 1 : e) {
        e = strstr(p, "\n");
        e = e ? e : p + strlen(p);
        while (p < e && (*p == ' ' || *p == '\t')) ++p;
        int dropped = p < e && *p == '-';
        p += dropped;
        const char *end = e, *slash = p, *sp = p;
        while (end > p && (unsigned char)end[-1] <= ' ') --end; /* "\r" too */
        while (sp < end && *sp != ' ' && *sp != '\t') ++sp;
        while (slash < sp && *slash != '/') ++slash;
        const char *repo = slash + 1, *name = sp;
        while (name < end && (*name == ' ' || *name == '\t')) ++name;
        unsigned owner = slash - p, rn = sp < repo ? 0 : sp - repo;
        if (p == end || *p == '#') continue;
        shelf_app a = { .dropped = (char)dropped };
        if (!parse_pin(&name, end, &a) && !dropped) continue; /* nothing a Q2 could check */
        if (dropped) memset(&a, 0, sizeof a), a.dropped = 1; /* its pin, and its last tag, let go */
        unsigned nn = end - name;
        if (owner > 39 || !only(p, owner, 1) || !only(repo, rn, 2) ||
            owner + 1 + rn >= sizeof out->repo || nn >= sizeof out->name)
            continue;
        int bad = 0;
        for (const char *c = name; c < end; ++c) bad |= (unsigned char)*c < ' ' || *c == 127;
        memcpy(a.repo, p, owner + 1 + rn);
        snprintf(a.name, sizeof a.name, "%.*s", (int)(nn ? nn : rn), nn ? name : repo);
        const char *id = rn > 3 && !memcmp(repo, "q2-", 3) ? repo + 3 : repo;
        unsigned in = repo + rn - id;
        for (unsigned k = 0; k < sizeof special / sizeof *special; ++k) {
            if (strlen(special[k].repo) == rn && !memcmp(special[k].repo, repo, rn)) {
                snprintf(a.id, sizeof a.id, "%s", special[k].id);
                snprintf(a.dir, sizeof a.dir, "%s", special[k].dir);
                snprintf(a.bin, sizeof a.bin, "%s", special[k].bin);
                a.keep = special[k].keep;
            } else if (strlen(special[k].id) == in && !memcmp(special[k].id, id, in))
                bad = 1; /* another repo's "spotify" is not Q2 Muse's */
        }
        if (!*a.id) {
            if (in > 15 || !only(id, in, 0)) continue;
            for (unsigned k = 0; k < sizeof reserved / sizeof *reserved; ++k)
                bad |= strlen(reserved[k]) == in && !memcmp(reserved[k], id, in);
            memcpy(a.id, id, in);
            snprintf(a.dir, sizeof a.dir, ".%s", a.id);
            snprintf(a.bin, sizeof a.bin, ".%s/q2-%s", a.id, a.id);
            a.keep = "";
        }
        for (int k = 0; k < n; ++k) bad |= !strcmp(out[k].id, a.id);
        bad |= addon_builtin(a.id); /* the firmware's own: its updates are the firmware's */
        if (!bad) out[n++] = a;
    }
    return n;
}

/* The list's number, its "serial N" line's; 0 without one. */
unsigned shelf_serial(const char *text) {
    for (const char *p = text; p && *p; p = strstr(p, "\n") ? strstr(p, "\n") + 1 : "") {
        unsigned v = 0, k = 0;
        if (strncmp(p, "serial ", 7)) continue;
        for (p += 7; p[k] >= '0' && p[k] <= '9' && k < 9; ++k) v = v * 10 + (p[k] - '0');
        if (k && (!p[k] || p[k] == '\n' || p[k] == '\r')) return v;
    }
    return 0;
}

/* unzip -l's listing of what an install unpacks (busybox's and Info-ZIP's columns: the size, then
 * the name 21 bytes after it): the entries, *total their bytes; -1 for an absolute path, a ".."
 * component, or over cap bytes in all. busybox strips a leading "/" and "../" itself before both
 * listing and unpacking, so this catches only what it leaves. */
int shelf_unpacked(const char *listing, unsigned cap, unsigned *total) {
    unsigned long long sum = 0;
    int n = 0;
    for (const char *p = listing, *e; p && *p; p = *e ? e + 1 : e) {
        const char *s = p, *d;
        e = strstr(p, "\n");
        e = e ? e : p + strlen(p);
        while (*s == ' ') ++s;
        for (d = s; *d >= '0' && *d <= '9'; ++d) {}
        if (d == s || d[0] != ' ' || d[1] != ' ' || d[2] < '0' || d[2] > '9' || e - d <= 21)
            continue; /* the header, its rules and the total */
        sum += (unsigned)atoi(s);
        ++n;
        if (d[21] == '/') return -1;
        for (const char *c = d + 21, *q; c <= e; c = q + 1) {
            for (q = c; q < e && *q != '/'; ++q) {}
            if (q - c == 2 && c[0] == '.' && c[1] == '.') return -1;
        }
    }
    if (sum > cap) return -1;
    *total = (unsigned)sum;
    return n;
}

/* Whether path is one of list's n bytes of NUL-terminated lines. */
int shelf_listed(const char *list, unsigned n, const char *path) {
    for (const char *p = list; p && p < list + n; p += strlen(p) + 1)
        if (!strcmp(p, path)) return 1;
    return 0;
}

/* A file of lines (find's), each NUL-terminated in place; n its bytes. */
static char *lines(const char *path, unsigned *n) {
    char *p = read_all(path, 4u << 20, n);
    for (unsigned i = 0; p && i < *n; ++i)
        if (p[i] == '\n') p[i] = 0;
    return p;
}

/* unzip with opts (and the shell's redirect, after) on app i's download: only its folder, never
 * macOS's ._ files or the kept paths. */
static int unzip(int i, const char *opts, const char *after) {
    char cmd[400];
    snprintf(cmd, sizeof cmd, UNZIP " %s " STAGING "/%s.part '%s/*' -x '*/._*' %s%s", opts,
             apps[i].id, apps[i].dir, apps[i].keep, after);
    return system(cmd);
}

static void staged(char *out, unsigned n, int i, const char *ext) {
    snprintf(out, n, STAGING "/%s%s", apps[i].id, ext);
}

/* The download's listing checked: SH_OK and the bytes it unpacks to in *total, or SH_BAD. */
int shelf_list(int i, unsigned *total) {
    char path[96], after[100];
    unsigned n = 0;
    staged(path, sizeof path, i, ".list");
    snprintf(after, sizeof after, " > %s", path);
    char *listing = unzip(i, "-l -qq", after) ? 0 : read_all(path, 1u << 20, &n);
    int entries = listing ? shelf_unpacked(listing, SH_CAP, total) : -1;
    free(listing);
    unlink(path);
    return entries > 0 ? SH_OK : SH_BAD;
}

/* Unpacked into staging/<id>/. ponytail: the card is FAT32 or exFAT, neither holding symlinks or
 * special files: busybox fails on one (its own error), so nothing checks for them. */
int shelf_unpack(int i) {
    char dir[96], opts[112];
    staged(dir, sizeof dir, i, "");
    mkdir(dir, 0755);
    snprintf(opts, sizeof opts, "-o -qq -d %s", dir);
    return unzip(i, opts, "") ? SH_CARD : SH_OK;
}

static void app_file(char *out, unsigned n, int i, const char *ext) {
    snprintf(out, n, SH_DIR "/apps/%s%s", apps[i].id, ext);
}

/* One staged file (rel, from the card's root) renamed over the card's, its folders made.
 * 0 when moved. */
static int move(int i, const char *rel) {
    char from[512], to[512];
    int at = (int)strlen(CARD "/"),
        a = snprintf(from, sizeof from, STAGING "/%s/%s", apps[i].id, rel),
        b = snprintf(to, sizeof to, CARD "/%s", rel);
    if (a < 0 || a >= (int)sizeof from || b < 0 || b >= (int)sizeof to) return 1;
    for (char *s = to + at; (s = strstr(s, "/")); *s++ = '/') *s = 0, mkdir(to, 0755);
    return rename(from, to) != 0;
}

/* Every staged file into place, the binary last (a rename: never half there), then the list of them
 * as .q2/apps/<id>.files. ponytail: a power cut part way leaves the files moved so far, the old tag
 * and list; a release that drops a file leaves the old one on the card and off the list. */
int shelf_place(int i) {
    char cmd[256], list[96], files[96];
    unsigned n = 0;
    int err = 0;
    staged(list, sizeof list, i, ".files");
    app_file(files, sizeof files, i, ".files");
    snprintf(cmd, sizeof cmd, "cd " STAGING "/%s && find %s -type f > %s", apps[i].id, apps[i].dir,
             list);
    char *rel = system(cmd) ? 0 : lines(list, &n);                /* FAT names hold no newline */
    for (int last = 0; rel && last < 2 && !(last && err); ++last) /* no new binary by a part set */
        for (const char *p = rel; p < rel + n; p += strlen(p) + 1) {
            int bin = *apps[i].bin && !strcmp(p, apps[i].bin);
            if (bin == last) err |= move(i, p);
        }
    free(rel);
    err = !rel || !n || err || rename(list, files);
    system("sync");
    return err ? SH_CARD : SH_OK;
}

/* Remove: app i's listed files, but those another app lists too (two fonts are both Rockbox's and
 * the themes'), then the folders that left empty, up to and with its own; then its list and tag.
 * A card app's folder goes whole, with what the app saved there (a login among it). */
void shelf_remove(int i) {
    char path[512];
    unsigned n = 0, m[SH_MAX] = { 0 };
    char *other[SH_MAX] = { 0 };
    for (int j = 0; j < shelf_n; ++j)
        if (j != i) app_file(path, sizeof path, j, ".files"), other[j] = lines(path, &m[j]);
    if (*apps[i].bin) { /* first: a part remove still boots Q2 Muse */
        snprintf(path, sizeof path, CARD "/%s", apps[i].bin);
        unlink(path);
    }
    app_file(path, sizeof path, i, ".files");
    char *list = lines(path, &n);
    for (const char *p = list; p && p < list + n; p += strlen(p) + 1) {
        int shared = 0;
        for (int j = 0; j < shelf_n; ++j) shared |= shelf_listed(other[j], m[j], p);
        snprintf(path, sizeof path, CARD "/%s", p);
        if (*p && !shared) unlink(path);
        for (char *s; (s = strrchr(path, '/')) > path + strlen(CARD);) {
            *s = 0;
            if (rmdir(path)) break; /* not empty: nor is any folder above */
        }
    }
    free(list);
    for (int j = 0; j < shelf_n; ++j) free(other[j]);
    /* dir is shelf_sources' checked ".<id>". ponytail: addon_quit waits QUIT_MS (20 s), so an app
     * still saving on its TERM past that can write files back. */
    if (!special_id(apps[i].id)) {
        snprintf(path, sizeof path, "rm -rf '" CARD "/%s'", apps[i].dir);
        system(path);
    }
    app_file(path, sizeof path, i, ".files");
    unlink(path);
    app_file(path, sizeof path, i, ".tag");
    unlink(path);
}

#ifndef PEQ_HOST
#include "offsets.inc"
#include "stock.h"

#define NU_REPO "https://github.com/" Q2POD_REPO "/releases" /* tools/build.py --update-repo */
#define NU_ASSET "update.tar"  /* and NU_ASSET ".sig", tools/release.py's */
#define NU_FILE "/mnt/mmc/update.tar"
#define NU_SIG "/tmp/q2-update.sig"
#define NU_CA "/etc/scrobble-ca.pem" /* scrobble.c's; github.com's and its asset host's roots */
#define NU_CHECK_S 5L /* a check's request, connect and all: the two of them about 10 s at most */


static struct {
    unsigned long thread;
    volatile int state, cancel, percent; /* cancel, percent: progress()'s pair */
    volatile int verifying; /* the download is whole and its signature being checked */
} nu __attribute__((section(".scratch")));

extern int stock_fwupdate_trampoline(void *win, void *ctx);

/* Pacing (docs/internals.md#bluetooth-coexistence-pacing): q2guard's /tmp/q2-bt-coex
 * (patch/q2bt.h's BT_PACE_FILE) says "paced=1 rate=BYTES" while A2DP streams with Wi-Fi joined
 * on 2.4 GHz, where the Q2's own downloads take the airtime the headset's link needs (the stall
 * cluster of 2026-11-24 was the Q2 receiving 1.5 to 2 MB/s). A transfer's write callback, on its
 * worker thread, then holds it to rate bytes a second; the socket's receive window passes that back
 * to the server. The file is read at most once a second, so a stream's start or end reaches a
 * running download within one. */
#define PACE_FILE "/tmp/q2-bt-coex"
#define PACE_MAX 100000000 /* bytes a second: a rate past it is no rate written by q2guard */
static struct {
    unsigned read_at, at, sent; /* the file's last read; this second's start and bytes */
    int rate;                   /* bytes a second, 0 unpaced */
} pace __attribute__((section(".scratch")));

/* The rate in the pace file's text: 0 unless it says paced=1 with a rate of 1 to PACE_MAX */
int netupdate_pace_rate(const char *text) {
    const char *r = strstr(text, "rate=");
    unsigned v = 0;
    if (strncmp(text, "paced=1", 7) || !r) return 0;
    for (r += 5; *r >= '0' && *r <= '9'; ++r)
        if ((v = v * 10 + (unsigned)(*r - '0')) > PACE_MAX) return 0;
    return (int)v;
}
static void pace_wait(unsigned n) {
    unsigned now = time_now_ms();
    if (!pace.read_at || now - pace.read_at >= 1000) {
        char text[64] = "";
        void *f = fopen(PACE_FILE, "r");
        if (f) text[fread(text, 1, sizeof text - 1, f)] = 0, fclose(f);
        pace.rate = netupdate_pace_rate(text), pace.read_at = now | !now;
    }
    if (!pace.rate) return;
    if (!pace.at || now - pace.at >= 1000) pace.at = now | !now, pace.sent = 0;
    pace.sent += n;
    unsigned due = pace.sent / (unsigned)(pace.rate / 1000 ? pace.rate / 1000 : 1),
             spent = now - pace.at;
    if (due > spent) usleep((due - spent) * 1000);
}

static unsigned save(const void *data, unsigned size, unsigned n, void *file) {
    unsigned wrote = fwrite(data, size, n, file) * size;
    pace_wait(wrote);
    return wrote;
}

/* xfer: a cancel flag followed by a percentage, nu's or the Apps page's. */
static int progress(void *xfer, long long total, long long now, long long c, long long d) {
    volatile int *x = xfer;
    if (total >= 1024)
        x[1] = (unsigned)(now >> 10) * 100 / (unsigned)(total >> 10); /* KB: 32-bit math */
    return x[0];
}

/* A transfer of url with what every one of ours sets: off the UI thread, GitHub's roots checked, at
 * most connect_s to connect. 0 when curl can't. */
static void *easy(const char *url, long connect_s) {
    void *c = curl_easy_init();
    if (!c) return 0;
    curl_easy_setopt(c, 10002, url);       /* CURLOPT_URL */
    curl_easy_setopt(c, 10018, "Q2Muse");  /* USERAGENT */
    curl_easy_setopt(c, 99, 1L);           /* NOSIGNAL: off the UI thread */
    curl_easy_setopt(c, 78, connect_s);    /* CONNECTTIMEOUT */
    curl_easy_setopt(c, 10065, NU_CA);     /* CAINFO */
    curl_easy_setopt(c, 64, 1L);           /* SSL_VERIFYPEER */
    curl_easy_setopt(c, 81, 2L);           /* SSL_VERIFYHOST */
    return c;
}

/* The asset to file, its progress in xfer. */
static int fetch(const char *url, void *file, long *code, volatile int *xfer) {
    void *c = easy(url, 15);
    if (!c) return 1;
    curl_easy_setopt(c, 52, 1L);          /* FOLLOWLOCATION: to the asset host */
    curl_easy_setopt(c, 20011, save);     /* WRITEFUNCTION */
    curl_easy_setopt(c, 10001, file);     /* WRITEDATA */
    curl_easy_setopt(c, 19, 1024L);       /* LOW_SPEED_LIMIT: a stalled transfer ends */
    curl_easy_setopt(c, 20, 30L);         /* LOW_SPEED_TIME */
    curl_easy_setopt(c, 20219, progress); /* XFERINFOFUNCTION */
    curl_easy_setopt(c, 10057, xfer);     /* XFERINFODATA */
    curl_easy_setopt(c, 43, 0L);          /* NOPROGRESS off, for it */
    /* curl itself fails a body shorter than its Content-Length (PARTIAL_FILE). */
    int err = curl_easy_perform(c) || curl_easy_getinfo(c, 0x200002, code); /* RESPONSE_CODE */
    curl_easy_cleanup(c);
    return err;
}

/* Headers alone, in at most secs: the latest release's tag from releases/latest's redirect (tag,
 * no redirect followed), or an asset's size in KB through its redirects (kb). */
static int head(const char *url, long secs, char *tag, unsigned n, unsigned *kb, long *code) {
    void *c = easy(url, secs);
    double length = -1;
    char *location = 0;
    if (!c) return 1;
    curl_easy_setopt(c, 44, 1L);   /* NOBODY */
    curl_easy_setopt(c, 13, secs); /* TIMEOUT */
    if (kb) curl_easy_setopt(c, 52, 1L);
    int err = curl_easy_perform(c) || curl_easy_getinfo(c, 0x200002, code); /* RESPONSE_CODE */
    if (!err && tag && !curl_easy_getinfo(c, 0x10001f, &location)) {        /* REDIRECT_URL */
        const char *t = netupdate_tag(location);
        tk_snprintf(tag, n, "%s", t ? t : "");
    }
    if (!err && kb && !curl_easy_getinfo(c, 0x30000f, &length)) /* CONTENT_LENGTH_DOWNLOAD */
        *kb = length > 0 ? (unsigned)(length / 1024) : 0;
    curl_easy_cleanup(c);
    return err;
}

/* Tags are single-digit x.y.z, so strcmp orders them: nothing older than this build, which stock's
 * updater would flash. Q2POD_TAG is empty in --dev builds, which take any tag.
 * ponytail: a beta (BETA in tools/build.py) refuses its own version's release too, so its testers
 * install that from the card; take an equal tag under a beta if they shouldn't have to. */
static int newer(const char *tag) { return !*Q2POD_TAG || strcmp(tag, Q2POD_TAG) > 0; }

/* Software Update's online check (sw_update.c), on its thread: the latest release's tag into tag
 * and its update.tar's size in KB into *kb. NET_NEWER, NET_LATEST, or NET_FAILED (no link, no
 * DNS, GitHub not answering in NU_CHECK_S a request, or no update.tar on the release). */
int netupdate_check(char *tag, unsigned n, unsigned *kb) {
    char url[160];
    long code = 0;
    *kb = 0;
    if (head(NU_REPO "/latest", NU_CHECK_S, tag, n, 0, &code) || code != 302 || !*tag)
        return NET_FAILED;
    if (!newer(tag)) return NET_LATEST;
    snprintf(url, sizeof url, NU_REPO "/download/%s/" NU_ASSET, tag);
    return head(url, NU_CHECK_S, 0, 0, kb, &code) || code != 200 ? NET_FAILED : NET_NEWER;
}

/* The release's update.tar.sig into sig: NU_DONE, NU_UNSIGNED when the release has none (q2-pod's
 * upstream releases, or a release not yet signed) or it isn't 64 bytes, NU_NET or NU_CARD. */
static int signature(const char *tag, unsigned char sig[64]) {
    char url[160];
    unsigned n = 0;
    long code = 0;
    snprintf(url, sizeof url, NU_REPO "/download/%s/" NU_ASSET ".sig", tag);
    void *f = fopen(NU_SIG, "wb");
    if (!f) return NU_CARD;
    int err = fetch(url, f, &code, &nu.cancel);
    err = fclose(f) || err;
    unsigned char *got = err || code != 200 ? 0 : read_all(NU_SIG, 64, &n);
    unlink(NU_SIG);
    int r = err ? NU_NET : code == 404 || (code == 200 && n != 64) ? NU_UNSIGNED
                         : code != 200                           ? NU_NET
                                                                 : NU_DONE;
    if (r == NU_DONE) memcpy(sig, got, 64);
    free(got);
    return r;
}

static void *worker(void *unused) {
    char tag[64] = "", url[160];
    unsigned char sig[64];
    long code = 0;
    if (head(NU_REPO "/latest", 30, tag, sizeof tag, 0, &code) || code != 302 || !*tag) {
        nu.state = NU_NET;
        return 0;
    }
    if (!newer(tag)) {
        nu.state = NU_LATEST;
        return 0;
    }
    /* The signature before the tar: a release without one downloads nothing. */
    int state = nu.cancel ? NU_CANCELLED : signature(tag, sig);
    if (state != NU_DONE || nu.cancel) { /* pressed while checking, or the page was left */
        nu.state = nu.cancel ? NU_CANCELLED : state;
        return 0;
    }
    /* The old update.tar stays until the new one is whole and its signature checks. The tag's own
     * asset, not releases/latest's: the one the signature names. ponytail: needs the tar's ~55 MB free
     * beside it. */
    snprintf(url, sizeof url, NU_REPO "/download/%s/" NU_ASSET, tag);
    void *f = fopen(NU_FILE ".part", "wb");
    int err = !f;
    state = NU_CARD;
    if (f) {
        err = fetch(url, f, &code, &nu.cancel);
        state = err ? (ferror(f) ? NU_CARD : NU_NET) : code != 200 ? NU_MISSING : NU_CARD;
        err = err || code != 200 || fflush(f) || fsync(fileno(f)) || ferror(f);
        err = fclose(f) || err;
    }
    /* The bytes stock will flash, read back from the card: SHA-512 of 55 MB takes seconds. */
    if (!err && !nu.cancel) {
        nu.verifying = 1;
        int v = sig_update(NU_FILE ".part", tag, sig, &nu.cancel);
        err = v != SIG_OK;
        state = v == SIG_BAD ? NU_FORGED : v == SIG_IO ? NU_CARD : state;
    }
    /* Read once, after the close and the check: a press while the card took the last bytes (the fsync
     * of the whole part, seconds on a slow card, the label at 100%) or while the signature was
     * checked is a cancel too, not "Can't write". */
    if (nu.cancel) state = NU_CANCELLED;
    if (!err && state != NU_CANCELLED && !rename(NU_FILE ".part", NU_FILE)) { /* a late cancel still cancels */
        void *dir = fopen("/mnt/mmc", "r"); /* the rename's directory entry, before stock flashes */
        if (dir) fsync(fileno(dir)), fclose(dir);
        state = NU_DONE;
    } else
        unlink(NU_FILE ".part");
    nu.state = state;
    return 0;
}

/* What a download that ended without update.tar says, as a toast. */
const char *netupdate_said(int state) {
    static const char *const said[] = {
        [NU_LATEST] = "Latest firmware installed", [NU_NET] = "Can't reach GitHub",
        [NU_MISSING] = "No update for this build", [NU_CARD] = "Can't write to the card",
        [NU_CANCELLED] = "Download canceled",
        [NU_UNSIGNED] = "Release not signed: not installed",
        [NU_FORGED] = "Signature check failed: not installed",
    };
    return state > NU_DONE && state <= NU_FORGED ? said[state] : "";
}

/* Software Update's Download and Install (sw_update.c): the download on its thread; 0 when it
 * can't start. netupdate_poll: NU_RUNNING (*percent and *verifying its progress) until it ends,
 * then its state, once, joined; netupdate_cancel: the transfer ends at its next progress call. */
int netupdate_start(void) {
    if (nu.state != NU_IDLE) return 0;
    nu.cancel = nu.percent = nu.verifying = 0;
    nu.state = NU_RUNNING;
    if (!pthread_create(&nu.thread, 0, worker, 0)) return 1;
    nu.state = NU_IDLE;
    return 0;
}
int netupdate_poll(int *percent, int *verifying) {
    int state = nu.state;
    *percent = nu.percent, *verifying = nu.verifying;
    if (state == NU_RUNNING) reset_poweroptions_timer(1, 1, 0); /* standby, auto power-off */
    if (state == NU_RUNNING || state == NU_IDLE) return state;
    pthread_join(nu.thread, 0);
    nu.state = NU_IDLE;
    return state;
}
void netupdate_cancel(void) {
    if (nu.state == NU_RUNNING) nu.cancel = 1;
}

/* systemset_fwupdate_page_init: stock's two rows (0x4c8660), each button named by its index, which
 * stock's click (0x4c84cc) reads with atoi: 0 TF card update, 1 Shanling's server. General's
 * Software Update row opens sw_update.c's page instead of this one; should stock's page open
 * anyway, its second row, renamed "2" so stock's click does nothing, opens that page too. */
int ringnav_fwupdate(void *win, void *ctx) {
    int result = stock_fwupdate_trampoline(win, ctx);
    void *view = win ? widget_lookup(win, "scroll_view_fwupdate", 1) : (void *)0;
    void *button = view && widget_count_children(view) == 2 ? widget_get_child(widget_get_child(view, 1), 0) : 0;
    if (button) {
        widget_set_name(button, "2");
        widget_on(button, EVT_CLICK, sw_update_open, 0);
    }
    return result;
}

/* The Apps page (System settings): Check for Updates, then a row per app in sources.list, its state
 * as the row's detail. One job at a time on one thread: a check (the signed list, its pins) on
 * opening or from Check for Updates, an install, a remove or Spotify's erase (Streaming →
 * Configuration's, shelf_erase). Leaving the page leaves it running; its progress shows when the
 * page opens again. */
#define SH_JSON "/tmp/q2-shelf.json"
#define SH_LIST SH_DIR "/sources.list" /* the last list fetched, for the page offline */
#define SH_LIST_URL "https://raw.githubusercontent.com/" Q2POD_REPO "/main/sources.list" /* --update-repo */
#define SH_RESERVE (64ull << 20) /* left free on the card beyond a download or what it unpacks */
#define SH_STALE_MS (10 * 60 * 1000ull)
#define SH_RECHECK_MS (60 * 1000ull) /* Check for Updates' least gap */
enum { JOB_CHECK = 1, JOB_INSTALL, JOB_REMOVE, JOB_ERASE };
enum { ST_INSTALL, ST_UPDATE, ST_INSTALLED };
enum { ROW_CHECK = SH_MAX }; /* map's row that is no app: Check for Updates, the first */

static struct {
    unsigned long thread;
    volatile int running, cancel, percent, stage, result; /* cancel, percent: progress()'s pair */
    int job, app, armed, rows, n, map[SH_MAX + 1];        /* map: a row's app or ROW_CHECK */
    int manual;                                           /* the check is Check for Updates' */
    unsigned need;                                        /* SH_SPACE's MB */
    unsigned long long checked;                           /* now_ms of the last whole check */
    shelf_app *next; /* SH_MAX: the check's list (n apps), the page's once the check ends; on the
                      * heap from the first check, kept */
    void *page, *row[SH_MAX + 1];
} sh __attribute__((section(".scratch")));

static int is(int i, const char *id) { return i >= 0 && i < shelf_n && !strcmp(apps[i].id, id); }

static int sh_find(const char *id) {
    for (int i = 0; i < shelf_n; ++i)
        if (is(i, id)) return i;
    return -1;
}

/* The folder (".<id>") an install or remove is replacing, else 0: addon.c starts no app there. */
const char *shelf_busy(void) {
    return sh.running && sh.job != JOB_CHECK && sh.job != JOB_ERASE ? apps[sh.app].dir : 0;
}

/* The folder a remove is deleting or an install moving into place (stage 3 on), else 0: addon.c
 * opens no page there. */
const char *shelf_moving(void) { return sh.stage >= 3 ? shelf_busy() : 0; }

/* glibc 2.28 MIPS o32 stat v3: st_mode at byte 20; 0 when path is missing. */
static unsigned file_mode(const char *path) {
    unsigned info[36];
    return __xstat(3, path, info) ? 0 : info[5] & 0170000;
}

/* url's body (1 MB at most) through SH_JSON, NUL-terminated, *n its bytes; 0 unless a whole 200. */
static char *get(const char *url, long *code, unsigned *n) {
    void *f = fopen(SH_JSON, "wb");
    int err = !f || fetch(url, f, code, &sh.cancel);
    err = (f && fclose(f)) || err;
    char *body = err || *code != 200 ? 0 : read_all(SH_JSON, 1u << 20, n);
    unlink(SH_JSON);
    sh.percent = 0;
    return body;
}

/* The list as it is now, its signature line cut off, or 0 and *why: SH_NET, SH_BUSY (GitHub's rate
 * limit), or SH_UNSIGNED when the release key didn't sign it (docs/internals.md#release-signatures)
 * or its serial is below the card's list's, an older signed list served again. */
static char *fresh_list(int *why) {
    unsigned n = 0, have = 0;
    long code = 0;
    char *list = get(SH_LIST_URL, &code, &n), *card = read_all(SH_LIST, 1u << 16, &have);
    unsigned body = list ? sig_list(list, n) : 0;
    if (body) list[body] = 0;
    have = card ? shelf_serial(card) : 0;
    free(card);
    *why = code == 403 || code == 429 ? SH_BUSY : !list ? SH_NET : SH_UNSIGNED;
    if (!body || shelf_serial(list) < have) free(list), list = 0;
    return list;
}

/* A check: the list fetched into sh.next, kept as SH_LIST with any app it dropped that is still
 * installed (so it can be removed), else the page's own. Each app's latest is its pin's tag, so a
 * check is one request, never one per app. A dropped app is never installed: dropping it is what
 * stops a taken-over repo's releases reaching a Q2 (GHSA-8wpr-pmqx-538x), as a pin does for any
 * other. Whether the list was fetched: one that failed is fetched again with the next check. */
static int check(void) {
    char path[96], hex[65], text[SH_MAX * 320];
    unsigned m = 0;
    int why = SH_NET;
    char *list = fresh_list(&why);
    unsigned serial = list ? shelf_serial(list) : 0;
    if (!sh.next && !(sh.next = calloc(SH_MAX, sizeof *sh.next))) { /* kept from the first check */
        free(list);
        return 0;
    }
    int n = list ? shelf_sources(list, sh.next, SH_MAX) : 0, fresh = n > 0;
    free(list);
    if (!n) memcpy(sh.next, apps, sizeof apps), n = shelf_n; /* a bad list never replaces one */
    for (int i = 0; fresh && i < shelf_n && n < SH_MAX; ++i) {
        int listed = 0;
        for (int k = 0; k < n; ++k) listed |= !strcmp(sh.next[k].id, apps[i].id);
        app_file(path, sizeof path, i, ".tag");
        if (!listed && file_mode(path))
            sh.next[n] = apps[i], sh.next[n].dropped = 1, *sh.next[n++].latest = 0;
    }
    /* the card's copy, for the page offline and the next list's serial: pins and all, unsigned, as
     * the device wrote it; an install fetches the signed list again */
    if (fresh) m = snprintf(text, sizeof text, "serial %u\n", serial);
    for (int k = 0; fresh && k < n; ++k) {
        shelf_app *a = &sh.next[k];
        sig_hex(a->sha256, 32, hex);
        if (a->dropped)
            m += snprintf(text + m, sizeof text - m, "-%s %s\n", a->repo, a->name);
        else
            m += snprintf(text + m, sizeof text - m, "%s %s %s %u %s %s\n", a->repo, a->latest, a->asset,
                          a->size, hex, a->name);
    }
    if (fresh) mkdir(SH_DIR, 0755), blob_io(SH_LIST, SH_LIST ".tmp", text, m, 1);
    sh.n = n;
    if (!fresh) sh.result = why; /* said after Check for Updates; the next check fetches it again */
    return fresh;
}

/* App i's pin from the signed list as it is now, fetched for this install, into *pin: SH_OK, the
 * list's SH_NET, SH_BUSY or SH_UNSIGNED with no list to be had, SH_MISSING once the repo is dropped
 * or gone from it, SH_CANCELLED when cancelled meanwhile. The page's list is the card's, unsigned
 * and maybe days old (opened with no Wi-Fi, the opening check stopped by a press;
 * GHSA-8wpr-pmqx-538x), so nothing an install takes comes from it. */
static int listed(int i, shelf_app *pin) {
    shelf_app now[SH_MAX];
    int why = SH_NET;
    char *list = fresh_list(&why);
    int n = list ? shelf_sources(list, now, SH_MAX) : 0, r = list ? SH_MISSING : why;
    free(list);
    for (int k = 0; k < n; ++k)
        if (!now[k].dropped && !strcmp(now[k].repo, apps[i].repo)) r = SH_OK, *pin = now[k];
    return sh.cancel ? SH_CANCELLED : r;
}

/* SH_OK when the card has bytes and SH_RESERVE free; else SH_SPACE and sh.need the MB short. */
static int room(unsigned bytes) {
    unsigned fs[32] = { 0 }; /* statfs, MIPS o32: f_bsize word 1, f_bavail word 7 */
    if (statfs(CARD, fs)) return SH_CARD;
    unsigned long long have = (unsigned long long)fs[7] * fs[1], want = bytes + SH_RESERVE;
    if (have >= want) return SH_OK;
    sh.need = (unsigned)((want - have + (1u << 20) - 1) >> 20);
    return SH_SPACE;
}

/* App i from its pin: the zip at the pinned tag's release, nothing else unpacked unless its SHA-256
 * is the pin's (SH_FORGED). Cancel lands during the download, else after the step it was pressed in;
 * never once moving starts. */
static int install(int i, const shelf_app *pin) {
    char url[256], part[96], path[96];
    unsigned char got[32];
    unsigned total = 0;
    long code = 0;
    if (apps[i].dropped) return SH_MISSING;
    if (sh.cancel) return SH_CANCELLED;
    snprintf(apps[i].latest, sizeof apps[i].latest, "%s", pin->latest);
    snprintf(url, sizeof url, "https://github.com/%s/releases/download/%s/%s", pin->repo, pin->latest,
             pin->asset);
    system("rm -rf " STAGING); /* an interrupted install's, too */
    mkdir(SH_DIR, 0755), mkdir(STAGING, 0755), mkdir(SH_DIR "/apps", 0755);
    int r = room(pin->size);
    if (r) return r;
    staged(part, sizeof part, i, ".part");
    void *f = fopen(part, "wb");
    if (!f) return SH_CARD;
    sh.stage = 1;
    int err = fetch(url, f, &code, &sh.cancel), bad = ferror(f);
    bad = fclose(f) || bad;
    r = sh.cancel ? SH_CANCELLED : bad ? SH_CARD : err ? SH_NET : code != 200 ? SH_MISSING : SH_OK;
    if (!r) { /* read back from the card, as unzip will read it */
        int h = sig_hash_file(part, 0, got, &sh.cancel);
        r = h == SIG_CANCELLED ? SH_CANCELLED : h ? SH_CARD : memcmp(got, pin->sha256, 32) ? SH_FORGED : SH_OK;
    }
    if (!r) sh.stage = 2, r = shelf_list(i, &total);
    if (!r) r = room(total);
    if (!r) r = shelf_unpack(i);
    if (!r && sh.cancel) r = SH_CANCELLED;
    if (!r) {
        sh.stage = 3;
        if (is(i, "spotify"))
            system(SPOT_KILL);   /* librespot runs from .spotify, if started since */
        addon_quit(apps[i].dir); /* a card app, the same: its files closed first */
        app_file(path, sizeof path, i, ".tag");
        r = shelf_place(i);
        char tmp[100];
        snprintf(tmp, sizeof tmp, "%s.tmp", path);
        if (!r && !blob_io(path, tmp, (void *)pin->latest, strlen(pin->latest), 1)) r = SH_CARD;
    }
    system("rm -rf " STAGING);
    return r;
}

static void *sh_worker(void *unused) {
    int i = sh.app, t = sh_find("themes");
    if (sh.job == JOB_CHECK) {
        if (check() && !sh.cancel) sh.checked = now_ms();
    } else if (sh.job == JOB_INSTALL) {
        shelf_app pin;
        int r = listed(i, &pin);
        sh.result = r ? r : install(i, &pin);
    } else {
        /* Erase Spotify Data: the add-on's logs go too, as spot.log names the account and each
         * track played */
        if (sh.job == JOB_ERASE)
            system("rm -rf " SPOT_DIR "/cache " SPOT_DIR "/.cache-size " SPOT_DIR
                   "/spot.log " SPOT_DIR "/aplay.log " SPOT_DIR "/load.log"),
                rmdir(SPOT_DIR);
        else {
            addon_quit(apps[i].dir); /* USB storage may take the card meanwhile */
            /* ponytail: read once; USB storage taking the card during the unlinks still says removed */
            if (card_mounted()) {
                if (is(i, "rockbox") && t >= 0) shelf_remove(t); /* no themes without Rockbox */
                shelf_remove(i);
            } else
                sh.result = SH_CARD; /* the bare mount point: nothing removed */
        }
        system("sync");
    }
    sh.running = 0;
    return 0;
}

/* With a tag file ST_INSTALLED, or ST_UPDATE when the latest differs; -1 without one. */
static int sh_tagged(int i) {
    char path[96], tag[40];
    app_file(path, sizeof path, i, ".tag");
    state_read(path, tag, sizeof tag); /* "\n" and the tag */
    if (!tag[1]) return -1;
    return *apps[i].latest && strcmp(tag + 1, apps[i].latest) ? ST_UPDATE : ST_INSTALLED;
}

/* sh_tagged's state, else ST_UPDATE for a manual install. */
static int sh_state(int i) {
    char path[96];
    int s = sh_tagged(i);
    if (s >= 0) return s;
    if (*apps[i].bin) {
        snprintf(path, sizeof path, CARD "/%s", apps[i].bin);
        if (file_mode(path) == 0100000) return ST_UPDATE;
    }
    return ST_INSTALL;
}

static void sh_text(int r) {
    static const char *const stages[] = { "Checking…", "Downloading", "Unpacking…", "Installing…",
                                          "Removing…" };
    static const char *const states[] = { "Install", "Update", "Installed" };
    int i = sh.map[r], armed = sh.armed == r + 1,
        busy = (sh.job == JOB_INSTALL || sh.job == JOB_REMOVE) && sh.app == i;
    char s[40];
    const char *t = s;
    if (i == ROW_CHECK && sh.job == JOB_CHECK)
        t = "Checking…";
    else if (i == ROW_CHECK) { /* with no list yet, the press loads it; OLED says so under the row,
                                * the row alone a plain one, the list's pitch (oled-design.md Empty
                                * and offline: one line in the list's pitch) */
        if (!shelf_n && sh.row[r]) { /* the list lays its items out at its item_height */
            void *view = P(sh.row[r], W_PARENT), *lv = view ? P(view, W_PARENT) : (void *)0;
            if (lv) widget_set_prop_int(lv, "item_height", page_h(48));
            widget_resize(sh.row[r], 375, page_h(48)); /* full width */
        }
        int noted = feedback_empty(sh.page, feedback_below(sh.row[r]),
                                   shelf_n ? (const char *)0 : "No apps listed",
                                   "Connect to Wi-Fi, then Check for Updates");
        t = !shelf_n && !noted ? "Connect to Wi-Fi to load the list" : "";
        void *title = noted ? widget_get_child(sh.row[r], 0) : (void *)0;
        if (title) /* OLED: one line, centred in the list's pitch, as its other rows keep it */
            widget_move_resize(title, I(title, W_X), 0, I(title, W_W), I(sh.row[r], W_H));
    } else if (busy && sh.cancel)
        t = "Canceling…";
    else if (busy && sh.stage == 1 && sh.percent)
        snprintf(s, sizeof s, "Downloading %d%%", sh.percent);
    else if (busy)
        t = stages[sh.stage];
    else if (armed && sh_state(i) == ST_INSTALLED)
        t = "Press again to remove";
    else if (armed) /* docs/boot.md#rockbox */
        t = "Starts at power-on; Boot to changes that. Press again to install";
    else
        t = states[sh_state(i)];
    widget_set_text_utf8(widget_get_child(sh.row[r], 1), t);
}

static void sh_rows(void);
static void sh_refresh(void) {
    for (int r = 0; sh.page && r < sh.rows; ++r) sh_text(r);
}

/* A check's toast: how many rows' apps have a newer release than their tag file's (a manual
 * install's Update isn't counted: its version is unknown); with none, from Check for Updates
 * (manual), why it failed or that none have. */
static void sh_told(int manual, const char *failed) {
    char s[40];
    int n = 0;
    for (int r = 1; r < sh.rows; ++r) n += sh_tagged(sh.map[r]) == ST_UPDATE; /* past ROW_CHECK */
    if (n)
        snprintf(s, sizeof s, "%d app%s can be updated", n, n == 1 ? "" : "s"), toast(s);
    else if (manual && failed)
        toast(failed);
    else if (manual)
        toast("All apps are up to date");
}

static int sh_wait(const void *unused) {
    static const char *const said[] = {
        [SH_NET] = "Can't reach GitHub",           [SH_CARD] = "Can't write to the card",
        [SH_MISSING] = "No update for this build", [SH_BAD] = "The download is not a usable app",
        [SH_CANCELLED] = "Download canceled",     [SH_BUSY] = "GitHub is busy, try later",
        [SH_UNSIGNED] = "App list not signed: not used",
        [SH_FORGED] = "Download doesn't match the list",
    };
    char s[64];
    if (sh.running) {
        if (sh.job != JOB_CHECK) reset_poweroptions_timer(1, 1, 0); /* standby, auto power-off */
        sh_refresh();
        return 8; /* RET_REPEAT */
    }
    pthread_join(sh.thread, 0);
    int job = sh.job, r = sh.result, i = sh.app, manual = sh.manual;
    sh.job = 0;
    /* its files replaced or gone: an update that failed or was cancelled before it stopped its app leaves it running, still tracked,
     * and a remove cut off by USB storage leaves it marked to start again with the card */
    if (r == SH_OK && (job == JOB_REMOVE || job == JOB_INSTALL))
        addon_forget(apps[i].dir);
    if (job == JOB_CHECK) /* the rows built again: the list may have changed */
        if (sh.next) memcpy(apps, sh.next, sizeof apps), shelf_n = sh.n, sh.rows = -1;
    if (job == JOB_ERASE || (job != JOB_CHECK && is(i, "spotify")))
        spot_stop(); /* spotify.c forgets the old one */
    sh_rows();
    if (job == JOB_CHECK && !sh.cancel && sh.page &&
        window_manager_get_top_window(window_manager()) == sh.page) /* not left or covered */
        sh_told(manual, r ? said[r] : 0);
    else if (job == JOB_INSTALL && r == SH_SPACE)
        snprintf(s, sizeof s, "Needs %u MB more space", sh.need), toast(s);
    else if ((job == JOB_INSTALL || job == JOB_REMOVE) && r)
        toast(said[r]);
    else if (job == JOB_INSTALL && is(i, "rockbox"))
        toast("Rockbox is ready. Find it on Home");
    else if (job == JOB_INSTALL && is(i, "themes"))
        toast("Rockbox Themes are ready in Rockbox's Theme Settings");
    else if (job == JOB_INSTALL)
        snprintf(s, sizeof s, "%s is ready in %s", apps[i].name,
                 addon_is_game(apps[i].id) ? "Library's Games" : "Streaming"),
            toast(s);
    else if (job == JOB_REMOVE)
        snprintf(s, sizeof s, "%s removed", apps[i].name), toast(s);
    else if (job == JOB_ERASE)
        stream_erased(), toast("Spotify's login and cache erased"); /* rows first: toast blocks */
    return 0;
}

static void sh_start(int job, int i) {
    if (job == JOB_ERASE || (is(i, "spotify") && job != JOB_CHECK))
        spot_stop(); /* on this thread: spotify.c's state too */
    sh.job = job, sh.app = i, sh.cancel = sh.percent = sh.result = sh.manual = 0, sh.running = 1;
    sh.stage = job == JOB_REMOVE ? 4 : 0;
    if (pthread_create(&sh.thread, 0, sh_worker, 0)) {
        sh.job = sh.running = 0;
        toast("Can't start the download");
    } else
        timer_add(sh_wait, (void *)0, 500);
}

/* Install and Update at once; Installed's Remove and Rockbox's first Install (it becomes the
 * power-on default) on a second press of the same row. Pressed again, the running install cancels.
 * Check for Updates checks now (loading the list, with none yet), unless the last whole check is
 * under SH_RECHECK_MS old: then it says that check's count again. */
static int sh_click(void *ctx, void *event) {
    int r = (int)(long)ctx, i = sh.map[r], armed = sh.armed == r + 1;
    sh.armed = 0;
    if (i == ROW_CHECK && sh.job == JOB_CHECK) {
        sh.manual = 1; /* checking already, the opening check maybe: its end answers the press */
    } else if (sh.job == JOB_CHECK) { /* needs no answer: stopped, so the next press acts */
        sh.cancel = 1;
        toast("Stopped checking for updates");
    } else if (sh.job) {
        if (sh.job == JOB_INSTALL && sh.app == i && sh.stage < 3)
            sh.cancel = 1; /* not once moving */
        else if (sh.job != JOB_INSTALL || sh.app != i)
            toast("Wait for the one in progress");
    } else if (i != ROW_CHECK && sh_state(i) == ST_INSTALLED) {
        if (!armed)
            sh.armed = r + 1;
        else
            sh_start(JOB_REMOVE, i);
    } else if (!need_link(sh_click, ctx)) {
        /* held for Smart Wi-Fi's link (a press while it waits takes its place), or none */
    } else if (i == ROW_CHECK && sh.checked && now_ms() - sh.checked < SH_RECHECK_MS)
        sh_told(1, 0);
    else if (i == ROW_CHECK)
        sh_start(JOB_CHECK, 0), sh.manual = 1;
    else if (is(i, "themes") && !rockbox_available())
        toast("Install Rockbox first");
    else if (is(i, "rockbox") && sh_state(i) == ST_INSTALL && !armed)
        sh.armed = r + 1;
    else
        sh_start(JOB_INSTALL, i);
    sh_refresh();
    return 0;
}

static int sh_closed(void *ctx, void *event) {
    sh.page = 0, sh.rows = sh.armed = 0;
    return 0;
}

/* The rows: Check for Updates, then the list's apps in its order, Rockbox Themes only with Rockbox
 * on the card and a dropped app only while installed; built again when a job ends changes which. */
static void sh_rows(void) {
    int map[SH_MAX + 1] = { ROW_CHECK }, n = 1;
    for (int i = 0; i < shelf_n; ++i)
        if ((!is(i, "themes") || rockbox_available()) && (!apps[i].dropped || sh_tagged(i) >= 0))
            map[n++] = i;
    if (sh.page && (n != sh.rows || memcmp(map, sh.map, sizeof map))) {
        memcpy(sh.map, map, sizeof map);
        sh.rows = 0;
        void *view = page_list(sh.page, sh.page, 0, "App Store", n, 64);
        for (int r = 0; r < n; ++r)
            /* sh_text writes the detail */
            sh.row[r] = page_row_detail(
                view, r, map[r] == ROW_CHECK ? "Check for Updates" : apps[map[r]].name, "", sh_click);
        sh.rows = n;
    }
    sh_refresh();
}

/* Streaming → Configuration's Erase Spotify Data (navigation.c): with start, Spotify's login, cache
 * and logs erased on this thread, unless a job is running (a check stops, as an App Store row's
 * press stops it, so the next press acts); whether an erase is running. */
int shelf_erase(int start) {
    if (start && sh.job == JOB_CHECK)
        sh.cancel = 1, toast("Stopped checking for updates");
    else if (start && sh.job)
        toast("Wait for the one in progress");
    else if (start)
        sh_start(JOB_ERASE, -1);
    return sh.job == JOB_ERASE;
}

/* The opening check, held for Smart Wi-Fi's link (smart_hold). */
static int sh_check(void *ctx, void *event) {
    if (!sh.job && get_wifisignal() > 0) sh_start(JOB_CHECK, 0), sh_refresh();
    return 0;
}

/* System settings' Apps row. The card's last list, until a check fetches the list again: with Wi-Fi
 * when the last whole check is over SH_STALE_MS old (or none), so opening the page often fetches it
 * once; while Smart Wi-Fi brings the radio back, once its link is up. */
int shelf_open(void *ctx, void *event) {
    if (sh.page || !(sh.page = page_open("apps_page", sh_closed, page_keyup))) return 0;
    if (sh.job != JOB_CHECK && !shelf_n) { /* an erase can run from Configuration before it opens */
        unsigned n = 0;
        char *list = read_all(SH_LIST, 1u << 16, &n);
        shelf_n = shelf_sources(list, apps, SH_MAX);
        free(list);
    }
    sh_rows();
    if (!sh.job && (!sh.checked || now_ms() - sh.checked > SH_STALE_MS)) {
        if (get_wifisignal() > 0)
            sh_start(JOB_CHECK, 0), sh_refresh();
        else
            /* once Smart Wi-Fi's link is up; a row pressed while it waits takes its place and acts on
             * the list shown (an install fetches the list first), and the next open checks */
            smart_hold(sh_check, 0, "apps_page");
    }
    return 0;
}

/* Smart Wi-Fi (smart_wifi.c smart_needed): Network Update's download or an Apps job keeps the radio on. */
int netupdate_busy(void) { return nu.state == NU_RUNNING || sh.job; }
#endif
