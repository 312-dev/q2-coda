/* Stock's HTTPS (docs/internals.md#stock-https): every curl_easy_setopt call with which stock's
 * helpers turn certificate checks off jumps here instead, and the transfer is verified against the
 * firmware's copy of Mozilla's CA bundle. */
#include "peq.h"

#define STOCK_CA "/etc/ssl/certs/ca-certificates.crt" /* tools/build.py STOCK_CA */

/* curl_easy_setopt(c, SSL_VERIFYPEER or SSL_VERIFYHOST, 0) in stock's helpers (tools/build.py
 * STOCK_TLS_CALLS): both checks on, the bundle as CAINFO and no date checks, whichever of the two
 * calls it was; stock's own option and value are dropped. */
int ringnav_stock_tls(void *c, int option, long value) {
    (void)option, (void)value;
    curl_easy_setopt(c, 10065, STOCK_CA);    /* CAINFO */
    curl_easy_setopt(c, 81, 2L);             /* SSL_VERIFYHOST */
    curl_easy_setopt(c, 20108, tls_untimed); /* SSL_CTX_FUNCTION: no date checks */
    return curl_easy_setopt(c, 64, 1L);      /* SSL_VERIFYPEER */
}
