/* iAP2's control messages (iap2_msg.c) */
#ifndef IAP2_MSG_H
#define IAP2_MSG_H
#include "iap2.h"

/* the message being built */
struct iap2_msg {
    u8 b[MAX_PACKET];
    unsigned len, group;
    u16 id;
    int ok;
};
extern struct iap2_msg m;

void msg_start(u16 id);
void param(u16 id, const void *data, unsigned n);
void param_u8(u16 id, u8 v);
void param_u16(u16 id, u16 v);
void param_u32(u16 id, u32 v);
void param_u64(u16 id, u32 hi, u32 lo);
void param_str(u16 id, const char *s);
void group_start(u16 id);
void group_end(void);
void msg_send(void);
void send_empty(u16 id);
const u8 *find(const u8 *data, unsigned len, u16 id, unsigned *n);
u32 get_be(const u8 *p, unsigned n);

#endif
