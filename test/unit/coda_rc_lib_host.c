/* For test_coda_rc_lib.py: what patch/coda_rc_lib.c takes from the daemon (coda_rc.c), with the device's
 * paths under root, which the test sets, and demo's shim always answering. */
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "coda_rc.h"

char root[512];
char shim_verb[128];

const char *crc_path(char *buf, size_t cap, const char *path) {
    snprintf(buf, cap, "%s%s", root, path);
    return buf;
}
int crc_write_file(const char *path, const void *buf, size_t n, unsigned mode) {
    char p[600];
    FILE *f = fopen(crc_path(p, sizeof p, path), "wb");
    (void)mode;
    if (!f) return -1;
    size_t w = fwrite(buf, 1, n, f);
    return fclose(f) || w != n ? -1 : 0;
}
void crc_log(const char *fmt, ...) {
    va_list a;
    va_start(a, fmt);
    vfprintf(stderr, fmt, a);
    va_end(a);
    fputc('\n', stderr);
}
int crc_shim_alive(void) { return 1; }
int crc_shim_command(crc_session *s, unsigned req_id, const char *verb) {
    snprintf(shim_verb, sizeof shim_verb, "%s", verb);
    return crc_send(s, T_ACK, req_id, 0, 0);
}
