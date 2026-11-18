/* card_store.c as q2guard's own object: that program has no sysroot headers, so card_store.c takes
 * its declarations from q2guard.h when Q2GUARD_DEVICE is set (docs/internals.md#overheat-guardian). */
#define Q2GUARD_DEVICE
#include "card_store.c"
