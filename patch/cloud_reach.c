/* Cloud reach (docs/internals.md#cloud-reach, the owner's ruling of 2026-11-27): the status row's
 * offline glyph, cloud-slash, shows while Wi-Fi is connected and any service the Q2 asks of the
 * internet can't be reached, one OR over all of them: a running card app's server (addon.c
 * addon_offline, its own reach= state) and each of demo's own requests, by the REACH_* service
 * that made it. A transfer notes its outcome (reach_note) as it ends, on whatever thread ran it:
 * down for no answer (curl failed to resolve, connect, finish TLS or the transfer in its time) or a
 * gateway's 502 or 504 (a proxy that could not reach the server: q2-subsonic's rule, without its
 * 503, which LRCLIB answers for itself when it is busy); up for any other answer, an error status
 * of the server's own too. A transfer stopped by its caller (curl's 42, a progress callback's) or
 * by its own write callback (23: a body over its buffer, a full card) says nothing of the server,
 * so it changes nothing. A service stays down until its next transfer answers, or until Wi-Fi's
 * link goes (cloud_offline, the UI thread's, clears every state then: a new link starts unknown).
 * ponytail: a service asked once in a while (Software Update's check, the time zone lookup) shows
 * down until it is asked again or the link drops; an expiry per service would lift that. */
#include "peq.h"

static volatile int reach_down[REACH_N] __attribute__((section(".scratch")));

#define CURLE_WRITE_ERROR 23
#define CURLE_ABORTED_BY_CALLBACK 42

/* service's transfer ended with curl_easy_perform's rc and, when rc is 0, its HTTP status. */
void reach_note(int service, int rc, long http) {
    if (service < 0 || service >= REACH_N || rc == CURLE_WRITE_ERROR ||
        rc == CURLE_ABORTED_BY_CALLBACK)
        return;
    reach_down[service] = rc != 0 || http == 502 || http == 504;
}

/* 1 while service's last transfer found it out of reach */
int reach_is_down(int service) { return service >= 0 && service < REACH_N && reach_down[service]; }

/* The status row's offline glyph (status_row.c SLOT_OFFLINE) and q2ui's offline=
 * (status_row_state): Wi-Fi connected and any service out of reach. With no link every state is
 * cleared. */
int cloud_offline(void) {
    if (!wifi_level()) {
        for (int i = 0; i < REACH_N; ++i) reach_down[i] = 0;
        return 0;
    }
    if (addon_offline()) return 1;
    for (int i = 0; i < REACH_N; ++i)
        if (reach_down[i]) return 1;
    return 0;
}
