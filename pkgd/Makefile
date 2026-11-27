# q2pkgd (docs/pkgd.md). Built by Buildroot for the image (os/package/q2pkgd) with the release key, and by test/pkgd.py
# for this machine with a test key: make OUT=DIR KEY='{0x..,...}' (the 32 bytes of the Ed25519 public key).
OUT ?= build/host
CFLAGS ?= -O2
MONO := ../patch/monocypher
$(OUT)/q2pkgd: q2pkgd.c ../perm/q2perm.h $(MONO)/monocypher.c $(MONO)/monocypher-ed25519.c | $(OUT)
	$(CC) -std=c11 -Wall -Wextra $(CFLAGS) -I$(MONO) -I../perm '-DRELEASE_KEY=$(KEY)' -o $@ q2pkgd.c $(MONO)/monocypher.c $(MONO)/monocypher-ed25519.c $(LDFLAGS)
$(OUT):
	mkdir -p $@
