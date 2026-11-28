/* Automatic Time Zone (docs/internals.md#automatic-time-zone): with the switch on (Q2POD
 * AUTOZONE, on unless chosen) and Wi-Fi connected, the zone is looked up from the network's public
 * address, at most once a day, and set as the time zone page sets one. */
#include "navigation.h"

/* The lookups, in turn: each answers GET with the caller's IANA zone as plain text, over HTTPS,
 * with no key. ipinfo.io first (free to 50,000 a month without a token; ISRG Root X1 signs it),
 * ipapi.co if it fails (1,000 a day; GTS Root R4). Both roots are in scrobble.c's bundle. */
static const char *const az_url[] = { "https://ipinfo.io/timezone", "https://ipapi.co/timezone/" };
#define AZ_CA "/etc/scrobble-ca.pem"
#define AZ_POLL_MS 1000u
#define AZ_DAY_MS (24u * 3600u * 1000u) /* a lookup that set the zone is not repeated for a day */
#define AZ_RETRY_MS (10u * 60u * 1000u) /* a failed one is tried again at a new link, 10 min on */
#define AZ_SAMPLES 24                   /* a year's offsets, every 15 days or so, for the match */
#define AZ_NAME 64

static const char *const tz_names[] = TIMEZONE_NAMES;
#define TZ_COUNT (sizeof tz_names / sizeof *tz_names)

/* thread: the lookup's; running: started and not yet joined; done: it has finished; at: the start
 * of the last lookup (0 none since boot); ok: that lookup set a zone; due: one asked for at once
 * (the switch turned on); up: the link at the last poll; quiet: the zone's own write runs; polled:
 * the last poll; n, body: the answer as it comes; zone: the answer taken, "" none. */
static struct {
    unsigned long thread;
    int running, ok, due, up, quiet;
    volatile int done;
    unsigned at, polled, n;
    char body[AZ_NAME], zone[AZ_NAME];
} az __attribute__((section(".scratch")));

/* An answer that names a zone: letters, digits and _ + - / only, starting with a letter, so it
 * can't leave /usr/share/zoneinfo or read as a POSIX rule; trailing white space dropped. */
static int zone_name(char *s) {
    unsigned n = 0;
    while (s[n]) ++n;
    while (n && (s[n - 1] == '\n' || s[n - 1] == '\r' || s[n - 1] == ' ')) s[--n] = 0;
    if (!n || n >= AZ_NAME || !((s[0] | 32) >= 'a' && (s[0] | 32) <= 'z')) return 0;
    for (unsigned i = 0; i < n; ++i) {
        char c = s[i];
        if (!((c | 32) >= 'a' && (c | 32) <= 'z') && !(c >= '0' && c <= '9') && c != '_' &&
            c != '-' && c != '+' && c != '/')
            return 0;
    }
    return 1;
}

static unsigned az_got(const char *data, unsigned size, unsigned count, void *unused) {
    unsigned n = size * count;
    if (az.n + n >= AZ_NAME) return 0; /* no zone name is this long: the transfer stops */
    memcpy(az.body + az.n, data, n);
    az.n += n;
    az.body[az.n] = 0;
    return n;
}

/* The lookup's pthread: each service in turn until one names a zone. Short timeouts: it holds
 * nothing up, but a dead service should not keep Smart Wi-Fi's radio on for long. */
static void *az_worker(void *unused) {
    for (unsigned i = 0; i < sizeof az_url / sizeof *az_url && !az.zone[0]; ++i) {
        void *c = curl_easy_init();
        long code = 0;
        az.n = 0;
        az.body[0] = 0;
        if (!c) continue;
        curl_easy_setopt(c, 10002, az_url[i]); /* CURLOPT_URL */
        curl_easy_setopt(c, 20011, az_got);    /* WRITEFUNCTION */
        curl_easy_setopt(c, 99, 1L);           /* NOSIGNAL: off the UI thread */
        curl_easy_setopt(c, 78, 5L);           /* CONNECTTIMEOUT */
        curl_easy_setopt(c, 13, 10L);          /* TIMEOUT */
        curl_easy_setopt(c, 10018, "Q2Muse");  /* USERAGENT */
        curl_easy_setopt(c, 10065, AZ_CA);     /* CAINFO */
        curl_easy_setopt(c, 64, 1L);           /* SSL_VERIFYPEER */
        curl_easy_setopt(c, 81, 2L);           /* SSL_VERIFYHOST */
        int rc = curl_easy_perform(c);
        if (!rc) curl_easy_getinfo(c, 0x200002, &code); /* RESPONSE_CODE */
        reach_note(REACH_ZONE, rc, code);                 /* cloud_reach.c: the last service asked */
        curl_easy_cleanup(c);
        if (code == 200 && zone_name(az.body)) memcpy(az.zone, az.body, AZ_NAME);
    }
    az.done = 1;
    return 0;
}

