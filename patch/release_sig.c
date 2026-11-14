/* Release signatures (docs/internals.md#release-signatures). Network Update's update.tar and the App
 * Store's sources.list are signed with the q2-coda release key, Ed25519, whose public half
 * tools/build.py compiles in from assets/signing/q2-coda-release.pub; the list in turn pins each app
 * zip by its SHA-256. Monocypher 4.0.2 (patch/monocypher, BSD-2-Clause or CC0) checks a signature
 * and hashes with SHA-512; SHA-256 is here. tools/signing.py writes the same messages. */
#include "monocypher/monocypher-ed25519.h"
#include "peq.h"
#include "stock.h"

/* Named, so test/patch.py can swap in a key whose private half it holds. */
const unsigned char release_key[32] = Q2POD_SIGN_KEY;

#define CHUNK (64u << 10)

typedef struct {
    unsigned h[8], n;
    unsigned long long len;
    unsigned char buf[64];
} sha256_t;

static const unsigned K[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
};

#define ROR(x, k) ((x) >> (k) | (x) << (32 - (k)))

static void sha256_block(sha256_t *s, const unsigned char *p) {
    unsigned w[64], v[8];
    for (int i = 0; i < 16; ++i)
        w[i] = (unsigned)p[4 * i] << 24 | p[4 * i + 1] << 16 | p[4 * i + 2] << 8 | p[4 * i + 3];
    for (int i = 16; i < 64; ++i) {
        unsigned a = w[i - 15], b = w[i - 2];
        w[i] = w[i - 16] + (ROR(a, 7) ^ ROR(a, 18) ^ a >> 3) + w[i - 7] +
               (ROR(b, 17) ^ ROR(b, 19) ^ b >> 10);
    }
    memcpy(v, s->h, sizeof v);
    for (int i = 0; i < 64; ++i) {
        unsigned e = v[4], a = v[0];
        unsigned t1 = v[7] + (ROR(e, 6) ^ ROR(e, 11) ^ ROR(e, 25)) + ((e & v[5]) ^ (~e & v[6])) +
                      K[i] + w[i];
        unsigned t2 = (ROR(a, 2) ^ ROR(a, 13) ^ ROR(a, 22)) + ((a & v[1]) ^ (a & v[2]) ^ (v[1] & v[2]));
        memmove(v + 1, v, 7 * sizeof *v);
        v[4] += t1, v[0] = t1 + t2;
    }
    for (int i = 0; i < 8; ++i) s->h[i] += v[i];
}

static void sha256_init(sha256_t *s) {
    static const unsigned h[8] = { 0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                                   0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19 };
    memcpy(s->h, h, sizeof h);
    s->n = 0, s->len = 0;
}

static void sha256_update(sha256_t *s, const unsigned char *p, unsigned n) {
    s->len += n;
    while (n) {
        unsigned k = 64 - s->n < n ? 64 - s->n : n;
        memcpy(s->buf + s->n, p, k);
        s->n += k, p += k, n -= k;
        if (s->n == 64) sha256_block(s, s->buf), s->n = 0;
    }
}

static void sha256_final(sha256_t *s, unsigned char out[32]) {
    unsigned long long bits = s->len * 8;
    unsigned char pad[72] = { 0x80 };
    unsigned k = (s->n < 56 ? 56 : 120) - s->n;
    for (int i = 0; i < 8; ++i) pad[k + i] = (unsigned char)(bits >> (56 - 8 * i));
    sha256_update(s, pad, k + 8);
    for (int i = 0; i < 32; ++i) out[i] = (unsigned char)(s->h[i / 4] >> (24 - 8 * (i % 4)));
}

/* n bytes as lowercase hex into out (2n + 1 bytes). */
void sig_hex(const unsigned char *in, unsigned n, char *out) {
    for (unsigned i = 0; i < n; ++i)
        out[2 * i] = "0123456789abcdef"[in[i] >> 4], out[2 * i + 1] = "0123456789abcdef"[in[i] & 15];
    out[2 * n] = 0;
}

