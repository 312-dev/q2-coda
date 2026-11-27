/* q2pkgd: Vamp OS's update service (docs/vamp-os.md#installer-and-updates, docs/pkgd.md), its first slice:
 *
 *     q2pkgd stage RELEASE_DIR [--rootfs DIR] [--card DIR]    a release checked, composed, staged beside the rescue copy
 *     q2pkgd install [--card DIR]                             the staged image put where Shanling's recovery reads it
 *     q2pkgd restore [--card DIR]                             at boot, first: the rescue copy put back in recovery-update/
 *     q2pkgd confirm [--card DIR]                             after a good boot: the same, and the install no longer pending
 *     q2pkgd app-check DIR                                    an app's folder and its q2app manifest (docs/platform/packages.md)
 *     q2pkgd app-add DIR | app-remove ID [--root DIR]         a package's user and groups made, or retired
 *     q2pkgd accounts [--root DIR]                            /run/vamp/passwd and group composed (rcS, at each boot)
 *     q2pkgd app-run DIR [--root DIR]                         the package's entry, run as its user
 *     q2pkgd serve [--root DIR] [--runsv PROG] [--self PROG]  the launcher: Q2PKG/1 on /run/vamp/pkg.sock
 *
 * The card's recovery-update/ (xImage and rootfs.squashfs, stock's) is the owner's only way back when a boot fails:
 * hold Previous and power on with Centre, and Shanling's recovery flashes it. So nothing here leaves the card without a
 * known-good copy there while idle:
 *
 *   stage    RELEASE_DIR holds vampos.squashfs, release.txt and release.sig. In order, stopping at the first failure
 *            and leaving nothing behind: release.sig is the release key's Ed25519 signature of
 *            "q2-coda-vampos-v1 TAG SHA512(release.txt)\n"; vampos.squashfs has the SHA-512 and size release.txt names;
 *            every vendor file release.txt names is found in --rootfs (its stock path, or under /vendor) with its
 *            SHA-256; the image is composed (copied, /vendor appended by mksquashfs with tools/vampos.py compose's
 *            flags); the result has a SHA-256 release.txt lists. It lands in .vamp/stage/recovery-update/ on the card,
 *            never in recovery-update/.
 *   install  keeps the rescue copy first: recovery-update/'s files copied whole to .vamp/rescue/ and checked byte for
 *            byte (or found there already, when an install is pending), then the staged rootfs.squashfs put into
 *            recovery-update/ beside the rescue's xImage, and .vamp/rescue/pending written. Any failure leaves
 *            recovery-update/ as it was.
 *   restore  with an install pending: .vamp/rescue/'s files put back in recovery-update/ (written beside and renamed),
 *            unless they are there already. Shanling's recovery removes recovery-update/ once it has flashed it (seen on a
 *            Q2: the folder gone after each install), so the card has no recovery at all until this runs: rcS runs it as
 *            soon as the card is mounted, before anything else that can fail.
 *   confirm  restore, then pending removed. Vamp OS runs it once its services have been up a minute (/etc/sv/q2pkgd-confirm).
 *
 * It writes no flash and marks nothing for the recovery: flashing is Shanling's recovery's, started by the owner's keys.
 *
 *   app-check  DIR is an app's folder (.<id>). Its q2app is checked as Vamp API 1's manifest: one with none of api=,
 *            version=, kind=, entry= and permissions= is an API 0 app (today's card apps, run as today); any other names
 *            api=1, a version of dotted numbers, a known kind, an entry that is a program in the folder (for every kind
 *            but theme and language), and only known permissions, screen for a game alone and background for every
 *            service. It says what the app is and may do, or the first thing wrong. It installs nothing.
 *
 * Package users (docs/platform/packages.md#permissions, perm/q2perm.h):
 *
 *   app-add     an API 1 app's folder, checked as app-check does, given a user "app-<id>" (a uid from 10000, the
 *               same one again for an id added before) in /mnt/data/vamp/apps, "<id> <uid> <permissions>" a line,
 *               and a data folder /mnt/data/vamp/app/<id> of its own (0700); then the accounts composed. An API 0 app
 *               gets no user: it runs as today.
 *   app-remove  its line kept with "-" for its permissions, so the uid and the data folder stay its own and are never
 *               given to another package; then the accounts composed.
 *   accounts    /etc/passwd.base and /etc/group.base (the image's, Buildroot's users) and the registry into
 *               /run/vamp/passwd and /run/vamp/group, where the image's /etc/passwd and /etc/group point: each package's
 *               user and its own group, and each permission's group listing the users that may use it.
 *   app-run     the entry of an added app as its user: the groups of its permissions as the registry has them (what
 *               was approved at app-add, not what the manifest says now), then its gid and uid; when any of those does
 *               not take, or uid 0 could be taken back, it runs nothing. HOME is its data folder. An API 0 app's
 *               run script instead, as root as today's Vamp runs it, and then its loop waited for (app_run0).
 *
 * The launcher (docs/platform/packages.md#starting-and-stopping-q2pkg1), the owner's ruling of 2026-11-27: q2pkgd owns
 * the packages, their users and their permissions, so it alone starts and stops them.
 *
 *   serve       Q2PKG/1 on /run/vamp/pkg.sock: start, stop and status of an app by its id, checked against the
 *               caller's SO_PEERCRED uid (root any app; a package its own status and stop). Each app started is a
 *               runit service of its own, /run/vamp/sv/<id>, its run "q2pkgd app-run DIR" and its runsv started here;
 *               a service kind is kept up, any other runs once. An API 0 app's start writes /tmp/q2-addon.caps first.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include <grp.h>
#include <sys/types.h>

#include "monocypher-ed25519.h"
#include "q2perm.h" /* perm/: the permissions' groups */

#ifndef RELEASE_KEY /* the release key's public half, from assets/signing/q2-coda-release.pub (os/package/q2pkgd) */
#error RELEASE_KEY
#endif
static const unsigned char release_key[32] = RELEASE_KEY;

static int fail(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "q2pkgd: ");
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
    return 1;
}

/* ---- SHA-256 (FIPS 180-4) ---- */

typedef struct { uint32_t h[8]; uint64_t len; unsigned char buf[64]; unsigned n; } sha256_t;
static const uint32_t K[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be,
    0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa,
    0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85,
    0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
    0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f,
    0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};
