################################################################################
#
# q2carrd: carrd/ and patch/'s coda-rc from the commit tools/vampos.py exported into $(VAMP_SRC)/vamp
#
################################################################################

Q2CARRD_VERSION = $(shell cat $(VAMP_SRC)/vamp/COMMIT 2>/dev/null)
Q2CARRD_SITE = $(VAMP_SRC)/vamp
Q2CARRD_SITE_METHOD = local
Q2CARRD_LICENSE = GPL-3.0+
Q2CARRD_LICENSE_FILES = LICENSE
Q2CARRD_DEPENDENCIES = sqlite

# coda-rc is tools/build.py's CODA_RC_SOURCES built with this libc's headers (coda_rc_sys.h's CODA_LIBC)
define Q2CARRD_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D)/carrd OUT=$(@D)/out CC="$(TARGET_CC)" CFLAGS="$(TARGET_CFLAGS)" \
		LDFLAGS="$(TARGET_LDFLAGS)" CODA_RC_SOURCES="$(shell python3 $(BR2_EXTERNAL_VAMP_PATH)/support/pins.py coda-rc-sources)" \
		$(@D)/out/q2carrd $(@D)/out/coda-rc
endef

define Q2CARRD_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/out/q2carrd $(TARGET_DIR)/usr/bin/q2carrd
	$(INSTALL) -D -m 0755 $(@D)/out/coda-rc $(TARGET_DIR)/usr/bin/coda-rc
endef

$(eval $(generic-package))
