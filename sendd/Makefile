# q2sendd and the q2send it runs (docs/vamp-os-services.md#localsend). Built by Buildroot for the image
# (os/package/q2sendd) and by test/sendd.py for this machine. q2send is today's (patch/localsend.c on
# patch/localsend_core.c, docs/localsend.md), built with real headers (LS_HOST) against the image's OpenSSL.
OUT ?= build/host
CFLAGS ?= -O2
PATCH ?= ../patch
$(OUT)/q2sendd: q2sendd.c | $(OUT)
	$(CC) -std=c11 -Wall -Wextra $(CFLAGS) -o $@ q2sendd.c $(LDFLAGS)
$(OUT)/q2send: $(PATCH)/localsend.c $(PATCH)/localsend_core.c $(PATCH)/localsend.h $(PATCH)/localsend_sys.h | $(OUT)
	$(CC) -DLS_HOST -Wall -Wextra -Wno-unused-parameter -Wno-deprecated-declarations $(CFLAGS) -I$(PATCH) -o $@ \
		$(PATCH)/localsend.c $(PATCH)/localsend_core.c $(LDFLAGS) -lssl -lcrypto -lpthread
$(OUT):
	mkdir -p $@
