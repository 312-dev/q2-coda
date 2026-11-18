/* The unified log's line (docs/internals.md#unified-log), shared by demo's ulog.c, which writes it
 * to its stderr, and q2guard's q2log_core.c, which stamps the lines it reads that have none:
 *
 *     2026-11-17T14:35:02Z 5123456 W usb plug, 40 ms
 *
 * the UTC time (the Q2's clock, maybe never set), the uptime in ms (CLOCK_MONOTONIC), a level (E, W
 * or I), a source (ui, player, usb, bt, wifi, guard, kernel) and the message on the rest of the
 * line. No libc: each side has its own declarations. */
#ifndef ULOG_LINE_H
#define ULOG_LINE_H

struct ulog_put {
    char *p, *end; /* end: the last byte that may be written */
};
static inline void ulog_c(struct ulog_put *b, char c) {
    if (b->p < b->end) *b->p++ = c;
}
static inline void ulog_s(struct ulog_put *b, const char *s) {
    while (*s) ulog_c(b, *s++);
}
static inline void ulog_pad(struct ulog_put *b, long v, int width) { /* zero-padded */
    char d[12];
    int n = 0;
    if (v < 0) v = 0;
    do d[n++] = (char)('0' + v % 10);
    while ((v /= 10) && n < 12);
    while (n < width) d[n++] = '0';
    while (n) ulog_c(b, d[--n]);
}
/* t, seconds since 1970, as the UTC date and time: days to the civil date (Howard Hinnant's) */
static inline void ulog_time(struct ulog_put *b, long t) {
    long days = t / 86400, s = t % 86400;
    if (s < 0) s += 86400, --days;
    long z = days + 719468, era = (z >= 0 ? z : z - 146096) / 146097;
    long doe = z - era * 146097, yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    long doy = doe - (365 * yoe + yoe / 4 - yoe / 100), mp = (5 * doy + 2) / 153;
    long d = doy - (153 * mp + 2) / 5 + 1, m = mp < 10 ? mp + 3 : mp - 9,
         y = yoe + era * 400 + (m <= 2);
    ulog_pad(b, y, 4), ulog_c(b, '-'), ulog_pad(b, m, 2), ulog_c(b, '-'), ulog_pad(b, d, 2),
        ulog_c(b, 'T');
    ulog_pad(b, s / 3600, 2), ulog_c(b, ':'), ulog_pad(b, s / 60 % 60, 2), ulog_c(b, ':');
    ulog_pad(b, s % 60, 2), ulog_c(b, 'Z');
}

/* One line into out (size bytes, NUL-terminated), newline included: its length. msg is n bytes (its
 * own newline dropped, control characters as spaces), cut to fit. */
static inline int ulog_line(char *out, int size, long wall, unsigned up_ms, char level,
                            const char *src, const char *msg, int n) {
    if (size < 2) return 0;
    struct ulog_put b = { out, out + size - 2 }; /* room for the newline and NUL */
    ulog_time(&b, wall);
    ulog_c(&b, ' ');
    ulog_pad(&b, (long)(up_ms & 0x7fffffffu), 1);
    ulog_c(&b, ' '), ulog_c(&b, level), ulog_c(&b, ' ');
    ulog_s(&b, src);
    ulog_c(&b, ' ');
    while (n > 0 && (msg[n - 1] == '\n' || msg[n - 1] == '\r')) --n;
    for (int i = 0; i < n; ++i) ulog_c(&b, (unsigned char)msg[i] < ' ' ? ' ' : msg[i]);
    *b.p++ = '\n';
    *b.p = 0;
    return (int)(b.p - out);
}

/* 1 when s (n bytes) is already a line as ulog_line writes it: time, uptime, level and source */
static inline int ulog_is_line(const char *s, int n) {
    static const char shape[] = "dddd-dd-ddTdd:dd:ddZ ";
    int i = 0;
    for (; shape[i]; ++i) {
        if (i >= n) return 0;
        if (shape[i] == 'd' ? s[i] < '0' || s[i] > '9' : s[i] != shape[i]) return 0;
    }
    int digits = 0;
    while (i < n && s[i] >= '0' && s[i] <= '9') ++i, ++digits;
    return digits && i + 3 < n && s[i] == ' ' &&
           (s[i + 1] == 'E' || s[i + 1] == 'W' || s[i + 1] == 'I') && s[i + 2] == ' ' &&
           s[i + 3] > ' ';
}

#endif
