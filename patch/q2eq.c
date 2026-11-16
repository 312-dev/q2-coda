/* q2eq, --dev builds only (docs/adb.md#q2eq): the EQ editor from a computer, over adb shell. It
 * changes nothing itself: each command is a request (q2eq.h) that demo carries out through the
 * editor's own actions, and the state it answers with is printed as JSON. Reading a saved preset
 * or another output's remembered EQ is the one thing done here, read-only. */
#ifdef Q2EQ_HOST
#include <dirent.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#define O_REQUEST (O_WRONLY | O_CREAT | O_EXCL | O_TRUNC)
#else
#define O_REQUEST 0x701 /* MIPS O_WRONLY | O_CREAT | O_EXCL | O_TRUNC */
typedef __builtin_va_list va_list;
#define va_start __builtin_va_start
#define va_end __builtin_va_end
struct dirent {
    unsigned ino, off;
    unsigned short reclen;
    unsigned char type;
    char d_name[256];
};
int open(const char *, int, ...), read(int, void *, unsigned), write(int, const void *, unsigned);
int close(int), unlink(const char *), rename(const char *, const char *), usleep(unsigned), getpid(void);
int snprintf(char *, unsigned, const char *, ...), vsnprintf(char *, unsigned, const char *, va_list);
int strcmp(const char *, const char *), strncmp(const char *, const char *, unsigned);
unsigned strlen(const char *);
char *strrchr(const char *, int), *strstr(const char *, const char *);
void *memset(void *, int, unsigned), *memcpy(void *, const void *, unsigned);
int memcmp(const void *, const void *, unsigned);
long time(long *);
void *opendir(const char *);
struct dirent *readdir(void *);
int closedir(void *);
#endif
#include "q2eq.h"

#ifndef Q2EQ_WAIT_MS
#define Q2EQ_WAIT_MS 10000 /* demo looks every 500 ms, between UI passes */
#endif
#define FOLDER Q2EQ_ROOT "/mnt/mmc/EQ"            /* peq.h's PEQ_FOLDER */
#define OUTPUTS Q2EQ_ROOT "/mnt/data/peq-outputs" /* and EQ_OUTPUTS */
#define LIMIT 16384                               /* PEQ_FILE_LIMIT: an import's largest */

static char buffer[8192];
static unsigned used;
static void flush(int fd) {
    for (unsigned at = 0; at < used;) {
        int n = write(fd, buffer + at, used - at);
        if (n <= 0) break;
        at += n;
    }
    used = 0;
}
static void out(const char *format, ...) {
    va_list ap;
    if (used > sizeof buffer - 1024) flush(1);
    va_start(ap, format);
    int n = vsnprintf(buffer + used, sizeof buffer - used, format, ap);
    va_end(ap);
    used += n < 0 ? 0 : n >= (int)(sizeof buffer - used) ? sizeof buffer - used - 1 : (unsigned)n;
}
/* s as a JSON string, its bytes as they are (UTF-8 passes through) */
static void text(const char *s) {
    out("\"");
    for (; *s; ++s) {
        unsigned char c = *s;
        if (c == '"' || c == '\\') out("\\%c", c);
        else if (c < 32) out("\\u%04x", c);
        else out("%c", c);
    }
    out("\"");
}

static const char *const types[PEQ_TYPES] = {"peaking",  "lowshelf", "highshelf", "lowpass",
                                              "highpass", "bandpass", "notch",     "allpass"};
static const char *const channels[4] = {"off", "both", "left", "right"};
static const char *const modes[3] = {"off", "simple", "advanced"};

static void preset(const peq_preset *p) {
    out("{\"preamp\": %.6g, \"preamp_auto\": %s, \"balance\": %.6g, \"bands\": [", p->preamp,
        p->automatic != 2 ? "true" : "false", p->balance);
    for (int i = 0; i < p->count && i < PEQ_BANDS; ++i) {
        const peq_band *b = &p->bands[i];
        out("%s{\"band\": %d, \"on\": %s, \"channel\": \"%s\", \"type\": \"%s\", \"freq\": %.6g, "
            "\"gain\": %.6g, \"q\": %.6g}",
            i ? ", " : "", i + 1, b->enabled ? "true" : "false", channels[b->enabled & 3],
            types[(unsigned)b->type < PEQ_TYPES ? b->type : 0], b->frequency, b->gain, b->q);
    }
    out("]}");
}