/* zone's offsets from UTC (tm_gmtoff) at AZ_SAMPLES times over the year from now, TZ set to it. */
static void offsets(const char *zone, long now, long *out) {
    toolsSetTimeZone(zone);
    tzset();
    for (int k = 0; k < AZ_SAMPLES; ++k) {
        long t = now + k * (365L * 86400L / AZ_SAMPLES);
        int buf[11];
        const int *tm = localtime_r(&t, buf);
        out[k] = tm ? tm[9] : 0;
    }
}

/* The saved index for an IANA zone: its own entry in TIMEZONES, else the entry with its offset now
 * that agrees with it at the most of AZ_SAMPLES times over the next year, so its daylight saving
 * (when, and whether) comes closest; -1 for a name that isn't a zone in the rootfs, or none with
 * its offset now. Sets each zone's TZ in turn (as navigation.c tz_now does), then the saved one
 * again. */
int autozone_match(const char *zone) {
    char name[AZ_NAME];
    unsigned n = 0;
    while (zone[n] && n < AZ_NAME - 1) name[n] = zone[n], ++n;
    name[n] = 0;
    if (zone[n] || !zone_name(name)) return -1;
    for (unsigned i = 0; i < TZ_COUNT; ++i)
        if (!tk_strcmp(tz_names[i], name)) return (int)i;
    char path[96];
    tk_snprintf(path, sizeof path, "/usr/share/zoneinfo/%s", name);
    void *f = fopen(path, "rb"); /* an unknown TZ reads as UTC: it must be a zone */
    if (!f) return -1;
    fclose(f);
    long now = clock_back(wall_time()), want[AZ_SAMPLES], got[AZ_SAMPLES];
    int best = -1, score = 0;
    offsets(name, now, want);
    for (unsigned i = 0; i < TZ_COUNT; ++i) {
        offsets(tz_names[i], now, got);
        if (got[0] != want[0]) continue;
        int s = 0;
        for (int k = 0; k < AZ_SAMPLES; ++k) s += got[k] == want[k];
        if (s > score) best = (int)i, score = s;
    }
    ringnav_timezone(); /* the saved zone's TZ back */
    return best;
}

/* Index i in effect and saved, as the time zone page's pick is: g_time_zone, its TZ (demo's, and so
 * every program demo starts), the status bar's clock (ringnav_timezone) and SYSSET TIMEZONE. */
static void az_apply(int i) {
    if (i < 0 || (unsigned)i >= TZ_COUNT || i == g_time_zone) return;
    g_time_zone = (unsigned char)i;
    ringnav_timezone();
    az.quiet = 1;
    write_int_config(i, "SYSSET", "TIMEZONE");
    az.quiet = 0;
}

/* The saved zone's TZ (g_time_zone's in TIMEZONES), "" for an index no row has: q2ui's clock's (status_row.c) */
const char *autozone_tz(void) { return g_time_zone < TZ_COUNT ? tz_names[g_time_zone] : ""; }

/* The switch turned on: a lookup at the next poll with the link up, whenever the last was. */
void autozone_due(void) { az.due = 1; }

int autozone_busy(void) { return az.running; }

/* toolsWriteConfig's hook: SYSSET TIMEZONE written on the time zone page is a zone picked by hand,
 * which turns the switch off, as a phone's picker does, so the next lookup doesn't undo it. */
void autozone_config(const char *section, const char *key) {
    if (az.quiet || tk_strcmp(section, "SYSSET") || tk_strcmp(key, "TIMEZONE")) return;
    void *win = page_in_use();
    if (!win || tk_strcmp(widget_get_prop_str(win, "name", ""), "autotime_page")) return;
    if (autozone_enabled()) autozone_off();
}

/* power_poll, every AZ_POLL_MS: a finished lookup's zone set; else, with the switch on and the link
 * up (the status bar's bars and stock's connect status), a lookup started when none ran since boot,
 * the last set a zone a day ago, the last failed and the link is new (AZ_RETRY_MS on), or the
 * switch was just turned on. Nothing is sent with the switch off. */
void autozone_poll(void) {
    unsigned now = time_now_ms();
    if (az.polled && now - az.polled < AZ_POLL_MS) return;
    az.polled = now | !now;
    if (az.running) {
        if (!az.done) return;
        pthread_join(az.thread, 0);
        az.running = 0;
        int i = az.zone[0] && autozone_enabled() ? autozone_match(az.zone) : -1;
        az.ok = i >= 0;
        az_apply(i);
        return;
    }
    int up = networkGetConnectStatus() > 0 && get_wifisignal() > 0, fresh = up && !az.up;
    az.up = up;
    if (!up || !autozone_enabled() || g_testmode_flag) return;
    unsigned since = now - az.at;
    if (az.at && !az.due && since < AZ_DAY_MS && (az.ok || !fresh || since < AZ_RETRY_MS)) return;
    az.due = 0;
    az.at = now | !now;
    az.zone[0] = 0;
    az.done = 0;
    az.running = !pthread_create(&az.thread, 0, az_worker, 0);
}
