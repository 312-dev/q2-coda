# q2sysd (docs/sysd.md). Built by Buildroot for the image (os/package/q2sysd) and by test/sysd.py for this machine.
OUT ?= build/host
CFLAGS ?= -O2
$(OUT)/q2sysd: q2sysd.c | $(OUT)
	$(CC) -std=c11 -Wall -Wextra $(CFLAGS) -o $@ q2sysd.c $(LDFLAGS)
$(OUT):
	mkdir -p $@