/* The saved state: which EQ is selected, Advanced's preset and Simple's three gains. */
static void state(const eq_state *s) {
    out("\"mode\": \"%s\", \"advanced\": ", modes[(unsigned)s->mode < 3 ? s->mode : 0]);
    preset(&s->peq);
    out(", \"simple\": {\"low\": %.6g, \"mid\": %.6g, \"high\": %.6g, \"preamp\": %.6g}",
        s->eq3.bands[0].gain, s->eq3.bands[1].gain, s->eq3.bands[2].gain, s->eq3.preamp);
}

static void reply(const q2eq_reply *r) {
    out("{\"ok\": %s, \"message\": ", r->ok ? "true" : "false");
    text(r->message);
    out(", \"page_open\": %s, \"per_output\": %s, \"output\": ", r->page ? "true" : "false",
        r->per_output ? "true" : "false");
    if (r->per_output && r->owner[0]) text(r->owner);
    else out("null");
    out(", \"playing\": %s, ", r->flag && r->state.mode != EQ_OFF ? "true" : "false");
    state(&r->state);
    out(", \"applied_name\": ");
    text(r->applied);
    out(", \"draft\": {\"name\": ");
    text(r->name);
    out(", \"dirty\": %s, \"enabled\": %s, \"auto_preamp\": %.6g, \"preset\": ", r->dirty ? "true" : "false",
        r->draft.bypass ? "false" : "true", r->automatic);
    preset(&r->draft);
    out("}"); /* the reply's own brace is the caller's, after any list */
}

static int say(int fd, const char *s, unsigned n) {
    for (unsigned at = 0; at < n;) {
        int k = write(fd, s + at, n - at);
        if (k <= 0) return 0;
        at += k;
    }
    return 1;
}

static int fail(const char *why) {
    out("{\"ok\": false, \"message\": ");
    text(why);
    out("}\n");
    flush(1);
    return 2;
}

static int load(const char *path, char *data, unsigned size) {
    int fd = open(path, 0), n = 0, got;
    if (fd < 0) return -1;
    while (n < (int)size && (got = read(fd, data + n, size - n)) > 0) n += got;
    close(fd);
    return n;
}

static int save(const char *path, const void *data, unsigned size) {
    char tmp[300];
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    unlink(tmp);
    int fd = open(tmp, O_REQUEST, 0600);
    if (fd < 0) return 0;
    int ok = write(fd, data, size) == (int)size;
    if (close(fd) || !ok || rename(tmp, path)) return unlink(tmp), 0;
    return 1;
}

/* The request to demo, and its answer: 0 if none came. */
static int ask(q2eq_request *rq, q2eq_reply *r) {
    struct {
        char magic[8];
        q2eq_reply r;
    } file;
    rq->nonce = (unsigned)getpid() * 2654435761u ^ (unsigned)time(0);
    unlink(Q2EQ_REPLY);
    if (!save(Q2EQ_REQUEST, rq, sizeof *rq)) return 0;
    for (int waited = 0; waited < Q2EQ_WAIT_MS; waited += 20) {
        if (load(Q2EQ_REPLY, (char *)&file, sizeof file) == sizeof file && !memcmp(file.magic, "Q2EQR01", 8) &&
            file.r.nonce == rq->nonce) {
            *r = file.r;
            r->message[sizeof r->message - 1] = r->name[255] = r->applied[255] = 0;
            r->owner[sizeof r->owner - 1] = 0;
            return 1;
        }
        usleep(20000);
    }
    unlink(Q2EQ_REQUEST); /* so a demo that comes back later does not act on it */
    return 0;
}

static int number(const char *s, int *v) {
    int n = 0;
    if (!*s) return 0;
    for (; *s; ++s) {
        if (*s < '0' || *s > '9' || n > 1000) return 0;
        n = n * 10 + *s - '0';
    }
    return *v = n, 1;
}

static int pick(const char *s, const char *const *names, int n) {
    for (int i = 0; i < n; ++i)
        if (!strcmp(s, names[i])) return i;
    return -1;
}

