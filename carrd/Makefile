# q2carrd and the coda-rc it runs (docs/carrd.md). Built by Buildroot for the image (os/package/q2carrd, which passes
# CODA_RC_SOURCES from tools/build.py) and by test/carrd.py for this machine. coda-rc is today's (patch/coda_rc.c and
# the rest, docs/remote-protocol.md), built with this libc's own headers (coda_rc_sys.h's CODA_LIBC) against SQLite.
OUT ?= build/host
CFLAGS ?= -O2
PATCH ?= ../patch
CODA_RC_SOURCES ?= coda_rc coda_rc_core coda_rc_lib coda_rc_ble coda_rc_mdns monocypher/monocypher monocypher/monocypher-ed25519
$(OUT)/q2carrd: q2carrd.c | $(OUT)
	$(CC) -std=c11 -Wall -Wextra $(CFLAGS) -o $@ q2carrd.c $(LDFLAGS)
$(OUT)/coda-rc: $(foreach s,$(CODA_RC_SOURCES),$(PATCH)/$(s).c) | $(OUT)
	$(CC) -DCODA_LIBC -Wall -Wno-unused-parameter $(CFLAGS) -I$(PATCH) -o $@ $(foreach s,$(CODA_RC_SOURCES),$(PATCH)/$(s).c) \
		$(LDFLAGS) -lsqlite3
$(OUT):
	mkdir -p $@
