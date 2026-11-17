/* For test_coda_rc_mdns.py: what patch/coda_rc_mdns.c takes from the daemon (coda_rc.c), with config.ini under
 * root and the clock set by the test. */
#include <stdarg.h>
#include <stdio.h>
#include "coda_rc.h"

char root[512];
uint8_t crc_device_id[8] = {0x1a, 0x2b, 0x3c, 0x4d, 0x5e, 0x6f, 0x70, 0x81};
uint32_t now_ms = 100000;
int tcp_port = CRC_TCP_PORT;

const char *crc_path(char *buf, size_t cap, const char *path) {
    snprintf(buf, cap, "%s%s", root, path);
    return buf;
}
void crc_log(const char *fmt, ...) {
    va_list a;
    va_start(a, fmt);
    vfprintf(stderr, fmt, a);
    va_end(a);
    fputc('\n', stderr);
}
uint32_t crc_now_ms(void) { return now_ms; }
int crc_tcp_port(void) { return tcp_port; }