/* path's bytes through SHA-512 (out 64 bytes) or SHA-256 (out 32), a CHUNK at a time, so a 55 MB
 * update.tar needs 64 KB: SIG_OK, SIG_IO when it can't be read, SIG_CANCELLED once *cancel is set. */
int sig_hash_file(const char *path, int sha512, unsigned char *out, volatile int *cancel) {
    crypto_sha512_ctx big;
    sha256_t small;
    void *f = fopen(path, "rb");
    unsigned char *buf = f ? calloc(1, CHUNK) : 0;
    int r = buf ? SIG_OK : SIG_IO;
    if (sha512)
        crypto_sha512_init(&big);
    else
        sha256_init(&small);
    for (unsigned n; !r && (n = fread(buf, 1, CHUNK, f));) {
        if (sha512)
            crypto_sha512_update(&big, buf, n);
        else
            sha256_update(&small, buf, n);
        if (cancel && *cancel) r = SIG_CANCELLED;
    }
    if (!r && ferror(f)) r = SIG_IO;
    if (f) fclose(f);
    free(buf);
    if (sha512)
        crypto_sha512_final(&big, out);
    else
        sha256_final(&small, out);
    return r;
}

/* The message a release signature covers: "q2-coda-update-v1 TAG SHA512\n" for update.tar (its tag,
 * so an older release's file can't pass as a newer one's), "q2-coda-sources-v1 SHA512\n" for the
 * list (tag 0). Its length; 0 when it doesn't fit. */
unsigned sig_message(char *out, unsigned n, const char *kind, const char *tag,
                     const unsigned char digest[64]) {
    char hex[129];
    sig_hex(digest, 64, hex);
    int k = snprintf(out, n, "q2-coda-%s-v1 %s%s%s\n", kind, tag ? tag : "", tag ? " " : "", hex);
    return k > 0 && (unsigned)k < n ? (unsigned)k : 0;
}

/* Whether sig is the release key's signature of the n bytes at message. */
int sig_check(const char *message, unsigned n, const unsigned char sig[64]) {
    return n && !crypto_ed25519_check(sig, release_key, (const unsigned char *)message, n);
}

/* update.tar at path against its release's tag and update.tar.sig: SIG_OK, SIG_BAD, or
 * sig_hash_file's SIG_IO or SIG_CANCELLED. */
int sig_update(const char *path, const char *tag, const unsigned char sig[64],
               volatile int *cancel) {
    unsigned char digest[64];
    char message[256];
    int r = sig_hash_file(path, 1, digest, cancel);
    if (r) return r;
    return sig_check(message, sig_message(message, sizeof message, "update", tag, digest), sig)
               ? SIG_OK
               : SIG_BAD;
}

static int nibble(char c) {
    return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1;
}

/* 2n lowercase hex digits at s into out's n bytes; whether all were. */
int sig_unhex(const char *s, unsigned char *out, unsigned n) {
    for (unsigned i = 0; i < n; ++i) {
        int a = nibble(s[2 * i]), b = a < 0 ? -1 : nibble(s[2 * i + 1]);
        if (b < 0) return 0;
        out[i] = (unsigned char)(a << 4 | b);
    }
    return 1;
}

#define SIG_LINE "signature "

/* A signed list: text's n bytes end in a line "signature <128 hex>" (its newline optional), the
 * release key's signature of the list's message for every byte before that line. Those bytes'
 * count when it checks, else 0. */
unsigned sig_list(const char *text, unsigned n) {
    unsigned end = n, at;
    unsigned char sig[64], digest[64];
    char message[160];
    if (end && text[end - 1] == '\n') --end;
    if (end && text[end - 1] == '\r') --end;
    for (at = end; at && text[at - 1] != '\n'; --at) {}
    if (!at || end - at != sizeof SIG_LINE - 1 + 128 || memcmp(text + at, SIG_LINE, sizeof SIG_LINE - 1) ||
        !sig_unhex(text + at + sizeof SIG_LINE - 1, sig, 64))
        return 0;
    crypto_sha512(digest, (const unsigned char *)text, at);
    return sig_check(message, sig_message(message, sizeof message, "sources", 0, digest), sig) ? at
                                                                                             : 0;
}
