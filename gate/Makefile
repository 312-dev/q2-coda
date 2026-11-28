# q2gate (docs/gate.md). Built by Buildroot for the image (os/package/q2gate) and by test/gate.py for this machine.
OUT ?= build/host
CFLAGS ?= -O2
$(OUT)/q2gate: q2gate.c | $(OUT)
	$(CC) -std=gnu11 -Wall -Wextra $(CFLAGS) -o $@ q2gate.c -pthread $(LDFLAGS)
$(OUT):
	mkdir -p $@
