################################################################################
#
# q2adb: patch/adb_gadget.sh, patch/adb_wifi.sh and q2adb-mdns from the commit in $(VAMP_SRC)/vamp
#
################################################################################

Q2ADB_VERSION = $(shell cat $(VAMP_SRC)/vamp/COMMIT 2>/dev/null)
Q2ADB_SITE = $(VAMP_SRC)/vamp
Q2ADB_SITE_METHOD = local
Q2ADB_LICENSE = GPL-3.0+
Q2ADB_LICENSE_FILES = LICENSE

# tools/build.py's ADB_MDNS_SOURCES, built against this image's libc (coda_rc_sys.h's CODA_HOST: libc's own headers)
define Q2ADB_BUILD_CMDS
	$(TARGET_CC) $(TARGET_CFLAGS) $(TARGET_LDFLAGS) -DCODA_HOST -Os -ffunction-sections -fdata-sections -Wl,--gc-sections \
		-o $(@D)/q2adb-mdns $(foreach s,$(shell python3 $(BR2_EXTERNAL_VAMP_PATH)/support/pins.py adb-mdns-sources),$(@D)/patch/$(s).c)
endef

define Q2ADB_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/patch/adb_gadget.sh $(TARGET_DIR)/usr/bin/q2adb-gadget
	$(INSTALL) -D -m 0755 $(@D)/patch/adb_wifi.sh $(TARGET_DIR)/usr/bin/q2adb-wifi
	$(INSTALL) -D -m 0755 $(@D)/q2adb-mdns $(TARGET_DIR)/usr/bin/q2adb-mdns
endef

$(eval $(generic-package))
