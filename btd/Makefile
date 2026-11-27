# q2btd (docs/btd.md). Built by Buildroot for the image (os/package/q2btd) and by test/btd.py for this machine.
OUT ?= build/host
CFLAGS ?= -O2
PKG_CONFIG ?= pkg-config
DBUS_CFLAGS ?= $(shell $(PKG_CONFIG) --cflags dbus-1)
DBUS_LIBS ?= $(shell $(PKG_CONFIG) --libs dbus-1)
$(OUT)/q2btd: q2btd.c | $(OUT)
	$(CC) -std=c11 -Wall -Wextra $(CFLAGS) $(DBUS_CFLAGS) -o $@ q2btd.c $(LDFLAGS) $(DBUS_LIBS)
$(OUT):
	mkdir -p $@
