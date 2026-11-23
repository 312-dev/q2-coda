/* FNV-1a over n bytes from h (FNV_SEED to start): the payload's one hash_bytes, in a file of its
 * own so the host builds (test/peq.py's editor and games) link the same definition the Q2 runs. */
#include "peq.h"

unsigned hash_bytes(unsigned h, const unsigned char *s, unsigned n) {
    for (unsigned i = 0; i < n; ++i) h = (h ^ s[i]) * 16777619u;
    return h;
}
