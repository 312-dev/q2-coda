/* The platform under patch/coda_rc_avrcp.c and coda_rc_lib.c for test_coda_rc_avrcp.py: files under root (the
 * test's directory), the shim's commands caught in cmd[] instead of written, Now Playing set by the test, and
 * lib_request's answers routed to avrcp_capture as coda_rc.c's crc_write does. */
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include "coda_rc.h"

char root[512];
char cmd[128];
int shim_alive = 1, qgen, qpos, play_state;
uint8_t crc_device_id[8];

const char *crc_path(char *buf, size_t cap, const char *path) {
    snprintf(buf, cap, "%s%s", root, path);
    return buf;
}

int crc_write_file(const char *path, const void *buf, size_t n, unsigned mode) {
    char p[600];
    int fd = open(crc_path(p, sizeof p, path), O_WRONLY | O_CREAT | O_TRUNC, mode);
    if (fd < 0) return -1;
    int ok = write(fd, buf, n) == (ssize_t)n;
    close(fd);
    return ok ? 0 : -1;
}

void crc_log(const char *fmt, ...) { (void)fmt; }
int crc_shim_alive(void) { return shim_alive; }

int crc_shim_command(crc_session *s, unsigned req_id, const char *verb) {
    if (!shim_alive) return crc_error(s, req_id, E_UNAVAILABLE, "demo is not answering");
    snprintf(cmd, sizeof cmd, "%s", verb);
    return 0;
}

void crc_avrcp_now(int *g, int *p) { *g = qgen, *p = qpos; }
int crc_avrcp_state(void) { return play_state; }

int crc_write(crc_session *s, const uint8_t *buf, size_t n) {
    if (s->transport == CRC_TR_AVRCP) avrcp_capture(buf, n);
    return 0;
}

/* the core's other needs: nothing here pairs */
void crc_random(void *buf, size_t n) { memset(buf, 7, n); }
uint32_t crc_now_ms(void) { return 1000; }
uint32_t crc_now_s(void) { return 1800000000u; }
int crc_peers_load(crc_peer *p, int cap) { (void)p, (void)cap; return 0; }
int crc_peers_save(const crc_peer *p, int n) { (void)p, (void)n; return -1; }
void crc_pair_show(const char *c, const char *name) { (void)c, (void)name; }
void crc_pair_hide(int outcome) { (void)outcome; }
int crc_pair_cancelled(void) { return 0; }
uint32_t crc_caps(void) { return 0; }
int crc_app(crc_session *s, int type, unsigned req_id, const uint8_t *p, size_t n) {
    (void)p, (void)n, (void)type;
    return crc_error(s, req_id, E_UNSUPPORTED, "host");
}
