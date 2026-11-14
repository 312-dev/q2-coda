/* A stand-in for stock's libQMDecryption.so (docs/player.md#qq-music) with the same three C exports, for
 * test/player/run.py: a key is any text but "bad", which is refused, and Decrypt XORs each byte with a mask made from
 * the key's first byte and the byte's offset in the file, so a block decrypted at the wrong offset comes out wrong.
 * QM_MASK below is the same mask, for the test that encrypts a file. */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define QM_MASK(key0, offset) ((uint8_t)((key0) ^ ((offset) * 7 + ((offset) >> 8))))

void *CreateDecryption(const char *ekey, int ekey_length) {
    if (ekey_length <= 0 || (int)strlen(ekey) != ekey_length || !strcmp(ekey, "bad")) return NULL;
    uint8_t *h = malloc(1);
    if (h) *h = (uint8_t)ekey[0];
    return h;
}

int Decrypt(void *handle, int64_t offset, unsigned char *data, int n) {
    for (int i = 0; i < n; i++) data[i] ^= QM_MASK(*(uint8_t *)handle, (uint64_t)(offset + i));
    return n;
}

void DestroyDecryption(void *handle) { free(handle); }
