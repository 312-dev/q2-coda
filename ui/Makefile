# Built by tools/ui.py, which passes the compiler, stb_truetype's folder, SQLite's header and library:
#   make OUT=build/host CC=cc STB=DIR STBI=DIR SQLITE_INC=DIR SQLITE_LIB=/usr/lib/<arch>-linux-gnu/libsqlite3.so.0
#   make OUT=build/mips CC="mips-mti-linux-gnu-gcc -EL ..." STB=DIR STBI=DIR SQLITE_INC=DIR SQLITE_LIB=ROOTFS/usr/lib/libsqlite3.so.0.8.6
OUT ?= build/host
STRIP ?= strip
SRC := main.c ui.c anim.c fb.c draw.c text.c image.c input.c theme.c status.c lib.c player.c now.c list.c card.c page_songs.c page_library.c page_home.c page_now.c page_depth.c page_gallery.c \
       ../patch/wheel_accel.c
OBJ := $(patsubst %.c,$(OUT)/%.o,$(notdir $(SRC)))
CFLAGS := -std=c11 -D_GNU_SOURCE -O2 -g -Wall -Wextra -ffunction-sections -fdata-sections -isystem $(STB) -isystem $(STBI) -isystem $(SQLITE_INC)
# SQLite linked by file: the root filesystem has the library and no header, and no libsqlite3.so link for -l
LIBS := $(SQLITE_LIB) -Wl,--allow-shlib-undefined -lm

$(OUT)/q2ui: $(OBJ)
	$(CC) -o $@.debug $(OBJ) -Wl,--gc-sections $(LIBS)
	$(STRIP) -o $@ $@.debug

# the toolkit's own checks (test_engine.c): everything but main.c, with stdarg for its printer
$(OUT)/q2ui-test: $(filter-out $(OUT)/main.o,$(OBJ)) $(OUT)/test_engine.o
	$(CC) -o $@ $^ -Wl,--gc-sections $(LIBS)

$(OUT)/%.o: %.c q2ui.h px.h bluenoise.inc ../patch/tokens.h | $(OUT)
	$(CC) $(CFLAGS) -c $< -o $@

$(OUT)/wheel_accel.o: ../patch/wheel_accel.c ../patch/wheel_accel.h | $(OUT)
	$(CC) $(CFLAGS) -c $< -o $@

$(OUT)/list.o: ../patch/wheel_accel.h

$(OUT)/player.o: ../patch/coda_rc_cmdq.h

$(OUT):
	mkdir -p $@
