/* iAP2's control session (iap2_session.c) */
#ifndef IAP2_SESSION_H
#define IAP2_SESSION_H
#include "iap2.h"

extern int identified, now_playing, np_sent, audio_on;
extern int ever_identified; /* this cable: identified kept after the link is lost, for the summary */
extern long auth_due, ident_due, availability_due, np_at; /* 0: not due */
extern u32 media_mask, playback_mask;
extern char car_name[64]; /* the car's identification name, for the status file (q2_iap.c state_write) */
extern const unsigned iap2_rates[9];
extern unsigned audio_hz;

void iap2_control_reset(void);
void iap2_control_link_up(void);
void iap2_control_receive(const u8 *msg, unsigned len);
void accept_identification(void);
void send_carplay_availability(void);
int iap2_rate_index(unsigned hz);
void iap2_audio_rate(unsigned hz);

#endif
