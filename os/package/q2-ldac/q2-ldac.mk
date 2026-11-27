################################################################################
#
# q2-ldac: tools/thirdparty.py's ldac (SOURCES["ldac"], build_ldac), run by os/support/pins.py
#
################################################################################

Q2_LDAC_VERSION = 2.0.2.3
Q2_LDAC_SITE = https://github.com/EHfive/ldacBT/releases/download/v$(Q2_LDAC_VERSION)
Q2_LDAC_SOURCE = ldacBT-$(Q2_LDAC_VERSION).tar.gz
Q2_LDAC_LICENSE = Apache-2.0
Q2_LDAC_LICENSE_FILES = libldac/LICENSE libldac/NOTICE
Q2_LDAC_INSTALL_STAGING = YES
Q2_LDAC_INSTALL_TARGET = NO

define Q2_LDAC_BUILD_CMDS
	python3 $(BR2_EXTERNAL_VAMP_PATH)/support/pins.py ldac $(@D) "$(TARGET_CC) $(TARGET_CFLAGS)" "$(TARGET_AR)"
endef

define Q2_LDAC_INSTALL_STAGING_CMDS
	cp -r $(@D)/stage/usr/include $(@D)/stage/usr/lib $(STAGING_DIR)/usr/
	sed -i 's|^prefix=.*|prefix=/usr|' $(STAGING_DIR)/usr/lib/pkgconfig/ldacBT-enc.pc $(STAGING_DIR)/usr/lib/pkgconfig/ldacBT-abr.pc
endef

$(eval $(generic-package))
