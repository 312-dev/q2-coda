# q2sysd (docs/sysd.md). Built by Buildroot for the image (os/package/q2sysd) and by test/sysd.py for this machine.
OUT ?= build/host
CFLAGS ?= -O2
# The overheat guardian's decisions and the PMIC's reader are today's Vamp's (patch/q2guard_core.c, q2guard_pmic.c,
# card_store.c for the log ring they link to), built with the system's headers (Q2GUARD_HOST).
GUARD = ../patch/q2guard_core.c ../patch/q2guard_pmic.c ../patch/card_store.c
$(OUT)/q2sysd: q2sysd.c ../perm/q2perm.h ../patch/q2guard.h ../patch/findmy_tune.h $(GUARD) | $(OUT)
	$(CC) -std=gnu11 -Wall -Wextra -DQ2GUARD_HOST -I../perm -I../patch $(CFLAGS) -o $@ q2sysd.c $(GUARD) -pthread $(LDFLAGS)
# q2ask: a shell's one exchange with an API 1 socket (docs/vamp-os-base.md#first-card-boot)
$(OUT)/q2ask: q2ask.c | $(OUT)
	$(CC) -std=gnu11 -Wall -Wextra $(CFLAGS) -o $@ q2ask.c $(LDFLAGS)
$(OUT):
	mkdir -p $@
