/* test_usb_fsm.py's handle on struct usb_fsm's layout as this machine's compiler makes it. */
#include <stddef.h>

#include "../../patch/usb_fsm.h"

int usb_fsm_shadow_offset(void) { return (int)offsetof(struct usb_fsm, shadow); }
int usb_fsm_size(void) { return (int)sizeof(struct usb_fsm); }
