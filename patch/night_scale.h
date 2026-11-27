/* Night Shift's colour temperatures (docs/internals.md#night-shift), shared by demo's (patch/nightshift.c) and q2ui's
 * (ui/fb.c): NS_WARM to NS_COOL by NS_STEP, NS_DEFAULT a new install's, and green's and blue's scale at each, of 255
 * (Tanner Helland's fit to the black-body colours, where red stays 255 below 6600K). */
#ifndef NIGHT_SCALE_H
#define NIGHT_SCALE_H
#define NS_WARM 2000
#define NS_COOL 5000
#define NS_STEP 100
#define NS_DEFAULT 3400
#define NS_GREEN                                                                                                       \
    { 137, 142, 146, 151, 155, 159, 163, 167, 170, 174, 177, 180, 184, 187, 190, 193,                                 \
      195, 198, 201, 203, 206, 208, 211, 213, 215, 218, 220, 222, 224, 226, 228 }
#define NS_BLUE                                                                                                        \
    { 14,  27,  39,  50,  61,  70,  79,  87,  95,  103, 110, 117, 123, 129, 135, 141,                                 \
      146, 151, 157, 161, 166, 171, 175, 179, 183, 187, 191, 195, 199, 202, 206 }
#endif