#define ROR(x, k) ((x) >> (k) | (x) << (32 - (k)))
static void sha256_block(sha256_t *s, const unsigned char *p) {
    uint32_t w[64], v[8];
    for (int i = 0; i < 16; ++i) w[i] = (uint32_t)p[4 * i] << 24 | (uint32_t)p[4 * i + 1] << 16 | (uint32_t)p[4 * i + 2] << 8 | p[4 * i + 3];
    for (int i = 16; i < 64; ++i)
        w[i] = w[i - 16] + (ROR(w[i - 15], 7) ^ ROR(w[i - 15], 18) ^ w[i - 15] >> 3) + w[i - 7] + (ROR(w[i - 2], 17) ^ ROR(w[i - 2], 19) ^ w[i - 2] >> 10);
    memcpy(v, s->h, sizeof v);
    for (int i = 0; i < 64; ++i) {
        uint32_t t1 = v[7] + (ROR(v[4], 6) ^ ROR(v[4], 11) ^ ROR(v[4], 25)) + ((v[4] & v[5]) ^ (~v[4] & v[6])) + K[i] + w[i];
        uint32_t t2 = (ROR(v[0], 2) ^ ROR(v[0], 13) ^ ROR(v[0], 22)) + ((v[0] & v[1]) ^ (v[0] & v[2]) ^ (v[1] & v[2]));
        memmove(v + 1, v, 7 * sizeof *v);
        v[4] += t1, v[0] = t1 + t2;
    }
    for (int i = 0; i < 8; ++i) s->h[i] += v[i];
}
static void sha256_init(sha256_t *s) {
    static const uint32_t h0[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    memcpy(s->h, h0, sizeof h0), s->len = 0, s->n = 0;
}
static void sha256_update(sha256_t *s, const unsigned char *p, size_t n) {
    s->len += n;
    while (n--) {
        s->buf[s->n++] = *p++;
        if (s->n == 64) sha256_block(s, s->buf), s->n = 0;
    }
}
static void sha256_final(sha256_t *s, unsigned char out[32]) {
    uint64_t bits = s->len * 8;
    unsigned char pad = 0x80, zero = 0;
    sha256_update(s, &pad, 1);
    while (s->n != 56) sha256_update(s, &zero, 1);
    for (int i = 7; i >= 0; --i) {
        unsigned char b = (unsigned char)(bits >> (8 * i));
        sha256_update(s, &b, 1);
    }
    for (int i = 0; i < 8; ++i) out[4 * i] = s->h[i] >> 24, out[4 * i + 1] = s->h[i] >> 16, out[4 * i + 2] = s->h[i] >> 8, out[4 * i + 3] = s->h[i];
}

static void hex(const unsigned char *in, size_t n, char *out) {
    for (size_t i = 0; i < n; ++i) sprintf(out + 2 * i, "%02x", in[i]);
}

/* a file's SHA-256 (sha512 0) or SHA-512 as hex, and its size; -1 when it cannot be read */
static int hash_file(const char *path, int sha512, char *out, long long *size) {
    FILE *f = fopen(path, "rb");
    if (!f) return -1;
    unsigned char buf[65536], d[64];
    sha256_t s;
    crypto_sha512_ctx c;
    sha512 ? crypto_sha512_init(&c) : sha256_init(&s);
    long long total = 0;
    size_t n;
    while ((n = fread(buf, 1, sizeof buf, f)) > 0) {
        sha512 ? crypto_sha512_update(&c, buf, n) : sha256_update(&s, buf, n);
        total += (long long)n;
    }
    int bad = ferror(f);
    fclose(f);
    if (bad) return -1;
    sha512 ? crypto_sha512_final(&c, d) : sha256_final(&s, d);
    hex(d, sha512 ? 64 : 32, out);
    if (size) *size = total;
    return 0;
}

/* ---- the release ---- */

#define FILES 128
static struct {
    char tag[32], image_sha512[129];
    long long image_bytes;
    unsigned epoch;
    int n;
    struct { char path[200], sha256[65]; unsigned mode; } file[FILES];
    int composed_n;
    struct { char stock[32], sha256[65]; } composed[8];
} rel;

/* release.txt: "Q2REL/1", then a field a line (docs/pkgd.md#the-release) */
static int read_release(const char *text) {
    char line[512];
    const char *p = text;
    int first = 1;
    while (*p) {
        size_t k = strcspn(p, "\n");
        if (k >= sizeof line) return fail("release.txt: a line too long");
        memcpy(line, p, k), line[k] = 0;
        p += k + (p[k] == '\n');
        if (first) {
            if (strcmp(line, "Q2REL/1")) return fail("release.txt is not Q2REL/1");
            first = 0;
            continue;
        }
        char a[200], b[65], cs[32];
        unsigned m;
        long long n;
        if (sscanf(line, "tag %31s", rel.tag) == 1) continue;
        if (sscanf(line, "image %128s %lld", rel.image_sha512, &rel.image_bytes) == 2) continue;
        if (sscanf(line, "epoch %u", &rel.epoch) == 1) continue;
        if (sscanf(line, "file %199s %64s %o", a, b, &m) == 3 && rel.n < FILES) {
            if (strstr(a, "..") || a[0] == '/') return fail("release.txt: a vendor path that is not plain: %s", a);
            snprintf(rel.file[rel.n].path, sizeof rel.file[0].path, "%s", a);
            snprintf(rel.file[rel.n].sha256, sizeof rel.file[0].sha256, "%s", b);
            rel.file[rel.n++].mode = m & 0777;
            continue;
        }
        if (sscanf(line, "composed %31s %64s", cs, b) == 2 && rel.composed_n < 8) {
            snprintf(rel.composed[rel.composed_n].stock, 32, "%s", cs), snprintf(rel.composed[rel.composed_n++].sha256, 65, "%s", b);
            continue;
        }
        (void)n;
        if (*line) return fail("release.txt: not understood: %s", line);
    }
    if (!*rel.tag || !*rel.image_sha512 || !rel.n || !rel.composed_n || !rel.epoch) return fail("release.txt is missing a field");
    return 0;
}

static char *slurp(const char *path, size_t *n) {
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    char *b = calloc(1, 1 << 20);
    *n = b ? fread(b, 1, (1 << 20) - 1, f) : 0;
    fclose(f);
    return b;
}

/* where a stock path lives under /vendor (tools/vampos.py VENDOR_PLACES) */
static int place(const char *stock, char *out, size_t room) {
    /* tools/vampos.py's VENDOR_PLACES, in the same order (test/pkgd.py stages both layouts) */
    static const char *const map[][2] = {{"module_driver/", "vendor/module_driver/"}, {"firmware/", "vendor/firmware/"},
                                         {"usr/data/", "vendor/mcu/"}, {"usr/bin/rtk_hciattach", "vendor/bin/rtk_hciattach"}};
    for (unsigned i = 0; i < sizeof map / sizeof *map; ++i)
        if (!strncmp(stock, map[i][0], strlen(map[i][0]))) return snprintf(out, room, "%s%s", map[i][1], stock + strlen(map[i][0])), 0;
    return -1;
}

static int run(char *const argv[]) {
    pid_t p = fork();
    if (p == 0) execvp(argv[0], argv), _exit(127);
    int st;
    if (p < 0 || waitpid(p, &st, 0) < 0) return -1;
    return WIFEXITED(st) ? WEXITSTATUS(st) : -1;
}

static int mkdirs(const char *dir) {
    char p[600];
    snprintf(p, sizeof p, "%s", dir);
    for (char *s = p + 1; *s; ++s)
        if (*s == '/') *s = 0, mkdir(p, 0755), *s = '/';
    return mkdir(p, 0755) && errno != EEXIST ? -1 : 0;
}

static int copy(const char *from, const char *to, unsigned mode) {
    int in = open(from, O_RDONLY | O_CLOEXEC), out = open(to, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, mode);
    char buf[65536];
    ssize_t n;
    int ok = in >= 0 && out >= 0;
    while (ok && (n = read(in, buf, sizeof buf)) > 0) ok = write(out, buf, (size_t)n) == n;
    if (in >= 0) close(in);
    if (out >= 0 && close(out)) ok = 0;
    if (ok) chmod(to, mode);
    return ok ? 0 : -1;
}

static void remove_tree(const char *dir) { /* the staging folder, made by this program alone */
    char *argv[] = {"rm", "-rf", (char *)dir, 0};
    run(argv);
}

static int stage(const char *dir, const char *rootfs, const char *out) { /* out: the card's .vamp/stage/recovery-update */
    char p[1000], q[800], h[129], txt_sha512[129];
    size_t n;
    /* 1. the signature */
    snprintf(p, sizeof p, "%s/release.txt", dir);
    char *text = slurp(p, &n);
    if (!text) return fail("no %s", p);
    if (hash_file(p, 1, txt_sha512, 0)) return fail("cannot read %s", p);
    if (read_release(text)) return 1;
    char message[256];
    int m = snprintf(message, sizeof message, "q2-coda-vampos-v1 %s %s\n", rel.tag, txt_sha512);
    unsigned char sig[64];
    snprintf(p, sizeof p, "%s/release.sig", dir);
    FILE *f = fopen(p, "rb");
    size_t got = f ? fread(sig, 1, sizeof sig, f) : 0;
    if (f) fclose(f);
    if (got != 64 || crypto_ed25519_check(sig, release_key, (const unsigned char *)message, (size_t)m))
        return fail("release.sig is not the release key's signature of release.txt");
    /* 2. the image */
    long long bytes;
    snprintf(p, sizeof p, "%s/vampos.squashfs", dir);
    if (hash_file(p, 1, h, &bytes) || strcmp(h, rel.image_sha512) || bytes != rel.image_bytes)
        return fail("vampos.squashfs is not the image release.txt names");
    /* 3. the vendor set, from this Q2's own files */
    char tmp[700];
    snprintf(tmp, sizeof tmp, "%s.q2pkgd", out);
    remove_tree(tmp);
    if (mkdirs(tmp)) return fail("cannot make %s", tmp);
    snprintf(q, sizeof q, "%s/stage", tmp);
    for (int i = 0; i < rel.n; ++i) {
        char where[300], dst[1200];
        if (place(rel.file[i].path, where, sizeof where))
            return remove_tree(tmp), fail("%s has no place under /vendor", rel.file[i].path);
        snprintf(p, sizeof p, "%s/%s", rootfs, rel.file[i].path);
        if (hash_file(p, 0, h, 0) || strcmp(h, rel.file[i].sha256)) { /* not at its stock path: Vamp OS keeps it in /vendor */
            snprintf(p, sizeof p, "%s/%s", rootfs, where);
            if (hash_file(p, 0, h, 0) || strcmp(h, rel.file[i].sha256))
                return remove_tree(tmp), fail("%s is not the file this release was tested with (an unsupported stock version, or damaged)",
                                              rel.file[i].path);
        }
        snprintf(dst, sizeof dst, "%s/%s", q, where);
        char *slash = strrchr(dst, '/');
        *slash = 0, mkdirs(dst), *slash = '/';
        if (copy(p, dst, rel.file[i].mode)) return remove_tree(tmp), fail("cannot copy %s", rel.file[i].path);
    }
    /* 4. compose */
    char img[800], epoch[16];
    snprintf(img, sizeof img, "%s/rootfs.squashfs", tmp);
    snprintf(p, sizeof p, "%s/vampos.squashfs", dir);
    if (copy(p, img, 0644)) return remove_tree(tmp), fail("cannot copy the image");
    snprintf(epoch, sizeof epoch, "%u", rel.epoch);
    char *mk[] = {"mksquashfs", q, img, "-no-progress", "-no-recovery", "-processors", "1", "-all-root", "-all-time", epoch, "-mkfs-time", epoch, 0};
    if (run(mk)) return remove_tree(tmp), fail("mksquashfs failed");
    /* 5. the result against the tested one */
    if (hash_file(img, 0, h, 0)) return remove_tree(tmp), fail("cannot read the composed image");
    int found = 0;
    for (int i = 0; i < rel.composed_n && !found; ++i) found = !strcmp(h, rel.composed[i].sha256);
    if (!found) return remove_tree(tmp), fail("the composed image (%s) is not one this release was tested as", h);
    /* 6. staged */
    if (mkdirs(out)) return remove_tree(tmp), fail("cannot make %s", out);
    snprintf(p, sizeof p, "%s/rootfs.squashfs", out);
    if (rename(img, p)) return remove_tree(tmp), fail("cannot write %s", p);
    remove_tree(tmp);
    printf("staged %s %s as %s (recovery-update/ untouched: q2pkgd install puts it there)\n", rel.tag, h, p);
    return 0;
}

/* ---- install and confirm ---- */

static int same_file(const char *a, const char *b) {
    char ha[65], hb[65];
    long long na, nb;
    return !hash_file(a, 0, ha, &na) && !hash_file(b, 0, hb, &nb) && na == nb && !strcmp(ha, hb);
}

/* from copied whole to to: beside it, checked byte for byte, then renamed over it */
static int put(const char *from, const char *to) {
    char tmp[900];
    snprintf(tmp, sizeof tmp, "%s.q2pkgd", to);
    if (copy(from, tmp, 0644) || !same_file(from, tmp) || rename(tmp, to)) return unlink(tmp), -1;
    return 0;
}

static const char *const RECOVERY_FILES[] = {"xImage", "rootfs.squashfs"};

static int install(const char *card) {
    char rec[600], rescue[600], staged[700], pending[700], a[800], b[800];
    snprintf(rec, sizeof rec, "%s/recovery-update", card);
    snprintf(rescue, sizeof rescue, "%s/.vamp/rescue", card);
    snprintf(staged, sizeof staged, "%s/.vamp/stage/recovery-update/rootfs.squashfs", card);
    snprintf(pending, sizeof pending, "%s/pending", rescue);
    if (access(staged, R_OK)) return fail("nothing staged (%s)", staged);
    int is_pending = !access(pending, F_OK);
    if (mkdirs(rescue)) return fail("cannot make %s", rescue);
    for (unsigned i = 0; i < 2; ++i) {
        snprintf(a, sizeof a, "%s/%s", rec, RECOVERY_FILES[i]);
        snprintf(b, sizeof b, "%s/%s", rescue, RECOVERY_FILES[i]);
        if (is_pending) { /* recovery-update holds an earlier install's image: the rescue copy is the one kept */
            if (access(b, R_OK)) return fail("an install is pending and its rescue copy %s is gone", b);
            continue;
        }
        if (access(a, R_OK)) return fail("no %s: the card has no recovery to keep (put stock's recovery-update/ there first)", a);
        if (put(a, b)) return fail("cannot keep the rescue copy of %s", a);
    }
    /* the rescue copy is safe: now recovery-update/ takes the staged image beside the rescue's own xImage */
    if (!is_pending) {
        FILE *f = fopen(pending, "w");
        if (!f || fclose(f)) return fail("cannot write %s", pending);
    }
    snprintf(a, sizeof a, "%s/rootfs.squashfs", rec);
    if (put(staged, a)) {
        snprintf(b, sizeof b, "%s/rootfs.squashfs", rescue), put(b, a); /* as it was */
        return fail("cannot write %s", a);
    }
    printf("installed: %s holds the staged image; hold Previous and power on with Centre to flash it. Vamp OS puts the "
           "rescue copy back as soon as it has the card (q2pkgd restore, from rcS)\n", rec);
    return 0;
}

static int restore(const char *card, int and_confirm) {
    char rec[600], rescue[600], pending[700], a[800], b[800];
    snprintf(rec, sizeof rec, "%s/recovery-update", card);
    snprintf(rescue, sizeof rescue, "%s/.vamp/rescue", card);
    snprintf(pending, sizeof pending, "%s/pending", rescue);
    if (access(pending, F_OK)) return 0; /* nothing installed since the last confirm */
    if (mkdirs(rec)) return fail("cannot make %s", rec);
    for (unsigned i = 0; i < 2; ++i) {
        snprintf(a, sizeof a, "%s/%s", rec, RECOVERY_FILES[i]);
        snprintf(b, sizeof b, "%s/%s", rescue, RECOVERY_FILES[i]);
        if (!same_file(a, b) && put(b, a)) return fail("cannot put the rescue copy back into %s", a);
    }
    if (and_confirm) unlink(pending);
    printf("%s: %s holds the rescue copy again\n", and_confirm ? "confirmed" : "restored", rec);
    return 0;
}

/* ---- app manifests (docs/platform/packages.md) ---- */

#define API_SERVED 1
static const char *const KINDS[] = {"browse", "toolkit", "game", "service", "theme", "language", "provider", 0};

static int listed(const char *const *list, const char *w, size_t n) {
    for (int i = 0; list[i]; ++i)
        if (strlen(list[i]) == n && !strncmp(list[i], w, n)) return 1;
    return 0;
}

static int known_permission(const char *w, size_t n) {
    for (int i = 0; Q2PERM_GROUPS[i].name; ++i)
        if (strlen(Q2PERM_GROUPS[i].name) == n && !strncmp(Q2PERM_GROUPS[i].name, w, n)) return 1;
    return 0;
}

struct manifest { char name[200], api[16], version[64], kind[32], entry[600], permissions[400]; long api_n; int new_keys; };

/* dir's manifest into m, checked; 0 good (an API 0 app has m->new_keys 0) */
static int manifest(const char *dir, struct manifest *mp) {
    char path[700], *text;
    const char *base = strrchr(dir, '/') ? strrchr(dir, '/') + 1 : dir;
    struct manifest m = {0};
    if (base[0] != '.' || !base[1]) return fail("%s: an app's folder is .<id>", dir);
    for (const char *c = base + 1; *c; ++c)
        if (!(*c >= 'a' && *c <= 'z') && !(*c >= '0' && *c <= '9') && *c != '-') return fail("%s: an app id is a-z, 0-9 and -", dir);
    snprintf(path, sizeof path, "%s/q2app", dir);
    size_t n;
    if (!(text = slurp(path, &n))) return fail("%s: no q2app", dir);
    for (char *l = strtok(text, "\n"); l; l = strtok(0, "\n")) {
        l[strcspn(l, "\r")] = 0;
        char *eq = strchr(l, '=');
        if (!eq || *l == '#') continue;
        *eq = 0;
        const char *k = l, *v = eq + 1;
        struct { const char *key; char *to; size_t room; int new_key; } keys[] = {
            {"name", m.name, sizeof m.name, 0}, {"api", m.api, sizeof m.api, 1}, {"version", m.version, sizeof m.version, 1},
            {"kind", m.kind, sizeof m.kind, 1}, {"entry", m.entry, sizeof m.entry, 1},
            {"permissions", m.permissions, sizeof m.permissions, 1}};
        for (unsigned i = 0; i < sizeof keys / sizeof *keys; ++i)
            if (!strcmp(k, keys[i].key)) snprintf(keys[i].to, keys[i].room, "%s", v), m.new_keys |= keys[i].new_key;
    }
    free(text);
    if (!*m.name) return fail("q2app: no name=");
    if (!m.new_keys) return *mp = m, 0;
    char *end;
    long api = strtol(m.api, &end, 10);
    if (!*m.api || *end || api < 1) return fail("q2app: api= is the lowest Vamp API it needs, a number from 1");
    if (api > API_SERVED) return fail("q2app: needs API %ld; this Vamp OS serves API %d", api, API_SERVED);
    if (!*m.version || strspn(m.version, "0123456789.") != strlen(m.version) || m.version[0] == '.' ||
        m.version[strlen(m.version) - 1] == '.' || strstr(m.version, ".."))
        return fail("q2app: version= is dotted numbers (1.4.2)");
    if (!listed(KINDS, m.kind, strlen(m.kind))) return fail("q2app: kind=%s is not a kind", m.kind);
    int data_only = !strcmp(m.kind, "theme") || !strcmp(m.kind, "language");
    if (data_only && *m.entry) return fail("q2app: a %s is data and has no entry=", m.kind);
    if (!data_only) {
        size_t prog = strcspn(m.entry, " ");
        char exe[800];
        struct stat st;
        if (!prog) return fail("q2app: a %s needs entry= (its program in the folder, then its arguments)", m.kind);
        if (m.entry[0] == '/' || strstr(m.entry, "..") || strpbrk(m.entry, "|&;<>$`\\\"'*?(){}"))
            return fail("q2app: entry= is a program in the app's folder and plain arguments, run with no shell");
        snprintf(exe, sizeof exe, "%s/%.*s", dir, (int)prog, m.entry);
        if (stat(exe, &st) || !S_ISREG(st.st_mode)) return fail("q2app: entry= names %.*s, which is not in the folder", (int)prog, m.entry);
    }
    int screen = 0, background = 0;
    for (const char *w = m.permissions; *w;) {
        size_t k = strcspn(w, ",");
        if (!k || !known_permission(w, k)) return fail("q2app: %.*s is not a permission", (int)(k ? k : 1), k ? w : ",");
        screen |= k == 6 && !strncmp(w, "screen", 6), background |= k == 10 && !strncmp(w, "background", 10);
        w += k + (w[k] == ',');
    }
    if (screen && strcmp(m.kind, "game")) return fail("q2app: screen is a game's permission alone");
    if (!strcmp(m.kind, "service") && !background) return fail("q2app: a service runs in the background: it needs permissions=background");
    m.api_n = api;
    *mp = m;
    return 0;
}

static const char *app_id(const char *dir) {
    const char *base = strrchr(dir, '/') ? strrchr(dir, '/') + 1 : dir;
    return base + 1;
}

static int app_check(const char *dir) {
    struct manifest m;
    if (manifest(dir, &m)) return 1;
    if (!m.new_keys) return printf("app %s: %s, API 0 (a card app as today)\n", app_id(dir), m.name), 0;
    printf("app %s: %s %s, a %s for API %ld, may use: %s\n", app_id(dir), m.name, m.version, m.kind, m.api_n,
           *m.permissions ? m.permissions : "nothing");
    return 0;
}

/* ---- package users (docs/platform/packages.md#permissions) ---- */

#define APPS_MAX 256
#define ID_MAX 27 /* "app-" and the id: 31 bytes, under the 32 a user name may be */
static char proot[256];
static struct app { char id[64]; long uid; char perms[400]; } apps[APPS_MAX];
static int n_apps;

static char *rp(const char *rel) { /* rel under --root */
    static char b[4][700];
    static int i;
    i = (i + 1) & 3;
    snprintf(b[i], sizeof b[i], "%s%s", proot, rel);
    return b[i];
}

/* /mnt/data/vamp/apps: "<id> <uid> <permissions>" a line; "." no permissions, "-" removed */
static void apps_read(void) {
    FILE *f = fopen(rp("/mnt/data/vamp/apps"), "r");
    char l[600];
    n_apps = 0;
    while (f && n_apps < APPS_MAX && fgets(l, sizeof l, f)) {
        struct app *a = &apps[n_apps];
        if (sscanf(l, "%63s %ld %399s", a->id, &a->uid, a->perms) == 3 && a->uid >= Q2PERM_UID_MIN) ++n_apps;
    }
    if (f) fclose(f);
}

static int write_whole(const char *dst, const char *text, mode_t mode) {
    char tmp[720];
    snprintf(tmp, sizeof tmp, "%s.q2pkgd", dst);
    FILE *f = fopen(tmp, "w");
    if (!f) return fail("%s: %s", tmp, strerror(errno));
    int bad = fputs(text, f) < 0;
    bad |= fclose(f) != 0;
    if (bad || chmod(tmp, mode) || rename(tmp, dst)) return unlink(tmp), fail("%s: %s", dst, strerror(errno));
    return 0;
}

static int apps_write(void) {
    char *text = calloc(APPS_MAX, 600), *o = text;
    if (!text) return fail("out of memory");
    for (int i = 0; i < n_apps; ++i) o += sprintf(o, "%s %ld %s\n", apps[i].id, apps[i].uid, apps[i].perms);
    mkdir(rp("/mnt/data/vamp"), 0755);
    int r = write_whole(rp("/mnt/data/vamp/apps"), text, 0644);
    free(text);
    return r;
}

static int has_perm(const struct app *a, const char *perm) {
    size_t k = strlen(perm);
    for (const char *w = a->perms; *w; w += strcspn(w, ",") + (w[strcspn(w, ",")] == ','))
        if (strcspn(w, ",") == k && !strncmp(w, perm, k)) return 1;
    return 0;
}

static int accounts(void) {
    size_t pn = 0, gn = 0;
    char *pw = slurp(rp("/etc/passwd.base"), &pn), *gr = slurp(rp("/etc/group.base"), &gn);
    if (!pw || !gr) return free(pw), free(gr), fail("no %s or %s", rp("/etc/passwd.base"), rp("/etc/group.base"));
    apps_read();
    size_t room = pn + gn + (size_t)n_apps * 200 + 64 * 1024;
    char *p = malloc(room), *g = malloc(room), *po = p, *go = g;
    if (!p || !g) return free(pw), free(gr), free(p), free(g), fail("out of memory");
    po += sprintf(po, "%.*s%s", (int)pn, pw, pn && pw[pn - 1] != '\n' ? "\n" : "");
    go += sprintf(go, "%.*s%s", (int)gn, gr, gn && gr[gn - 1] != '\n' ? "\n" : "");
    for (int i = 0; i < n_apps; ++i) {
        if (!strcmp(apps[i].perms, "-")) continue;
        po += sprintf(po, "app-%s:x:%ld:%ld:%s:/mnt/data/vamp/app/%s:/bin/false\n", apps[i].id, apps[i].uid, apps[i].uid, apps[i].id, apps[i].id);
        go += sprintf(go, "app-%s:x:%ld:\n", apps[i].id, apps[i].uid);
    }
    for (int k = 0; Q2PERM_GROUPS[k].name; ++k) {
        int first = 1;
        go += sprintf(go, "%s:x:%d:", Q2PERM_GROUPS[k].name, Q2PERM_GROUPS[k].gid);
        for (int i = 0; i < n_apps; ++i)
            if (strcmp(apps[i].perms, "-") && has_perm(&apps[i], Q2PERM_GROUPS[k].name))
                go += sprintf(go, "%sapp-%s", first ? "" : ",", apps[i].id), first = 0;
        *go++ = '\n', *go = 0;
    }
    mkdir(rp("/run"), 0755), mkdir(rp("/run/vamp"), 0755);
    int r = write_whole(rp("/run/vamp/passwd"), p, 0644) || write_whole(rp("/run/vamp/group"), g, 0644);
    free(pw), free(gr), free(p), free(g);
    return r;
}

static struct app *app_find(const char *id) {
    for (int i = 0; i < n_apps; ++i)
        if (!strcmp(apps[i].id, id)) return &apps[i];
    return 0;
}

static int app_add(const char *dir) {
    struct manifest m;
    const char *id = app_id(dir);
    if (manifest(dir, &m)) return 1;
    if (!m.new_keys) return printf("app %s: API 0, run as today with no user of its own\n", id), 0;
    if (strlen(id) > ID_MAX) return fail("%s: an app id of more than %d bytes has no user name", id, ID_MAX);
    apps_read();
    struct app *a = app_find(id);
    if (!a) {
        long uid = Q2PERM_UID_MIN - 1;
        for (int i = 0; i < n_apps; ++i)
            if (apps[i].uid > uid) uid = apps[i].uid;
        if (n_apps == APPS_MAX) return fail("%d packages: no room for another", APPS_MAX);
        a = &apps[n_apps++];
        snprintf(a->id, sizeof a->id, "%s", id), a->uid = uid + 1;
    }
    snprintf(a->perms, sizeof a->perms, "%s", *m.permissions ? m.permissions : ".");
    char data[700];
    snprintf(data, sizeof data, "%s", rp("/mnt/data/vamp/app"));
    mkdir(rp("/mnt/data"), 0755), mkdir(rp("/mnt/data/vamp"), 0755), mkdir(data, 0755);
    snprintf(data, sizeof data, "%s/%s", rp("/mnt/data/vamp/app"), id);
    if (mkdir(data, 0700) && errno != EEXIST) return fail("%s: %s", data, strerror(errno));
    if (chown(data, (uid_t)a->uid, (gid_t)a->uid) && geteuid() == 0) return fail("%s: %s", data, strerror(errno));
    chmod(data, 0700);
    if (apps_write() || accounts()) return 1;
    printf("app %s: user app-%s (uid %ld), groups: %s\n", id, id, a->uid, strcmp(a->perms, ".") ? a->perms : "none");
    return 0;
}

static int app_remove(const char *id) {
    apps_read();
    struct app *a = app_find(id);
    if (!a || !strcmp(a->perms, "-")) return fail("%s: not added", id);
    snprintf(a->perms, sizeof a->perms, "-");
    if (apps_write() || accounts()) return 1;
    printf("app %s: removed; uid %ld and its data folder stay its own\n", id, a->uid);
    return 0;
}

/* ---- the launcher: Q2PKG/1 on /run/vamp/pkg.sock (docs/platform/packages.md#starting-and-stopping-q2pkg1) ---- */

/* The capability line an API 0 app reads once at its start (/tmp/q2-addon.caps, today's Vamp's ADDON_CAPS): only the
 * words this image serves. catalogue: q2libd reads the app's catalogue.tsv and q2playd hands it the Library's plays
 * (.catplay); playlist: q2playd plays the m3u its .play names. The rest of today's words are its UI's, Q2SPOT/1's pages. */
#define API0_CAPS "catalogue playlist"
#define PKG_CLIENTS 8
#define PKG_LINE 512
#define RUNSV_UP_MS 2000 /* a new runsv has this long to open its control */
#define STOP_MS 5000     /* an app stopped has this long to end before stop answers err stop */

static char runsv_prog[300] = "/usr/bin/runsv", self_prog[300] = "/usr/bin/q2pkgd";

static void logf_(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "q2pkgd: ");
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
}

