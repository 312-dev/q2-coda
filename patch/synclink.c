/* SyncLink, Shanling's phone remote, off unless the user turns it on (docs/internals.md#synclink).
 * Stock starts each of its listeners (Bluetooth RFCOMM and LE, Wi-Fi TCP 1751 and its multicast
 * discovery) only while g_synclinkflag, SYSSET SYNCLINK, is 1 (Bluetooth) or 2 (Wi-Fi), and only
 * its own page (stock's Wireless > SyncLink) writes that key, and Settings no longer leads to that
 * page (Wi-Fi and Bluetooth are Settings' own rows), so it stays off: this file turns off once what
 * older firmware left on. None of the listeners asks a phone for a password. */
#include "navigation.h"

/* After config_init (ringnav_usb_config), before platform_init goes on to Bluetooth, Wi-Fi and
 * initSyncLink: a SyncLink on from before Q2POD SYNCLINKOWN was written is turned off and saved so,
 * then the mark is written, after which SYNCLINK is the user's choice and is left alone. The
 * default config.ini stock copies in at first boot or a reset says SYNCLINK=0. ponytail: a SyncLink
 * the user turned on deliberately before this firmware goes off once too; the changelog says so, as
 * no file tells the two apart. */
void synclink_default(void) {
    if (config_value("Q2POD", "SYNCLINKOWN", 2, 0)) return;
    if (g_synclinkflag) {
        g_synclinkflag = 0;
        write_int_config(0, "SYSSET", "SYNCLINK");
    }
    write_int_config(1, "Q2POD", "SYNCLINKOWN");
}
