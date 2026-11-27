# q2scrobd (docs/vamp-os-services.md#scrobbling). Built by Buildroot for the image (os/package/q2scrobd) and by
# test/scrobd.py for this machine.
OUT ?= build/host
CFLAGS ?= -O2
SQLITE_LIB ?= -lsqlite3
$(OUT)/q2scrobd: q2scrobd.c | $(OUT)
	$(CC) -std=c11 -Wall -Wextra $(CFLAGS) $(if $(SQLITE_INC),-isystem $(SQLITE_INC)) -o $@ q2scrobd.c $(LDFLAGS) $(SQLITE_LIB)
$(OUT):
	mkdir -p $@