static void sleep_ms(int ms) {
    struct timespec t = {ms / 1000, (long)(ms % 1000) * 1000000L};
    nanosleep(&t, 0);
}

static int valid_id(const char *id) {
    if (!*id || strlen(id) > 63) return 0;
    for (const char *c = id; *c; ++c)
        if (!(*c >= 'a' && *c <= 'z') && !(*c >= '0' && *c <= '9') && *c != '-') return 0;
    return 1;
}

/* whether pid is a process that has not ended (a zombie, ended and not yet reaped, has) */
static int alive(long pid) {
    char p[40], b[160] = "";
    if (pid <= 1 || (kill((pid_t)pid, 0) && errno != EPERM)) return 0;
    snprintf(p, sizeof p, "/proc/%ld/stat", pid);
    FILE *f = fopen(p, "r");
    if (f) {
        if (!fgets(b, sizeof b, f)) *b = 0;
        fclose(f);
    }
    const char *s = strrchr(b, ')'); /* "pid (comm) S ...": the state after the name, which may hold anything */
    return !(s && s[1] == ' ' && s[2] == 'Z');
}

/* an API 0 app's loop: the pid in /tmp/q2-<id>, when that process lives; else 0 */
static long api0_pid(const char *id) {
    char p[120], b[32] = "";
    snprintf(p, sizeof p, "/tmp/q2-%s", id);
    FILE *f = fopen(rp(p), "r");
    if (f) {
        if (!fgets(b, sizeof b, f)) *b = 0;
        fclose(f);
    }
    long pid = strtol(b, 0, 10);
    return alive(pid) ? pid : 0;
}

