/* The platform under patch/coda_rc_core.c for test_coda_rc.py: no sockets and no files. Frames the core
 * writes collect in out[], peers live in memory, the clock is set by the test, and crc_app answers an
 * authenticated request by echoing its payload in an Ack (type 0x70 closes the session instead). */
#include <stdint.h>
#include <string.h>
#include <sys/random.h>
#include "coda_rc.h"

uint8_t crc_device_id[8] = {1, 2, 3, 4, 5, 6, 7, 8};
uint8_t out[1 << 20];
size_t out_n;
uint32_t now_ms = 1000;
crc_peer peers[CRC_MAX_PEERS];
int peers_n, save_fails, cancelled, hidden = -9;
char code[8], shown_name[CRC_NAME];

void crc_random(void *buf, size_t n) { (void)!getrandom(buf, n, 0); }
uint32_t crc_now_ms(void) { return now_ms; }
uint32_t crc_now_s(void) { return 1800000000u; }

int crc_write(crc_session *s, const uint8_t *buf, size_t n) {
    (void)s;
    if (out_n + n > sizeof out) return -1;
    memcpy(out + out_n, buf, n);
    out_n += n;
    return 0;
}

int crc_peers_load(crc_peer *p, int cap) {
    int n = peers_n < cap ? peers_n : cap;
    memcpy(p, peers, n * sizeof *p);
    return n;
}

int crc_peers_save(const crc_peer *p, int n) {
    if (save_fails) return -1;
    memcpy(peers, p, n * sizeof *p);
    peers_n = n;
    return 0;
}

void crc_pair_show(const char *c, const char *name) {
    memcpy(code, c, 7);
    strncpy(shown_name, name, sizeof shown_name - 1);
    hidden = -9;
}
void crc_pair_hide(int outcome) { hidden = outcome; code[0] = 0; }
char qr_code[8]; /* Pair a Phone's code, set by the test; spent counts the pairings that ended on it */
int spent;
int crc_code_shown(char c[7]) {
    if (!qr_code[0]) return 0;
    memcpy(c, qr_code, 7);
    return 1;
}
void crc_code_spent(void) { qr_code[0] = 0, ++spent; }
int iroh_proved; /* sessions through q2-iroh that proved their phone (crc_iroh_proved) */
void crc_iroh_proved(crc_session *s) { ++iroh_proved, s->iroh_ok = 1; }
int crc_pair_cancelled(void) { int c = cancelled; cancelled = 0; return c; }
uint32_t crc_caps(void) { return CAP_BROWSE; }

int crc_app(crc_session *s, int type, unsigned req_id, const uint8_t *p, size_t n) {
    if (type == 0x70) return -1;
    return crc_send(s, T_ACK, req_id, p, n);
}

/* for ctypes: a session, and its state */
static crc_session sessions[4];
crc_session *session(int i) {
    crc_close(&sessions[i]); /* as the daemon does before it reuses a connection's slot */
    crc_open(&sessions[i], 0, 0);
    return &sessions[i];
}
void reset(void) {
    for (int i = 0; i < 4; ++i) crc_close(&sessions[i]);
}
int session_state(int i) { return sessions[i].state; }
size_t session_size(void) { return sizeof(crc_session); }