static const char usage[] =
    "usage: q2eq [--now] [--replace] [--output KEY] COMMAND\n"
    "Changes go to the EQ page's draft, as the editor's do; apply (or --now) plays them.\n"
    "  current                       the state, as JSON (every command prints it)\n"
    "  list                          presets on the card (EQ/*.txt)\n"
    "  outputs                       outputs with a remembered EQ (EQ per Output)\n"
    "  on | off                      Advanced's Enabled; off turns Simple's off too\n"
    "  per-output on|off             EQ per Output\n"
    "  apply | discard | reset       apply the draft; drop it; Reset to defaults\n"
    "  preamp auto|DB                -24 to 12 in 0.5 steps\n"
    "  balance DB                    -12 (left louder) to 12, 0.5 steps\n"
    "  band N on|off                 N 1-30: the ten, then one past the last used\n"
    "  band N channel both|left|right\n"
    "  band N type peaking|lowshelf|highshelf|lowpass|highpass|bandpass|notch|allpass\n"
    "  band N freq HZ | q Q | gain DB\n"
    "  load NAME | stock NAME        a card preset; Pop Rock Dance Blues Metal Vocal Classical Jazz\n"
    "  save 1-10 | save-as NAME      Save editor preset (Manual NN), or by name\n"
    "  rename OLD NEW | delete NAME\n"
    "  import FILE|- [--name NAME]   Equalizer APO text saved to the card and loaded\n"
    "  export [NAME]                 the draft, or a saved preset, as Equalizer APO text\n"
    "  simple on|off | simple reset | simple low|mid|high DB | simple preset NAME\n";

int main(int argc, char **argv) {
    static q2eq_request rq;
    static q2eq_reply r;
    static char data[LIMIT + 1];
    const char *a[8] = {0}, *name = 0;
    char other[sizeof rq.output] = {0};
    int n = 0, v;
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--now")) rq.flags |= Q2EQ_NOW;
        else if (!strcmp(argv[i], "--replace")) rq.flags |= Q2EQ_REPLACE;
        else if (!strcmp(argv[i], "--json")) continue; /* JSON is all it prints */
        else if (!strcmp(argv[i], "--output") && i + 1 < argc) snprintf(rq.output, sizeof rq.output, "%s", argv[++i]);
        else if (!strcmp(argv[i], "--name") && i + 1 < argc) name = argv[++i];
        else if (!strcmp(argv[i], "--help") || !strcmp(argv[i], "-h")) return !say(1, usage, sizeof usage - 1);
        else if (n < 8) a[n++] = argv[i];
    }
    const char *c = n ? a[0] : "", *x = n > 1 ? a[1] : "", *y = n > 2 ? a[2] : "", *z = n > 3 ? a[3] : "";
    int ok = 1;
