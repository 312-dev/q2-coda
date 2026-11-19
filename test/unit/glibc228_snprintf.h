/* snprintf as the Q2's glibc 2.28 runs it (libio/vsnprintf.c _IO_vsnprintf: string[0] = '\0' before
 * the format is read), for host builds whose newer glibc no longer does that: a "%s" whose argument
 * is the buffer itself comes out empty. test_q2bt_devices.py builds q2bt_core.c with it. */
#include <stdarg.h>
#include <stdio.h>
static inline int glibc228_snprintf(char *s, size_t n, const char *f, ...) {
    va_list a;
    va_start(a, f);
    if (n) s[0] = 0;
    int r = vsnprintf(s, n, f, a);
    va_end(a);
    return r;
}
#define snprintf glibc228_snprintf
