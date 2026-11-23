/* iAP2's log lines (iap2_names.c) */
#ifndef IAP2_NAMES_H
#define IAP2_NAMES_H
#include "iap2.h"

const char *iap2_name(unsigned id);
void iap2_hex(char *out, unsigned cap, const u8 *p, unsigned n);
void iap2_log(const char *fmt_label, const char *detail);
void log_msg(int from_q2, const u8 *m, unsigned len, unsigned max);

#endif
