/* QQ Music's encrypted songs (docs/player.md#qq-music), as hciplayer's stream filter does them (0x4a9f90-0x4aa740):
 * a URL with qm_songid=ID has its key in /tmp/qm_ID, which demo writes, and every block read is decrypted in place by
 * its offset in the file through stock's libQMDecryption.so, loaded on first use. Whatever goes wrong before the key
 * is taken (no song id, a bad character in it, no key file, an empty one, a key the library refuses), the stream is
 * read as it is, as stock reads it; stock's log lines are kept, on stderr. */
#include "q2player.h"
#include <ctype.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "peq.h" /* PEQ_ROOT: the Q2's paths, or a host test's directory */

struct qq {
    void *handle;
};

static void *(*create)(const char *ekey, int length);
static int (*decrypt)(void *handle, int64_t offset, unsigned char *data, int n);
static void (*destroy)(void *handle);

static int load(void) {
    static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
    static void *lib;
    pthread_mutex_lock(&mu);
    if (!lib && (lib = dlopen("libQMDecryption.so", RTLD_NOW))) {
        create = (void *(*)(const char *, int))dlsym(lib, "CreateDecryption");
        decrypt = (int (*)(void *, int64_t, unsigned char *, int))dlsym(lib, "Decrypt");
        destroy = (void (*)(void *))dlsym(lib, "DestroyDecryption");
        if (!create || !decrypt || !destroy) dlclose(lib), lib = NULL;
    }
    int ok = lib != NULL;
    pthread_mutex_unlock(&mu);
    if (!ok) fprintf(stderr, "[qmdec] libQMDecryption.so: %s\n", dlerror());
    return ok;
}

/* The song id after qm_songid=, to the next &, #, | or } or the end: 1 to 127 letters, digits, _ and -. */
static int song_id(const char *url, char id[128]) {
    const char *p = strstr(url, "qm_songid="), *e;
    if (!p || !p[10]) return 0;
    p += 10;
    for (e = p; *e && *e != '&' && *e != '#' && *e != '|' && *e != '}'; e++) {}
    if (e == p || e - p >= 128) return 0;
    for (const char *c = p; c < e; c++)
        if (!isalnum((unsigned char)*c) && *c != '_' && *c != '-') {
            fprintf(stderr, "[qmdec] bad songid char 0x%02x\n", (unsigned char)*c);
            return 0;
        }
    memcpy(id, p, e - p);
    id[e - p] = 0;
    return 1;
}

struct qq *qq_open(const char *url) {
    char id[128], path[160], key[1024], *k = key;
    FILE *f = NULL;
    if (!song_id(url, id)) return NULL;
    snprintf(path, sizeof path, "%s/qm_%s", PEQ_ROOT "/tmp", id);
    for (int i = 0; i < 5 && !(f = fopen(path, "rb")); i++) usleep(20000); /* demo may be writing it */
    if (!f) {
        fprintf(stderr, "[qmdec] ekey file %s not found\n", path);
        return NULL;
    }
    if (!fgets(key, sizeof key, f)) key[0] = 0;
    fclose(f);
    for (size_t n = strlen(key); n && strchr(" \t\r\n", key[n - 1]); n--) key[n - 1] = 0;
    while (*k == ' ' || *k == '\t') k++;
    if (!*k) {
        fprintf(stderr, "[qmdec] ekey file %s is empty\n", path);
        return NULL;
    }
    struct qq *q = calloc(1, sizeof *q);
    if (!q || !load() || !(q->handle = create(k, (int)strlen(k)))) {
        fprintf(stderr, "[qmdec] CreateDecryption failed (songid=%s)\n", id);
        free(q);
        return NULL;
    }
    fprintf(stderr, "[qmdec] decrypt enabled (songid=%s, ekey len=%d)\n", id, (int)strlen(k));
    return q;
}

int qq_decrypt(struct qq *q, int64_t offset, uint8_t *data, int n) {
    if (n <= 0) return n;
    if (decrypt(q->handle, offset, data, n) == n) return n;
    fprintf(stderr, "[qmdec] decrypt failed at offset %lld\n", (long long)offset);
    return -1;
}

void qq_close(struct qq *q) {
    if (!q) return;
    destroy(q->handle);
    free(q);
}