/* the app's runsv: whether it runs (its supervise/ok has a reader), and whether its service runs */
static int sv_alive(const char *sv) {
    char p[760];
    snprintf(p, sizeof p, "%s/supervise/ok", sv);
    int fd = open(p, O_WRONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return 0;
    close(fd);
    return 1;
}
static int sv_running(const char *sv) {
    char p[760], b[64] = "";
    snprintf(p, sizeof p, "%s/supervise/stat", sv);
    FILE *f = fopen(p, "r");
    if (f) {
        if (!fgets(b, sizeof b, f)) *b = 0;
        fclose(f);
    }
    return sv_alive(sv) && !strncmp(b, "run", 3);
}
static int sv_control(const char *sv, char c) {
    char p[760];
    snprintf(p, sizeof p, "%s/supervise/control", sv);
    int fd = open(p, O_WRONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return -1;
    int r = write(fd, &c, 1) == 1 ? 0 : -1;
    close(fd);
    return r;
}

/* /run/vamp/sv/<id>: its run (q2pkgd app-run DIR) and a down file, so runsv starts nothing before it is told */
static int sv_make(const char *id, const char *dir, char *sv, size_t room) {
    char rel[120], p[760], text[1600];
    mkdir(rp("/run"), 0755), mkdir(rp("/run/vamp"), 0755), mkdir(rp("/run/vamp/sv"), 0755);
    snprintf(rel, sizeof rel, "/run/vamp/sv/%s", id);
    snprintf(sv, room, "%s", rp(rel));
    if (mkdir(sv, 0755) && errno != EEXIST) return logf_("%s: %s", sv, strerror(errno)), -1;
    snprintf(text, sizeof text, "#!/bin/sh\n# made by q2pkgd serve: app %s (docs/platform/packages.md#starting-and-stopping-q2pkg1)\n"
             "exec %s app-run %s%s%s 2>&1\n", id, self_prog, dir, *proot ? " --root " : "", proot);
    snprintf(p, sizeof p, "%s/run", sv);
    if (write_whole(p, text, 0755)) return -1;
    snprintf(p, sizeof p, "%s/down", sv);
    return write_whole(p, "", 0644);
}

/* the app's runsv, started when it is not running (its own session: it outlives this process) */
static int sv_up(const char *sv) {
    if (sv_alive(sv)) return 0;
    pid_t pid = fork();
    if (pid < 0) return -1;
    if (!pid) {
        setsid();
        signal(SIGCHLD, SIG_DFL), signal(SIGPIPE, SIG_DFL); /* an ignored signal stays ignored across exec */
        int null = open("/dev/null", O_RDONLY);
        if (null >= 0) dup2(null, 0), close(null);
        dup2(2, 1); /* the apps' output into this service's log */
        execl(runsv_prog, "runsv", sv, (char *)0);
        _exit(127);
    }
    for (int t = 0; t < RUNSV_UP_MS; t += 20) {
        if (sv_alive(sv)) return 0;
        sleep_ms(20);
    }
    return -1;
}

struct launch { char id[64], dir[700], sv[700]; struct manifest m; struct app *a; };

/* id's app on the card, as start, stop and status take it: 0, or the err word */
static const char *launch_find(const char *id, struct launch *l) {
    char p[120];
    memset(l, 0, sizeof *l);
    if (!valid_id(id)) return "app";
    snprintf(l->id, sizeof l->id, "%s", id);
    snprintf(p, sizeof p, "/mnt/mmc/.%s", id);
    snprintf(l->dir, sizeof l->dir, "%s", rp(p));
    snprintf(p, sizeof p, "/run/vamp/sv/%s", id);
    snprintf(l->sv, sizeof l->sv, "%s", rp(p));
    if (manifest(l->dir, &l->m)) return "app";
    if (!l->m.new_keys) {
        char run[720];
        struct stat st;
        snprintf(run, sizeof run, "%s/run", l->dir);
        return stat(run, &st) || !S_ISREG(st.st_mode) ? "app" : 0;
    }
    if (!*l->m.entry) return "app";
    apps_read();
    l->a = app_find(id);
    return !l->a || !strcmp(l->a->perms, "-") ? "not-added" : 0;
}

static int launch_running(const struct launch *l) { return sv_running(l->sv) || (!l->m.new_keys && api0_pid(l->id)); }

static const char *launch_start(struct launch *l) {
    if (launch_running(l)) return 0;
    if (!l->m.new_keys && write_whole(rp("/tmp/q2-addon.caps"), API0_CAPS "\n", 0644)) return "start";
    if (sv_make(l->id, l->dir, l->sv, sizeof l->sv) || sv_up(l->sv)) return logf_("%s: runsv did not come up", l->id), "start";
    /* a service is kept up; anything else runs once, and its own exit (an app's idle exit) stands */
    if (sv_control(l->sv, !strcmp(l->m.kind, "service") ? 'u' : 'o')) return "start";
    for (int t = 0; t < RUNSV_UP_MS && !sv_running(l->sv); t += 20) sleep_ms(20); /* runsv's word that it runs it */
    logf_("started %s", l->id);
    return 0;
}

static const char *launch_stop(struct launch *l) {
    if (sv_alive(l->sv)) sv_control(l->sv, 'd');
    long pid = l->m.new_keys ? 0 : api0_pid(l->id);
    if (pid && !sv_running(l->sv)) kill((pid_t)pid, SIGTERM); /* a loop started elsewhere: today's Stop, a TERM */
    for (int t = 0; t < STOP_MS; t += 50) {
        if (!launch_running(l)) return logf_("stopped %s", l->id), (const char *)0;
        sleep_ms(50);
    }
    return logf_("%s: still running %d s after its stop", l->id, STOP_MS / 1000), "stop";
}

/* the caller's uid may run cmd on id: root any; a package's own uid its own status and stop */
static int launch_allowed(int fd, const char *cmd, const char *id) {
    long uid = q2perm_peer_uid(fd);
    if (uid == 0) return 1;
    apps_read();
    const struct app *own = 0;
    for (int i = 0; i < n_apps; ++i)
        if (apps[i].uid == uid && strcmp(apps[i].perms, "-")) own = &apps[i];
    if (own && strcmp(cmd, "start") && !strcmp(own->id, id)) return 1;
    char who[100];
    if (own) snprintf(who, sizeof who, "app-%s", own->id);
    else snprintf(who, sizeof who, "no package");
    logf_("refused uid %ld (%s): %s %s", uid, who, cmd, id);
    return 0;
}

static struct pkg_client { int fd, hello; size_t n; char buf[PKG_LINE + 1]; } pc[PKG_CLIENTS];

static void say(struct pkg_client *c, const char *fmt, ...) {
    char b[700];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(b, sizeof b - 1, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if (n > (int)sizeof b - 2) n = (int)sizeof b - 2;
    b[n++] = '\n';
    if (send(c->fd, b, (size_t)n, MSG_NOSIGNAL) != n) close(c->fd), c->fd = -1;
}

/* one line: 0 to go on, -1 to close */
static int pkg_line(struct pkg_client *c, char *line) {
    if (!c->hello) {
        if (strcmp(line, "Q2PKG/1")) return say(c, "err version"), -1;
        c->hello = 1;
        return say(c, "Q2PKG/1 ok"), 0;
    }
    if (!strcmp(line, "caps")) return say(c, "caps start stop status"), 0;
    char cmd[16], id[PKG_LINE], extra[4];
    if (sscanf(line, "%15s %511s %3s", cmd, id, extra) != 2 || (strcmp(cmd, "start") && strcmp(cmd, "stop") && strcmp(cmd, "status")))
        return say(c, "err command"), 0;
    if (!launch_allowed(c->fd, cmd, id)) return say(c, "err permission"), 0;
    struct launch l;
    const char *err = launch_find(id, &l);
    if (!err && !strcmp(cmd, "status")) return say(c, "status %s %s", id, launch_running(&l) ? "running" : "stopped"), 0;
    if (!err) err = !strcmp(cmd, "start") ? launch_start(&l) : launch_stop(&l);
    return err ? say(c, "err %s", err) : say(c, "ok"), 0;
}

static volatile sig_atomic_t serving = 1;
static void on_term(int s) { (void)s, serving = 0; }

static int serve(void) {
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    snprintf(a.sun_path, sizeof a.sun_path, "%s", rp("/run/vamp/pkg.sock"));
    mkdir(rp("/run"), 0755), mkdir(rp("/run/vamp"), 0755);
    signal(SIGCHLD, SIG_IGN); /* the runsvs it starts are reaped by the kernel */
    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, on_term), signal(SIGINT, on_term);
    int lfd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    unlink(a.sun_path);
    if (lfd < 0 || bind(lfd, (struct sockaddr *)&a, sizeof a) || listen(lfd, PKG_CLIENTS))
        return fail("%s: %s", a.sun_path, strerror(errno));
    chmod(a.sun_path, 0666); /* no permission guards the hello: each command is checked (launch_allowed) */
    for (int i = 0; i < PKG_CLIENTS; ++i) pc[i].fd = -1;
    logf_("serving Q2PKG/1 on %s", a.sun_path);
    while (serving) {
        struct pollfd p[1 + PKG_CLIENTS];
        p[0] = (struct pollfd){.fd = lfd, .events = POLLIN};
        for (int i = 0; i < PKG_CLIENTS; ++i) p[1 + i] = (struct pollfd){.fd = pc[i].fd, .events = POLLIN};
        if (poll(p, 1 + PKG_CLIENTS, -1) < 0 && errno != EINTR) break;
        if (p[0].revents & POLLIN) {
            int fd = accept4(lfd, 0, 0, SOCK_CLOEXEC), i = 0;
            while (fd >= 0 && i < PKG_CLIENTS && pc[i].fd >= 0) ++i;
            if (fd >= 0 && i == PKG_CLIENTS) close(fd);
            else if (fd >= 0) pc[i] = (struct pkg_client){.fd = fd};
        }
        for (int i = 0; i < PKG_CLIENTS; ++i) {
            struct pkg_client *c = &pc[i];
            if (c->fd < 0 || p[1 + i].fd != c->fd || !p[1 + i].revents) continue;
            ssize_t n = recv(c->fd, c->buf + c->n, PKG_LINE - c->n, 0);
            if (n <= 0) {
                close(c->fd), c->fd = -1;
                continue;
            }
            c->n += (size_t)n;
            char *nl;
            while (c->fd >= 0 && (nl = memchr(c->buf, '\n', c->n))) {
                *nl = 0;
                if (nl > c->buf && nl[-1] == '\r') nl[-1] = 0;
                int r = pkg_line(c, c->buf);
                if (c->fd < 0) break;
                size_t used = (size_t)(nl + 1 - c->buf);
                memmove(c->buf, nl + 1, c->n - used), c->n -= used;
                if (r) close(c->fd), c->fd = -1;
            }
            if (c->fd >= 0 && c->n == PKG_LINE) close(c->fd), c->fd = -1; /* a line too long */
        }
    }
    unlink(a.sun_path);
    return 0;
}

/* An API 0 app as today's Vamp starts it, as root (it has no user): its run script, which starts the app's loop in the
 * background, leaves the loop's pid in /tmp/q2-<id> and returns; then that loop waited for until it ends, so runsv sees
 * the app running while it lives. This process is the loop's subreaper where the kernel allows, so the loop, orphaned
 * when its launcher returns, is its child to reap. A TERM (sv down, Q2PKG/1's stop) goes on to the loop, as today's Stop. */
static volatile sig_atomic_t run0_term;
static void on_run0_term(int s) { (void)s, run0_term = 1; }
static int app_run0(const char *dir, const char *id) {
    char run[720];
    snprintf(run, sizeof run, "%s/run", dir);
    struct sigaction sa = {.sa_handler = on_run0_term}; /* no SA_RESTART: a TERM ends the wait below */
    sigaction(SIGTERM, &sa, 0);
    signal(SIGCHLD, SIG_DFL);
    /* Linux 3.4's, so the Q2's 5.10 has it; QEMU's user mode refuses it (EINVAL), and then the loop, not this
     * process's child, is watched by its pid alone (the ECHILD case below) and init reaps it */
    prctl(PR_SET_CHILD_SUBREAPER, 1);
    pid_t p = fork();
    if (p < 0) return fail("%s: %s", id, strerror(errno));
    if (!p) {
        execl("/bin/sh", "sh", run, (char *)0);
        _exit(127);
    }
    int st = 0;
    while (waitpid(p, &st, 0) < 0 && errno == EINTR) {
    }
    if (!WIFEXITED(st) || WEXITSTATUS(st)) return fail("%s: its run script failed", id);
    long pid = api0_pid(id);
    if (!pid) return fail("%s: no loop in /tmp/q2-%s after its run script", id, id);
    for (pid_t w = 0; !run0_term && w != (pid_t)pid;) /* each orphan reaped, the loop's own end the last */
        if ((w = waitpid(-1, &st, 0)) < 0 && errno == ECHILD) {
            if (!alive(pid)) break; /* started elsewhere (its run script found it running): not this process's child */
            sleep_ms(500);
        }
    if (run0_term && alive(pid)) {
        kill((pid_t)pid, SIGTERM);
        for (int t = 0; t < 3000 && alive(pid); t += 50) {
            while (waitpid(-1, &st, WNOHANG) > 0) {
            }
            sleep_ms(50);
        }
    }
    while (waitpid(-1, &st, WNOHANG) > 0) { /* the loop, ended: reaped here, not left a zombie to init */
    }
    return 0;
}

static int app_run(const char *dir) {
    struct manifest m;
    const char *id = app_id(dir);
    if (manifest(dir, &m)) return 1;
    if (!m.new_keys) return app_run0(dir, id);
    if (!*m.entry) return fail("%s: a %s has no entry to run", id, m.kind);
    apps_read();
    struct app *a = app_find(id);
    if (!a || !strcmp(a->perms, "-")) return fail("%s: not added (q2pkgd app-add)", id);
    gid_t gids[16];
    int n = 0;
    gids[n++] = (gid_t)a->uid;
    for (int k = 0; Q2PERM_GROUPS[k].name && n < 16; ++k)
        if (has_perm(a, Q2PERM_GROUPS[k].name)) gids[n++] = (gid_t)Q2PERM_GROUPS[k].gid;
    uid_t u = (uid_t)a->uid;
    if (setgroups((size_t)n, gids) || setgid(u) || setuid(u)) return fail("cannot become app-%s: %s; nothing run", id, strerror(errno));
    if (getuid() != u || geteuid() != u || getgid() != u || getegid() != u || !setuid(0))
        return fail("app-%s's ids did not all take; nothing run", id);
    char data[700], home[720], *argv[32], *e = m.entry, prog[700];
    int c = 0;
    snprintf(data, sizeof data, "/mnt/data/vamp/app/%s", id);
    if (chdir(dir)) return fail("%s: %s", dir, strerror(errno));
    for (char *w = strtok(e, " "); w && c < 31; w = strtok(0, " ")) argv[c++] = w;
    argv[c] = 0;
    snprintf(prog, sizeof prog, "./%s", argv[0]);
    snprintf(home, sizeof home, "HOME=%s", data);
    char *env[] = {home, "PATH=/usr/bin:/bin", 0};
    execve(prog, argv, env);
    return fail("%s: %s", prog, strerror(errno));
}

static int apps_main(int argc, char **argv) {
    const char *arg = 0;
    for (int i = 2; i < argc; ++i) {
        if (!strcmp(argv[i], "--root") && i + 1 < argc) snprintf(proot, sizeof proot, "%s", argv[++i]);
        else if (!strcmp(argv[1], "serve") && !strcmp(argv[i], "--runsv") && i + 1 < argc) snprintf(runsv_prog, sizeof runsv_prog, "%s", argv[++i]);
        else if (!strcmp(argv[1], "serve") && !strcmp(argv[i], "--self") && i + 1 < argc) snprintf(self_prog, sizeof self_prog, "%s", argv[++i]);
        else if (!arg && argv[i][0] != '-') arg = argv[i];
        else return fprintf(stderr, "q2pkgd: %s: unknown argument %s\n", argv[1], argv[i]), 2;
    }
    if (!strcmp(argv[1], "accounts")) return arg ? 2 : accounts();
    if (!strcmp(argv[1], "serve")) return arg ? 2 : serve();
    if (!arg) return fprintf(stderr, "q2pkgd: %s needs %s\n", argv[1], strcmp(argv[1], "app-remove") ? "DIR" : "ID"), 2;
    if (!strcmp(argv[1], "app-add")) return app_add(arg);
    if (!strcmp(argv[1], "app-remove")) return app_remove(arg);
    if (!strcmp(argv[1], "app-run")) return app_run(arg);
    return 2;
}

int main(int argc, char **argv) {
    const char *rootfs = "/", *card = "/mnt/mmc", *usage =
        "usage: q2pkgd stage RELEASE_DIR [--rootfs DIR] [--card DIR] | install | restore | confirm [--card DIR]"
        " | app-check DIR | app-add DIR | app-remove ID | app-run DIR | accounts [--root DIR]"
        " | serve [--root DIR] [--runsv PROG] [--self PROG]\n";
    if (argc < 2) return fputs(usage, stderr), 2;
    if (!strcmp(argv[1], "app-check")) return argc == 3 ? app_check(argv[2]) : (fputs(usage, stderr), 2);
    if (!strcmp(argv[1], "app-add") || !strcmp(argv[1], "app-remove") || !strcmp(argv[1], "app-run") || !strcmp(argv[1], "accounts") ||
        !strcmp(argv[1], "serve")) {
        int r = apps_main(argc, argv);
        return r == 2 ? (fputs(usage, stderr), 2) : r;
    }
    int first = !strcmp(argv[1], "stage") ? 3 : 2;
    if (first > argc) return fputs(usage, stderr), 2;
    for (int i = first; i < argc; ++i) {
        if (!strcmp(argv[i], "--rootfs") && i + 1 < argc) rootfs = argv[++i];
        else if (!strcmp(argv[i], "--card") && i + 1 < argc) card = argv[++i];
        else return fputs(usage, stderr), 2;
    }
    char out[600];
    snprintf(out, sizeof out, "%s/.vamp/stage/recovery-update", card);
    if (!strcmp(argv[1], "stage")) return stage(argv[2], rootfs, out);
    if (!strcmp(argv[1], "install")) return install(card);
    if (!strcmp(argv[1], "restore")) return restore(card, 0);
    if (!strcmp(argv[1], "confirm")) return restore(card, 1);
    return fputs(usage, stderr), 2;
}
