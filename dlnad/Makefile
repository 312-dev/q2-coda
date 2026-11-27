# q2dlnad (docs/dlnad.md). Built by Buildroot for the image (os/package/q2dlnad) and by test/dlnad.py for this machine.
OUT ?= build/host
CFLAGS ?= -O2
$(OUT)/q2dlnad: q2dlnad.c | $(OUT)
	$(CC) -std=c11 -Wall -Wextra $(CFLAGS) -o $@ q2dlnad.c $(LDFLAGS)
$(OUT):
	mkdir -p $@
