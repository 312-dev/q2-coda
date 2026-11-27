################################################################################
#
# q2netd: netd/ from the commit tools/vampos.py exported into $(VAMP_SRC)/vamp
#
################################################################################

Q2NETD_VERSION = $(shell cat $(VAMP_SRC)/vamp/COMMIT 2>/dev/null)
Q2NETD_SITE = $(VAMP_SRC)/vamp
Q2NETD_SITE_METHOD = local
Q2NETD_LICENSE = GPL-3.0+
Q2NETD_LICENSE_FILES = LICENSE

define Q2NETD_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D)/netd OUT=$(@D)/out CC="$(TARGET_CC)" CFLAGS="$(TARGET_CFLAGS)" \
		LDFLAGS="$(TARGET_LDFLAGS)" $(@D)/out/q2netd
endef

define Q2NETD_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/out/q2netd $(TARGET_DIR)/usr/bin/q2netd
endef

$(eval $(generic-package))
