################################################################################
#
# q2btd: btd/ from the commit tools/vampos.py exported into $(VAMP_SRC)/vamp
#
################################################################################

Q2BTD_VERSION = $(shell cat $(VAMP_SRC)/vamp/COMMIT 2>/dev/null)
Q2BTD_SITE = $(VAMP_SRC)/vamp
Q2BTD_SITE_METHOD = local
Q2BTD_LICENSE = GPL-3.0+
Q2BTD_LICENSE_FILES = LICENSE
Q2BTD_DEPENDENCIES = dbus host-pkgconf

define Q2BTD_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D)/btd OUT=$(@D)/out CC="$(TARGET_CC)" CFLAGS="$(TARGET_CFLAGS)" \
		LDFLAGS="$(TARGET_LDFLAGS)" PKG_CONFIG="$(PKG_CONFIG_HOST_BINARY)" $(@D)/out/q2btd
endef

define Q2BTD_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/out/q2btd $(TARGET_DIR)/usr/bin/q2btd
endef

$(eval $(generic-package))
