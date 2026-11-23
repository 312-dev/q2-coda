/* What q2_iap.c takes of the iAP2 side (docs/iphone-mode.md#iap2): the car's reports in, the clock, the link's and
 * the car's state its loop, its log and its state file read, and the options main sets. */
#ifndef Q2_IAP2_H
#define Q2_IAP2_H
#include "iap2.h"
#include "iap2_names.h"

extern const u8 iap2_probe[6]; /* iAP2's detect, FF 55 02 00 EE 10 */
extern int seen, link_up, trace_quiet, audio_on, ever_identified;
extern long link_quiet_ms, health_ms;
extern char car_name[64];
extern const unsigned iap2_rates[9];
extern unsigned audio_hz;

int iap2_bare_ack(const u8 *r, unsigned n);
int iap2_report(const u8 *r, unsigned len);
void iap2_lost(const char *why);
int iap2_rate_index(unsigned hz);
void iap2_audio_rate(unsigned hz);
void iap2_art_slow(void);
void iap2_art_done(int found);
void iap2_tick(void);
long iap2_due(long now);
void iap2_connect(const struct iap2_host *h);
void iap2_session_end(void);

#endif
