################################################################################
#
# q2pkgd: pkgd/ from the commit tools/vampos.py exported into $(VAMP_SRC)/vamp, with the release key's public half
#
################################################################################

Q2PKGD_VERSION = $(shell cat $(VAMP_SRC)/vamp/COMMIT 2>/dev/null)
Q2PKGD_SITE = $(VAMP_SRC)/vamp
Q2PKGD_SITE_METHOD = local
Q2PKGD_LICENSE = GPL-3.0+, BSD-2-Clause or CC0-1.0 (Monocypher)
Q2PKGD_LICENSE_FILES = LICENSE patch/monocypher/LICENCE.md
Q2PKGD_DEPENDENCIES = q2-mbedtls

define Q2PKGD_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D)/pkgd OUT=$(@D)/out CC="$(TARGET_CC)" CFLAGS="$(TARGET_CFLAGS)" \
		LDFLAGS="$(TARGET_LDFLAGS)" KEY='$(shell python3 $(BR2_EXTERNAL_VAMP_PATH)/support/pins.py release-key)' $(@D)/out/q2pkgd
endef

define Q2PKGD_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/out/q2pkgd $(TARGET_DIR)/usr/bin/q2pkgd
endef

$(eval $(generic-package))
