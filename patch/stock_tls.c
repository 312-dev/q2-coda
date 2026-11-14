/* Stock's HTTPS (docs/internals.md#stock-https): every curl_easy_setopt call with which stock's
 * helpers turn certificate checks off jumps here instead, and the transfer is verified against the
 * firmware's copy of Mozilla's CA bundle. */
#include "peq.h"

#define STOCK_CA "/etc/ssl/certs/ca-certificates.crt" /* tools/build.py STOCK_CA */

/* curl_easy_setopt(c, SSL_VERIFYPEER or SSL_VERIFYHOST, 0) in stock's helpers (tools/build.py
 * STOCK_TLS_CALLS): both checks on and the bundle as CAINFO, whichever of the two calls it was;
 * stock's own option and value are dropped. No certificate's dates are checked: the rootfs's
 * libcurl does its TLS with an mbedTLS built without them (docs/internals.md#2038). */
int ringnav_stock_tls(void *c, int option, long value) {
    (void)option, (void)value;
    curl_easy_setopt(c, 10065, STOCK_CA);    /* CAINFO */
    curl_easy_setopt(c, 81, 2L);             /* SSL_VERIFYHOST */
    return curl_easy_setopt(c, 64, 1L);      /* SSL_VERIFYPEER */
}
