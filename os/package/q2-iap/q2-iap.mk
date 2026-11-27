################################################################################
#
# q2-iap: Car Audio's daemon and gadget (patch/q2_iap.c and its iap2_*.c, patch/q2car.sh) from the commit
# tools/vampos.py exported into $(VAMP_SRC)/vamp, and tools/uac1.py's pinned usb_f_uac1.ko
#
################################################################################

Q2_IAP_VERSION = $(shell cat $(VAMP_SRC)/vamp/COMMIT 2>/dev/null)
Q2_IAP_SITE = $(VAMP_SRC)/vamp
Q2_IAP_SITE_METHOD = local
Q2_IAP_LICENSE = GPL-3.0+
Q2_IAP_LICENSE_FILES = LICENSE
Q2_IAP_DEPENDENCIES = sqlite
Q2_IAP_SOURCES = q2_iap iap2_link iap2_names iap2_hid iap2_msg iap2_xfer iap2_library iap2_nowplaying iap2_session q2_iap_lib

define Q2_IAP_BUILD_CMDS
	$(TARGET_CC) $(TARGET_CFLAGS) -std=gnu11 -Wall -o $(@D)/q2-iap $(foreach s,$(Q2_IAP_SOURCES),$(@D)/patch/$(s).c) \
		$(TARGET_LDFLAGS) -lsqlite3
endef

define Q2_IAP_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/q2-iap $(TARGET_DIR)/usr/bin/q2-iap
	$(INSTALL) -D -m 0755 $(@D)/patch/q2car.sh $(TARGET_DIR)/usr/libexec/vamp/q2car
	$(INSTALL) -D -m 0644 $(shell python3 $(@D)/os/support/pins.py uac1) $(TARGET_DIR)/usr/lib/vamp/usb_f_uac1.ko
endef

$(eval $(generic-package))
