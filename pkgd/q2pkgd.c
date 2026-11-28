/* q2pkgd: VampOS's update service (docs/vamp-os.md#installer-and-updates, docs/pkgd.md):
 *
 *     q2pkgd stage RELEASE_DIR [--rootfs DIR] [--card DIR]    a release checked, composed, staged beside the rescue copy
 *     q2pkgd install [--card DIR]                             the staged image put where Shanling's recovery reads it
 *     q2pkgd restore [--card DIR]                             at boot, first: the rescue copy put back in recovery-update/
 *     q2pkgd confirm [--card DIR]                             after a good boot: the same, and the install no longer pending
 *     q2pkgd download [--feed URL] [--tag TAG] [--ca FILE] [--timeout S] [--rootfs DIR] [--card DIR]
 *                                                             a release fetched, checked and composed for the next card boot
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
 *   confirm  restore, then pending removed. VampOS runs it once its services have been up a minute (/etc/sv/q2pkgd-confirm).
 *
 *   download a release from the feed (FEED, a GitHub repository; --feed another) over HTTPS verified against --ca
 *            (/etc/scrobble-ca.pem): the tag from releases/latest's redirect (or --tag), release.sig and release.txt
 *            fetched and checked first, then the image onto the card and staged as stage does it, and the composed
 *            image put in .vamp/os/ (vampos.squashfs, its .sha256 and release.txt), where the next card boot reads it
 *            (patch/q2vampos.sh). The switch (boot-once, boot-always) is left as it is. A release the card has, or a
 *            latest tag not newer than the card's, fetches no image; any failure leaves .vamp/os/ as it was.
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

#include <ctype.h>
#include <grp.h>
#include <netdb.h>
#include <strings.h>
#include <sys/file.h>
#include <sys/statvfs.h>
#include <sys/types.h>

#include <mbedtls/ctr_drbg.h>
#include <mbedtls/entropy.h>
#include <mbedtls/net_sockets.h>
#include <mbedtls/ssl.h>
#include <mbedtls/x509_crt.h>
#include <psa/crypto.h>

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
    memset(&rel, 0, sizeof rel);
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

/* release.txt read into rel and release.sig checked against it with the compiled-in key */
static int release_signed(const char *dir) {
    char p[1000], txt_sha512[129];
    size_t n;
    snprintf(p, sizeof p, "%s/release.txt", dir);
    char *text = slurp(p, &n);
    if (!text) return fail("no %s", p);
    int bad = hash_file(p, 1, txt_sha512, 0) ? fail("cannot read %s", p) : read_release(text);
    free(text);
    if (bad) return 1;
    char message[256];
    int m = snprintf(message, sizeof message, "q2-coda-vampos-v1 %s %s\n", rel.tag, txt_sha512);
    unsigned char sig[64];
    snprintf(p, sizeof p, "%s/release.sig", dir);
    FILE *f = fopen(p, "rb");
    size_t got = f ? fread(sig, 1, sizeof sig, f) : 0;
    if (f) fclose(f);
    if (got != 64 || crypto_ed25519_check(sig, release_key, (const unsigned char *)message, (size_t)m))
        return fail("release.sig is not the release key's signature of release.txt");
    return 0;
}

