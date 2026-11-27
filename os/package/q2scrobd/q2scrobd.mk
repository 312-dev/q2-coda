################################################################################
#
# q2scrobd: scrobd/ from the commit tools/vampos.py exported into $(VAMP_SRC)/vamp
#
################################################################################

Q2SCROBD_VERSION = $(shell cat $(VAMP_SRC)/vamp/COMMIT 2>/dev/null)
Q2SCROBD_SITE = $(VAMP_SRC)/vamp
Q2SCROBD_SITE_METHOD = local
Q2SCROBD_LICENSE = GPL-3.0+
Q2SCROBD_LICENSE_FILES = LICENSE
Q2SCROBD_DEPENDENCIES = sqlite

define Q2SCROBD_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D)/scrobd OUT=$(@D)/out CC="$(TARGET_CC)" CFLAGS="$(TARGET_CFLAGS)" \
		LDFLAGS="$(TARGET_LDFLAGS)" $(@D)/out/q2scrobd
endef

define Q2SCROBD_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/out/q2scrobd $(TARGET_DIR)/usr/bin/q2scrobd
endef

$(eval $(generic-package))
