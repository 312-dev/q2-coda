# q2pkgd (docs/pkgd.md). Built by Buildroot for the image (os/package/q2pkgd) with the release key, and by test/pkgd.py
# for this machine with a test key: make OUT=DIR KEY='{0x..,...}' (the 32 bytes of the Ed25519 public key).
OUT ?= build/host
CFLAGS ?= -O2
MONO := ../patch/monocypher
# the repository download fetches Vamp OS releases from (docs/pkgd.md#download)
FEED ?= https://github.com/312-dev/q2-coda
# mbedTLS for download: the image's static q2-mbedtls, or MBEDTLS=PREFIX (tools/player.py's host build) for a test
TLS_LIB ?= $(if $(MBEDTLS),-L$(MBEDTLS)/lib) -lmbedtls -lmbedx509 -lmbedcrypto
$(OUT)/q2pkgd: q2pkgd.c ../perm/q2perm.h $(MONO)/monocypher.c $(MONO)/monocypher-ed25519.c | $(OUT)
	$(CC) -std=c11 -Wall -Wextra $(CFLAGS) -I$(MONO) -I../perm $(if $(MBEDTLS),-isystem $(MBEDTLS)/include) '-DRELEASE_KEY=$(KEY)' '-DFEED="$(FEED)"' \
		-o $@ q2pkgd.c $(MONO)/monocypher.c $(MONO)/monocypher-ed25519.c $(LDFLAGS) $(TLS_LIB)
$(OUT):
	mkdir -p $@
