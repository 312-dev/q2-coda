# q2scrobd (docs/vamp-os-services.md#scrobbling). Built by Buildroot for the image (os/package/q2scrobd) and by
# test/scrobd.py for this machine.
OUT ?= build/host
CFLAGS ?= -O2
SQLITE_LIB ?= -lsqlite3
# mbedTLS for the upload: the image's static q2-mbedtls, or MBEDTLS=PREFIX (tools/player.py's host build) for a test
TLS_LIB ?= $(if $(MBEDTLS),-L$(MBEDTLS)/lib) -lmbedtls -lmbedx509 -lmbedcrypto
$(OUT)/q2scrobd: q2scrobd.c | $(OUT)
	$(CC) -std=c11 -Wall -Wextra $(CFLAGS) $(if $(SQLITE_INC),-isystem $(SQLITE_INC)) $(if $(MBEDTLS),-isystem $(MBEDTLS)/include) -o $@ q2scrobd.c $(LDFLAGS) $(SQLITE_LIB) $(TLS_LIB)
$(OUT):
	mkdir -p $@
