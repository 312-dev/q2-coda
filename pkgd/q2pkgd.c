/* q2pkgd: Vamp OS's update service (docs/vamp-os.md#installer-and-updates, docs/pkgd.md), its first slice:
 *
 *     q2pkgd stage RELEASE_DIR [--rootfs DIR] [--card DIR]    a release checked, composed, staged beside the rescue copy
 *     q2pkgd install [--card DIR]                             the staged image put where Shanling's recovery reads it
 *     q2pkgd restore [--card DIR]                             at boot, first: the rescue copy put back in recovery-update/
 *     q2pkgd confirm [--card DIR]                             after a good boot: the same, and the install no longer pending
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
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#include "monocypher-ed25519.h"

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
    static const char *const map[][2] = {{"module_driver/", "vendor/module_driver/"}, {"firmware/", "vendor/firmware/"},
                                         {"usr/data/", "vendor/mcu/"}};
    for (unsigned i = 0; i < 3; ++i)
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
        place(rel.file[i].path, where, sizeof where);
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

int main(int argc, char **argv) {
    const char *rootfs = "/", *card = "/mnt/mmc", *usage =
        "usage: q2pkgd stage RELEASE_DIR [--rootfs DIR] [--card DIR] | install | restore | confirm [--card DIR]\n";
    if (argc < 2) return fputs(usage, stderr), 2;
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
