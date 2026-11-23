/* iAP2's transport, link layer and clock (iap2_link.c): the car's reports in, the tick and its deadline, the link's
 * loss, and the link's state the other iAP2 files and q2_iap.c read */
#ifndef IAP2_LINK_H
#define IAP2_LINK_H
#include "iap2.h"

extern const u8 iap2_probe[6]; /* iAP2's detect, FF 55 02 00 EE 10 */
extern int seen, link_up, trace_quiet;
extern long link_quiet_ms, health_ms;
extern u8 control_session, file_session;
extern unsigned peer_max, window, head, count, q_count;
extern long rto;
extern int msg_quiet;
extern unsigned quiet_pos;

int backlog(void);
void enqueue(u8 session, const u8 *b, unsigned n);
int iap2_bare_ack(const u8 *r, unsigned n);
int iap2_report(const u8 *r, unsigned len);
void iap2_lost(const char *why);
void iap2_tick(void);
long iap2_due(long now);
void iap2_connect(const struct iap2_host *h);
void iap2_session_end(void);

#endif
