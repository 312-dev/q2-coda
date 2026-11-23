/* The car's buttons (iap2_hid.c) */
#ifndef IAP2_HID_H
#define IAP2_HID_H
#include "iap2.h"

#define HOLD_MS 500      /* Next or Previous held this long scans */
#define SCAN_KEEP_MS 500 /* and says so again this often while held */

extern unsigned n_controls; /* the descriptor's buttons */
extern u8 last_report[16];
extern unsigned held; /* Next's or Previous's usage while it is down, else 0 */
extern long held_at, scan_at;
extern int scanning;

void parse_hid(const u8 *d, unsigned len);
void scan_tick(long now);
void take_hid_report(const u8 *r, unsigned len);

#endif
