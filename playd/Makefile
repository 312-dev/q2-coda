# q2playd (docs/playd.md). Built by Buildroot for the image (os/package/q2playd), and by test/playd.py for this
# machine against its own SQLite library and the header tools/thirdparty.py keeps, as tools/ui.py builds q2ui:
#   make OUT=DIR CC=cc SQLITE_INC=DIR SQLITE_LIB=/usr/lib/<arch>-linux-gnu/libsqlite3.so.0
OUT ?= build/host
CFLAGS ?= -O2
SQLITE_LIB ?= -lsqlite3
# patch/peq.c, hash.c and peq_level.c: the EQ state as demo's editor keeps it, built as the player builds peq.c
# (PEQ_HOST: libc calls; the Q2's own paths, which --root prefixes in q2playd's calls)
PEQ := ../patch/peq.c ../patch/hash.c ../patch/peq_level.c
$(OUT)/q2playd: q2playd.c ../perm/q2perm.h ../patch/peq_types.h ../patch/peq.h $(PEQ) | $(OUT)
	$(CC) -std=c11 -Wall -Wextra -I../perm -I../patch $(CFLAGS) $(if $(SQLITE_INC),-isystem $(SQLITE_INC)) -o $@ q2playd.c \
		-DPEQ_HOST -DPEQ_ROOT='""' $(PEQ) $(LDFLAGS) $(SQLITE_LIB) -lm
$(OUT):
	mkdir -p $@