/* out: where rootfs.squashfs lands (the card's .vamp/stage/recovery-update for stage); digest, when given, its SHA-256 */
static int stage(const char *dir, const char *rootfs, const char *out, char *digest) {
    char p[1000], q[800], h[129];
    /* 1. the signature */
    if (release_signed(dir)) return 1;
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
        if (hash_file(p, 0, h, 0) || strcmp(h, rel.file[i].sha256)) { /* not at its stock path: VampOS keeps it in /vendor */
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
    if (digest) return memcpy(digest, h, 64), digest[64] = 0, 0;
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
    printf("installed: %s holds the staged image; hold Previous and power on with Centre to flash it. VampOS puts the "
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

/* ---- download: a release from the feed into the card's .vamp/os/ for the next card boot (docs/pkgd.md#download) ---- */

#ifndef FEED /* the repository whose releases are fetched (os/package/q2pkgd: Q2PKGD_FEED) */
#define FEED "https://github.com/312-dev/q2-coda"
#endif
#define CONNECT_S 15
#define REDIRECTS 5
static int dl_timeout = 30; /* no byte for this long ends a transfer (--timeout) */
static const char *dl_ca = "/etc/scrobble-ca.pem";
static char dl_err[300];

static int dl_fail(const char *fmt, ...) { /* the reason, for the caller to say */
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(dl_err, sizeof dl_err, fmt, ap);
    va_end(ap);
    return -1;
}

static int tcp_connect(const char *host, const char *port) {
    struct addrinfo hints = {.ai_family = AF_UNSPEC, .ai_socktype = SOCK_STREAM}, *res, *a;
    int fd = -1;
    if (getaddrinfo(host, port, &hints, &res)) return -1;
    for (a = res; a && fd < 0; a = a->ai_next) {
        fd = socket(a->ai_family, a->ai_socktype | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
        if (fd < 0) continue;
        struct pollfd pf = {.fd = fd, .events = POLLOUT};
        int err = 0;
        socklen_t el = sizeof err;
        if ((connect(fd, a->ai_addr, a->ai_addrlen) && errno != EINPROGRESS) || poll(&pf, 1, CONNECT_S * 1000) != 1 ||
            getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &el) || err)
            close(fd), fd = -1;
    }
    freeaddrinfo(res);
    if (fd >= 0) fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) & ~O_NONBLOCK);
    return fd;
}

/* mbedTLS's I/O on the socket (the image's mbedTLS has no net module); a read waits as mbedTLS's read timeout says */
static int bio_send(void *ctx, const unsigned char *b, size_t n) {
    ssize_t w = send(*(int *)ctx, b, n, MSG_NOSIGNAL);
    return w >= 0 ? (int)w : errno == EINTR ? MBEDTLS_ERR_SSL_WANT_WRITE : MBEDTLS_ERR_NET_SEND_FAILED;
}

static int bio_recv(void *ctx, unsigned char *b, size_t n, uint32_t ms) {
    struct pollfd pf = {.fd = *(int *)ctx, .events = POLLIN};
    int r = poll(&pf, 1, ms ? (int)ms : -1);
    if (r == 0) return MBEDTLS_ERR_SSL_TIMEOUT;
    if (r < 0) return errno == EINTR ? MBEDTLS_ERR_SSL_WANT_READ : MBEDTLS_ERR_NET_RECV_FAILED;
    ssize_t got = recv(pf.fd, b, n, 0);
    return got >= 0 ? (int)got : errno == EINTR ? MBEDTLS_ERR_SSL_WANT_READ : MBEDTLS_ERR_NET_RECV_FAILED;
}

/* a header's value from the block hdr (lines after the status line), 0 when it has none */
static const char *header(const char *hdr, const char *name, char *out, size_t room) {
    size_t k = strlen(name);
    for (const char *l = strstr(hdr, "\r\n"); l && l[2]; l = strstr(l + 2, "\r\n"))
        if (!strncasecmp(l + 2, name, k) && l[2 + k] == ':') {
            const char *v = l + 3 + k;
            while (*v == ' ' || *v == '\t') v++;
            snprintf(out, room, "%.*s", (int)strcspn(v, "\r\n"), v);
            return out;
        }
    return 0;
}

/* One GET over verified TLS. The status, with Location in loc for a redirect; for a 200 the body, which must have a
 * Content-Length (and be want bytes when want >= 0), into f or into mem (at most room - 1 bytes, then a 0). -1 with
 * dl_err when it could not be had. */
static long get(const char *url, char *loc, size_t locn, long long want, FILE *f, char *mem, size_t room, size_t *got_n) {
    char host[256], port[8] = "443", where[512];
    const char *h = url + 8, *slash, *colon;
    if (strncmp(url, "https://", 8)) return dl_fail("%.120s is not https", url);
    if (!(slash = strchr(h, '/'))) slash = h + strlen(h);
    colon = memchr(h, ':', (size_t)(slash - h));
    snprintf(host, sizeof host, "%.*s", (int)((colon ? colon : slash) - h), h);
    if (colon) snprintf(port, sizeof port, "%.*s", (int)(slash - colon - 1), colon + 1);
    snprintf(where, sizeof where, "%s", *slash ? slash : "/");
    mbedtls_ssl_context ssl;
    mbedtls_ssl_config conf;
    mbedtls_x509_crt ca;
    mbedtls_ctr_drbg_context drbg;
    mbedtls_entropy_context ent;
    int fd = -1;
    long code = -1;
    mbedtls_ssl_init(&ssl), mbedtls_ssl_config_init(&conf), mbedtls_x509_crt_init(&ca), mbedtls_ctr_drbg_init(&drbg);
    mbedtls_entropy_init(&ent);
    psa_crypto_init();
    char req[1024], hdr[16384];
    int n = snprintf(req, sizeof req, "GET %s HTTP/1.1\r\nHost: %s%s%s\r\nUser-Agent: Vamp\r\nAccept: */*\r\nConnection: close\r\n\r\n",
                     where, host, colon ? ":" : "", colon ? port : "");
    int ok = n > 0 && n < (int)sizeof req;
    if (ok && (mbedtls_ctr_drbg_seed(&drbg, mbedtls_entropy_func, &ent, (const unsigned char *)"q2pkgd", 6) ||
               mbedtls_x509_crt_parse_file(&ca, dl_ca) < 0 ||
               mbedtls_ssl_config_defaults(&conf, MBEDTLS_SSL_IS_CLIENT, MBEDTLS_SSL_TRANSPORT_STREAM, MBEDTLS_SSL_PRESET_DEFAULT)))
        ok = 0, dl_fail("no TLS: cannot read %s", dl_ca);
    if (ok) {
        mbedtls_ssl_conf_authmode(&conf, MBEDTLS_SSL_VERIFY_REQUIRED); /* the signature is checked too, but a verified host first */
        mbedtls_ssl_conf_ca_chain(&conf, &ca, 0);
        mbedtls_ssl_conf_rng(&conf, mbedtls_ctr_drbg_random, &drbg);
        mbedtls_ssl_conf_read_timeout(&conf, (uint32_t)dl_timeout * 1000);
        if (mbedtls_ssl_setup(&ssl, &conf) || mbedtls_ssl_set_hostname(&ssl, host)) ok = 0, dl_fail("no TLS for %s", host);
        else if ((fd = tcp_connect(host, port)) < 0) ok = 0, dl_fail("cannot reach %s", host);
    }
    if (ok) {
        mbedtls_ssl_set_bio(&ssl, &fd, bio_send, 0, bio_recv);
        int r;
        while ((r = mbedtls_ssl_handshake(&ssl)) == MBEDTLS_ERR_SSL_WANT_READ || r == MBEDTLS_ERR_SSL_WANT_WRITE) {
        }
        if (r) ok = 0, dl_fail("%s: TLS refused (-0x%x)", host, (unsigned)-r);
    }
    for (int off = 0; ok && off < n;) {
        int w = mbedtls_ssl_write(&ssl, (const unsigned char *)req + off, (size_t)(n - off));
        if (w > 0) off += w;
        else if (w != MBEDTLS_ERR_SSL_WANT_WRITE) ok = 0, dl_fail("%s: the request was not sent", host);
    }
    size_t hn = 0;
    char *end = 0;
    while (ok && !end) { /* the head, up to its blank line */
        if (hn == sizeof hdr - 1) ok = 0, dl_fail("%s: a head too long", host);
        int r = ok ? mbedtls_ssl_read(&ssl, (unsigned char *)hdr + hn, sizeof hdr - 1 - hn) : 0;
        if (!ok || r == MBEDTLS_ERR_SSL_WANT_READ) continue;
        if (r == MBEDTLS_ERR_SSL_TIMEOUT) ok = 0, dl_fail("%s: timed out (nothing for %d s)", host, dl_timeout);
        else if (r <= 0) ok = 0, dl_fail("%s: no answer", host);
        else hn += (size_t)r, hdr[hn] = 0, end = strstr(hdr, "\r\n\r\n");
    }
    if (ok && (strncmp(hdr, "HTTP/1.", 7) || hn < 12)) ok = 0, dl_fail("%s: not an HTTP answer", host);
    if (ok) {
        code = strtol(hdr + 9, 0, 10);
        end[2] = 0; /* the head's lines, each ending \r\n */
        size_t have = hn - (size_t)(end + 4 - hdr);
        char v[600];
        if (code != 200) {
            if (loc && locn) loc[0] = 0, header(hdr, "Location", loc, locn);
        } else if (header(hdr, "Transfer-Encoding", v, sizeof v) || !header(hdr, "Content-Length", v, sizeof v)) {
            code = dl_fail("%s: the answer has no length", host);
        } else {
            long long len = strtoll(v, 0, 10), done = 0;
            if (want >= 0 && len != want) code = dl_fail("%s is %lld bytes, release.txt says %lld", where, len, want);
            else if (!f && (len < 0 || (unsigned long long)len >= room)) code = dl_fail("%s: too long (%lld bytes)", where, len);
            char buf[16384], *p = end + 4;
            while (code == 200 && done < len) {
                size_t k = have;
                if (!k) {
                    int r = mbedtls_ssl_read(&ssl, (unsigned char *)buf, sizeof buf);
                    if (r == MBEDTLS_ERR_SSL_WANT_READ) continue;
                    if (r == MBEDTLS_ERR_SSL_TIMEOUT) {
                        code = dl_fail("%s: timed out (nothing for %d s, %lld of %lld bytes)", where, dl_timeout, done, len);
                        break;
                    }
                    if (r <= 0) {
                        code = dl_fail("%s: the answer was short (%lld of %lld bytes)", where, done, len);
                        break;
                    }
                    p = buf, k = (size_t)r;
                }
                have = 0;
                if ((long long)k > len - done) k = (size_t)(len - done);
                if (f ? fwrite(p, 1, k, f) != k : (memcpy(mem + done, p, k), 0)) code = dl_fail("cannot write to the card");
                done += (long long)k;
            }
            if (code == 200 && !f) mem[len] = 0, *got_n = (size_t)len;
        }
        mbedtls_ssl_close_notify(&ssl);
    }
    if (fd >= 0) close(fd);
    mbedtls_ssl_free(&ssl), mbedtls_ssl_config_free(&conf), mbedtls_x509_crt_free(&ca);
    mbedtls_ctr_drbg_free(&drbg), mbedtls_entropy_free(&ent);
    return ok ? code : -1;
}

/* get, following up to REDIRECTS redirects (each to an https:// place: GitHub's assets are on another host) */
static long fetch(const char *url, long long want, FILE *f, char *mem, size_t room, size_t *got_n) {
    char now[1024], loc[1024];
    snprintf(now, sizeof now, "%s", url);
    for (int i = 0; i <= REDIRECTS; ++i) {
        long code = get(now, loc, sizeof loc, want, f, mem, room, got_n);
        if (code < 300 || code > 308 || !*loc) return code;
        if (loc[0] == '/') { /* on the same host */
            const char *slash = strchr(now + 8, '/');
            int k = slash ? (int)(slash - now) : (int)strlen(now);
            char t[2100];
            if (snprintf(t, sizeof t, "%.*s%s", k, now, loc) >= (int)sizeof now) return dl_fail("%.120s: a redirect too long", url);
            memcpy(now, t, strlen(t) + 1);
        } else {
            snprintf(now, sizeof now, "%s", loc);
        }
    }
    return dl_fail("%.120s: too many redirects", url);
}

/* a release tag as it goes into a URL and the signed message: 1 to 31 of A-Za-z0-9._- (Network Update's rule) */
static int plain_tag(const char *t) {
    size_t k = strlen(t);
    if (!k || k > 31) return 0;
    for (; *t; ++t)
        if (!isalnum((unsigned char)*t) && !strchr("._-", *t)) return 0;
    return 1;
}

/* tags compared as versions: a leading v skipped, runs of digits as numbers (v1.10.0 after v1.9.0) */
static int tag_cmp(const char *a, const char *b) {
    a += *a == 'v', b += *b == 'v';
    while (*a || *b) {
        if (isdigit((unsigned char)*a) && isdigit((unsigned char)*b)) {
            char *ea, *eb;
            unsigned long long na = strtoull(a, &ea, 10), nb = strtoull(b, &eb, 10);
            if (na != nb) return na < nb ? -1 : 1;
            a = ea, b = eb;
            continue;
        }
        if (*a != *b) return (unsigned char)*a < (unsigned char)*b ? -1 : 1;
        a++, b++;
    }
    return 0;
}

static int write_synced(const char *path, const char *text, size_t n) {
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    int ok = fd >= 0 && write(fd, text, n) == (ssize_t)n && !fsync(fd);
    if (fd >= 0 && close(fd)) ok = 0;
    return ok ? 0 : -1;
}

static void sync_dir(const char *dir) {
    int fd = open(dir, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (fd >= 0) fsync(fd), close(fd);
}

/* the work of download, in dl (made empty by the caller, removed by it after) */
static int download_in(const char *feed, const char *want_tag, const char *card, const char *rootfs, const char *dl) {
    char url[1400], p[1400], tag[64], os[700], loc[1024];
    static char mem[65536];
    size_t n;
    snprintf(os, sizeof os, "%s/.vamp/os", card);
    /* the tag: the one asked for, else where releases/latest redirects (.../releases/tag/TAG) */
    if (want_tag) {
        if (!plain_tag(want_tag)) return fail("--tag %s is not a release tag", want_tag);
        snprintf(tag, sizeof tag, "%s", want_tag);
    } else {
        snprintf(url, sizeof url, "%s/releases/latest", feed);
        long code = get(url, loc, sizeof loc, -1, 0, mem, sizeof mem, &n);
        if (code < 0) return fail("%s", dl_err);
        const char *t = code >= 301 && code <= 308 && strstr(loc, "/releases/tag/") ? strrchr(loc, '/') + 1 : "";
        if (!plain_tag(t)) return fail("no release at %s (%ld%s%.80s)", feed, code, *loc ? " to " : "", loc);
        snprintf(tag, sizeof tag, "%s", t);
    }
    /* the signature and release.txt, checked before the image is fetched */
    const char *small[] = {"release.sig", "release.txt"};
    for (int i = 0; i < 2; ++i) {
        snprintf(url, sizeof url, "%s/releases/download/%s/%s", feed, tag, small[i]);
        long code = fetch(url, i ? -1 : 64, 0, mem, sizeof mem, &n);
        if (code < 0) return fail("%s %s: %s", tag, small[i], dl_err);
        if (code != 200) return fail(i ? "%s: no %s (%ld)" : "%s: release not signed (%s: %ld): not downloaded", tag, small[i], code);
        snprintf(p, sizeof p, "%s/%s", dl, small[i]);
        if (write_synced(p, mem, n)) return fail("cannot write %s", p);
    }
    if (release_signed(dl)) return 1;
    if (strcmp(rel.tag, tag)) return fail("release.txt's tag %s is not %s: not downloaded", rel.tag, tag);
    /* the card has it already, or something newer */
    char have[65] = "", card_tag[64] = "";
    snprintf(p, sizeof p, "%s/vampos.squashfs.sha256", os);
    FILE *f = fopen(p, "r");
    if (f) {
        if (fscanf(f, "%64s", have) != 1) have[0] = 0;
        fclose(f);
    }
    for (int i = 0; i < rel.composed_n; ++i)
        if (!strcmp(have, rel.composed[i].sha256)) return printf("%s is on the card already (%s)\n", tag, os), 0;
    snprintf(p, sizeof p, "%s/release.txt", os);
    if ((f = fopen(p, "r"))) {
        char line[200];
        while (fgets(line, sizeof line, f))
            if (sscanf(line, "tag %63s", card_tag) == 1) break;
        fclose(f);
    }
    if (!want_tag && *card_tag && tag_cmp(tag, card_tag) <= 0)
        return fail("%s is not newer than the card's %s: not downloaded (--tag %s to take it)", tag, card_tag, tag);
    /* room for the download and the composed copy beside it, while the card's image stays */
    struct statvfs sv;
    long long need = 2 * rel.image_bytes + (16LL << 20);
    if (statvfs(card, &sv)) return fail("cannot read %s's free space", card);
    long long freeb = (long long)sv.f_bavail * (long long)sv.f_frsize;
    if (freeb < need) return fail("the card has %lld MB free; %s needs %lld MB: not downloaded", freeb >> 20, tag, need >> 20);
    /* the image, onto the card and flushed; stage then reads it back from there */
    char part[1300], img[1300];
    snprintf(part, sizeof part, "%s/vampos.squashfs.part", dl);
    snprintf(img, sizeof img, "%s/vampos.squashfs", dl);
    snprintf(url, sizeof url, "%s/releases/download/%s/vampos.squashfs", feed, tag);
    if (!(f = fopen(part, "wb"))) return fail("cannot write %s", part);
    long code = fetch(url, rel.image_bytes, f, 0, 0, &n);
    int flushed = !fflush(f) && !fsync(fileno(f));
    if (fclose(f)) flushed = 0;
    if (code < 0) return fail("%s vampos.squashfs: %s", tag, dl_err);
    if (code != 200) return fail("%s: no vampos.squashfs (%ld)", tag, code);
    if (!flushed || rename(part, img)) return fail("cannot write %s", img);
    /* stage's checks again on the card's copy, and the image composed with this Q2's vendor set */
    char out[1300], digest[65], line[200];
    snprintf(out, sizeof out, "%s/composed", dl);
    if (stage(dl, rootfs, out, digest)) return 1;
    /* into .vamp/os: the image renamed over the card's (a running card boot keeps its open copy), then its .sha256;
     * between the two, q2vampos sees a mismatch and boots today's Vamp */
    char a[1400], b[1400];
    if (mkdirs(os)) return fail("cannot make %s", os);
    int k = snprintf(line, sizeof line, "%s  vampos.squashfs\n", digest);
    snprintf(a, sizeof a, "%s/vampos.squashfs.sha256.new", os);
    snprintf(p, sizeof p, "%s/release.txt", dl);
    char *text = slurp(p, &n);
    snprintf(b, sizeof b, "%s/release.txt.new", os);
    int bad = !text || write_synced(a, line, (size_t)k) || write_synced(b, text, n);
    free(text);
    if (bad) return unlink(a), unlink(b), fail("cannot write to %s", os);
    snprintf(p, sizeof p, "%s/rootfs.squashfs", out);
    char dst[1400];
    snprintf(dst, sizeof dst, "%s/vampos.squashfs", os);
    if (rename(p, dst)) return unlink(a), unlink(b), fail("cannot write %s", dst);
    snprintf(dst, sizeof dst, "%s/vampos.squashfs.sha256", os);
    if (rename(a, dst)) return fail("cannot write %s", dst);
    snprintf(dst, sizeof dst, "%s/release.txt", os);
    if (rename(b, dst)) return fail("cannot write %s", dst);
    sync_dir(os);
    printf("downloaded %s: %s/vampos.squashfs (%s), the next card boot's\n", tag, os, digest);
    return 0;
}

static int download(const char *feed, const char *tag, const char *card, const char *rootfs) {
    char vamp[700], lock[800], dl[800];
    snprintf(vamp, sizeof vamp, "%s/.vamp", card);
    snprintf(lock, sizeof lock, "%s/download.lock", vamp);
    snprintf(dl, sizeof dl, "%s/download", vamp);
    if (mkdirs(vamp)) return fail("cannot make %s", vamp);
    int fd = open(lock, O_RDWR | O_CREAT | O_CLOEXEC, 0644);
    if (fd < 0) return fail("cannot open %s", lock);
    if (flock(fd, LOCK_EX | LOCK_NB)) return close(fd), fail("another download is running (%s)", lock);
    remove_tree(dl); /* a download that was stopped part way */
    int r = mkdirs(dl) ? fail("cannot make %s", dl) : download_in(feed, tag, card, rootfs, dl);
    remove_tree(dl);
    close(fd);
    return r;
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
    if (api > API_SERVED) return fail("q2app: needs API %ld; this VampOS serves API %d", api, API_SERVED);
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
 * (.catplay); playlist: q2playd plays the m3u its .play names; ahead: q2playd writes Download Ahead's .ahead (the app's
 * .stream is the UI's to read); ratings and likes: q2playd writes ratings.out and reads ratings.in
 * (docs/playd.md#ratings-and-likes). The rest of today's words are its UI's, Q2SPOT/1's pages. */
#define API0_CAPS "catalogue playlist ahead ratings likes grow"
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
        " | download [--feed URL] [--tag TAG] [--ca FILE] [--timeout S] [--rootfs DIR] [--card DIR]"
        " | app-check DIR | app-add DIR | app-remove ID | app-run DIR | accounts [--root DIR]"
        " | serve [--root DIR] [--runsv PROG] [--self PROG]\n";
    if (argc < 2) return fputs(usage, stderr), 2;
    if (!strcmp(argv[1], "app-check")) return argc == 3 ? app_check(argv[2]) : (fputs(usage, stderr), 2);
    if (!strcmp(argv[1], "app-add") || !strcmp(argv[1], "app-remove") || !strcmp(argv[1], "app-run") || !strcmp(argv[1], "accounts") ||
        !strcmp(argv[1], "serve")) {
        int r = apps_main(argc, argv);
        return r == 2 ? (fputs(usage, stderr), 2) : r;
    }
    int first = !strcmp(argv[1], "stage") ? 3 : 2, dl = !strcmp(argv[1], "download");
    const char *feed = FEED, *tag = 0;
    if (first > argc) return fputs(usage, stderr), 2;
    for (int i = first; i < argc; ++i) {
        if (!strcmp(argv[i], "--rootfs") && i + 1 < argc) rootfs = argv[++i];
        else if (!strcmp(argv[i], "--card") && i + 1 < argc) card = argv[++i];
        else if (dl && !strcmp(argv[i], "--feed") && i + 1 < argc) feed = argv[++i];
        else if (dl && !strcmp(argv[i], "--tag") && i + 1 < argc) tag = argv[++i];
        else if (dl && !strcmp(argv[i], "--ca") && i + 1 < argc) dl_ca = argv[++i];
        else if (dl && !strcmp(argv[i], "--timeout") && i + 1 < argc && atoi(argv[i + 1]) > 0) dl_timeout = atoi(argv[++i]);
        else return fputs(usage, stderr), 2;
    }
    if (dl) return download(feed, tag, card, rootfs);
    char out[600];
    snprintf(out, sizeof out, "%s/.vamp/stage/recovery-update", card);
    if (!strcmp(argv[1], "stage")) return stage(argv[2], rootfs, out, 0);
    if (!strcmp(argv[1], "install")) return install(card);
    if (!strcmp(argv[1], "restore")) return restore(card, 0);
    if (!strcmp(argv[1], "confirm")) return restore(card, 1);
    return fputs(usage, stderr), 2;
}
