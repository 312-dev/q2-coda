/* iAP2's file transfers (iap2_xfer.c) */
#ifndef IAP2_XFER_H
#define IAP2_XFER_H
#include "iap2.h"

#define QLIST_MAX 2000 /* the longest queue sent as Up Next's list */

extern u8 art_id, next_transfer; /* the cover's transfer; the next transfer's id (80 to FF) */
extern unsigned art_sent;
extern int art_going;
extern u8 ql[QLIST_MAX * 8], ql_id; /* Up Next's list on its way (ql_announce) */
extern unsigned ql_len, ql_going;
extern long ql_seen_at;

void file_send(u8 id, u8 op, const u8 *d, unsigned n);
unsigned piece_room(void);
u8 piece_op(int first, int last);
u8 transfer_take(void);
void put_id8(u8 *p, u32 hi, u32 lo);
u8 art_start(const struct iap2_np *n);
void art_setup(void);
void iap2_file(const u8 *d, unsigned n);

#endif