#define IS(s) !strcmp(c, s)
    rq.op = Q2EQ_STATE;
    if (IS("current")) {
        memcpy(other, rq.output, sizeof other); /* asked for as the current, read if it is not */
        rq.output[0] = 0;
    } else if (IS("list")) rq.op = Q2EQ_LIST;
    else if (IS("outputs")) rq.op = Q2EQ_STATE;
    else if (IS("on") || IS("off")) rq.op = Q2EQ_ON, rq.arg = IS("on");
    else if (IS("per-output") && (!strcmp(x, "on") || !strcmp(x, "off")))
        rq.op = Q2EQ_PER_OUTPUT, rq.arg = !strcmp(x, "on");
    else if (IS("apply")) rq.op = Q2EQ_APPLY;
    else if (IS("discard")) rq.op = Q2EQ_DISCARD;
    else if (IS("reset")) rq.op = Q2EQ_RESET;
    else if ((IS("preamp") || IS("balance")) && *x)
        rq.op = IS("preamp") ? Q2EQ_PREAMP : Q2EQ_BALANCE, snprintf(rq.text, sizeof rq.text, "%s", x);
    else if (IS("band") && number(x, &v) && v >= 1 && *y) {
        rq.band = v - 1;
        snprintf(rq.text, sizeof rq.text, "%s", z);
        if (!strcmp(y, "on") || !strcmp(y, "off")) rq.op = Q2EQ_BAND, rq.arg = !strcmp(y, "on");
        else if (!strcmp(y, "channel") && (rq.arg = pick(z, channels, 4)) > 0) rq.op = Q2EQ_CHANNEL;
        else if (!strcmp(y, "type") && (rq.arg = pick(z, types, PEQ_TYPES)) >= 0) rq.op = Q2EQ_TYPE;
        else if (!strcmp(y, "freq") && *z) rq.op = Q2EQ_FREQ;
        else if (!strcmp(y, "q") && *z) rq.op = Q2EQ_Q;
        else if (!strcmp(y, "gain") && *z) rq.op = Q2EQ_GAIN;
        else ok = 0;
    } else if ((IS("load") || IS("stock") || IS("delete") || IS("save-as")) && *x)
        rq.op = IS("load") ? Q2EQ_LOAD : IS("stock") ? Q2EQ_STOCK : IS("delete") ? Q2EQ_DELETE : Q2EQ_SAVE_AS,
        snprintf(rq.name, sizeof rq.name, "%s", x);
    else if (IS("save") && number(x, &v) && v >= 1 && v <= 10) rq.op = Q2EQ_SAVE, rq.arg = v - 1;
    else if (IS("rename") && *x && *y)
        rq.op = Q2EQ_RENAME, snprintf(rq.name, sizeof rq.name, "%s", x), snprintf(rq.text, sizeof rq.text, "%s", y);
    else if (IS("import") && *x) {
        int got = load(strcmp(x, "-") ? x : "/dev/stdin", data, sizeof data);
        if (got < 0) return fail("Cannot read that file");
        if (got > LIMIT) return fail("Larger than 16 KB");
        if (!name) { /* the file's name, less its extension */
            const char *base = strrchr(x, '/') ? strrchr(x, '/') + 1 : x, *dot = strrchr(base, '.');
            if (!strcmp(x, "-")) return fail("import - needs --name NAME");
            snprintf(rq.name, sizeof rq.name, "%.*s", (int)(dot && dot != base ? dot - base : (long)strlen(base)), base);
        } else snprintf(rq.name, sizeof rq.name, "%s", name);
        if (!save(Q2EQ_IMPORT, data, got)) return fail("Cannot write " Q2EQ_IMPORT);
        rq.op = Q2EQ_IMPORT_AS;
    } else if (IS("export")) {
        if (*x) { /* a saved preset as it is on the card */
            char path[300];
            snprintf(path, sizeof path, FOLDER "/%s.txt", x);
            int got = strstr(x, "/") ? -1 : load(path, data, LIMIT);
            if (got < 0) return fail("No such preset");
            return say(1, data, got) ? 0 : 2;
        }
        rq.op = Q2EQ_EXPORT;
    } else if (IS("simple")) {
        if (!strcmp(x, "on") || !strcmp(x, "off")) rq.op = Q2EQ_SIMPLE, rq.arg = !strcmp(x, "on");
        else if (!strcmp(x, "reset")) rq.op = Q2EQ_SIMPLE_RESET;
        else if (!strcmp(x, "preset") && *y) rq.op = Q2EQ_SIMPLE_PRESET, snprintf(rq.name, sizeof rq.name, "%s", y);
        else if ((rq.band = pick(x, (const char *const[]){"low", "mid", "high"}, 3)) >= 0 && *y)
            rq.op = Q2EQ_SIMPLE_GAIN, snprintf(rq.text, sizeof rq.text, "%s", y);
        else ok = 0;
    } else ok = 0;
    if (!ok) {
        say(2, usage, sizeof usage - 1);
        return 2;
    }
    if (!ask(&rq, &r)) return fail("demo did not answer: is a --dev build's EQ running?");
    if (IS("export") && r.ok) {
        int got = load(Q2EQ_TEXT, data, LIMIT);
        return got > 0 && say(1, data, got) ? 0 : fail("Export failed");
    }
    if (other[0] && (!r.per_output || strcmp(other, r.owner))) { /* another output's remembered EQ */
        struct {
            char magic[8];
            eq_state s;
        } file;
        char path[300];
        snprintf(path, sizeof path, OUTPUTS "/%s", other);
        if (strstr(other, "/") || strstr(other, "..") || load(path, (char *)&file, sizeof file) != sizeof file ||
            memcmp(file.magic, "Q2PEQ03", 8))
            return fail("No EQ remembered for that output");
        out("{\"ok\": true, \"output\": ");
        text(other);
        out(", \"remembered\": true, ");
        state(&file.s);
        out("}\n");
        return flush(1), 0;
    }
    reply(&r);
    if (IS("list")) {
        int got = load(Q2EQ_TEXT, data, LIMIT);
        out(", \"presets\": [");
        for (int i = 0, start = 0, k = 0; i < got; ++i)
            if (data[i] == '\n') {
                data[i] = 0;
                out(k++ ? ", " : "");
                text(data + start);
                start = i + 1;
            }
        out("]");
    }
    if (IS("outputs")) {
        void *dir = opendir(OUTPUTS);
        struct dirent *e;
        out(", \"outputs\": [");
        for (int k = 0; dir && (e = readdir(dir));) {
            unsigned len = strlen(e->d_name);
            if (e->d_name[0] == '.' || !strcmp(e->d_name, "owner") || (len > 4 && !strcmp(e->d_name + len - 4, ".tmp")))
                continue;
            out(k++ ? ", " : "");
            text(e->d_name);
        }
        if (dir) closedir(dir);
        out("]");
    }
    out("}\n");
    flush(1);
    return r.ok ? 0 : 1;
}
