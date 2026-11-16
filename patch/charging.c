/* Charging (docs/internals.md#charging, docs/oled-design.md#status-row): what the Q2 says while a
 * cable is in. */
#include "navigation.h"

/* 1 while the charger cannot keep up with the Q2: orange in the status row (list_marks.c
 * battery_slim) and its toasts. ponytail: nothing reports it. The AXP2101's driver
 * (axp2101-power-supply) exposes the battery's status, level, voltage, temperature and times to
 * empty and full, and stock's demo reads only battery/status and voltage_now; no input current
 * limit, charger type or VBUS state reaches user space that the firmware or the docs know of. A
 * real signal (the PMIC's input-limit flag, or the battery's current going negative while it says
 * Charging) would lift this; a guess from the level would not. */
int charge_weak(void) { return 0; }
