# q2netd (docs/netd.md). Built by Buildroot for the image (os/package/q2netd) and by test/netd.py for this machine.
OUT ?= build/host
CFLAGS ?= -O2
$(OUT)/q2netd: q2netd.c ../perm/q2perm.h | $(OUT)
	$(CC) -std=c11 -Wall -Wextra -I../perm $(CFLAGS) -o $@ q2netd.c $(LDFLAGS)
$(OUT):
	mkdir -p $@
