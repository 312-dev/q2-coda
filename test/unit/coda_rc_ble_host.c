/* For test_coda_rc_daemon.py: coda_rc.c built for this machine without BlueZ (the daemon runs --no-ble, so
 * none of these is reached but ble_start's absence). */
#include "coda_rc.h"

int ble_start(void) { return -1; }
int ble_events(void) { return 0; }
void ble_handle(void) {}
int ble_write(const uint8_t *p, size_t n) { (void)p, (void)n; return -1; }
void ble_disconnect(void) {}
void ble_stop(void) {}
int ble_retry_due(uint32_t now_ms) { (void)now_ms; return 0; }
