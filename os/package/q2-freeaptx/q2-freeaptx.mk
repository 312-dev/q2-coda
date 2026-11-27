################################################################################
#
# q2-freeaptx: tools/thirdparty.py's freeaptx (SOURCES["freeaptx"], build_freeaptx), run by os/support/pins.py
#
################################################################################

Q2_FREEAPTX_VERSION = 0.2.2
Q2_FREEAPTX_SITE = https://github.com/regularhunter/libfreeaptx/archive/$(Q2_FREEAPTX_VERSION)
Q2_FREEAPTX_SOURCE = libfreeaptx-$(Q2_FREEAPTX_VERSION).tar.gz
Q2_FREEAPTX_LICENSE = LGPL-2.1+
Q2_FREEAPTX_LICENSE_FILES = COPYING
Q2_FREEAPTX_INSTALL_STAGING = YES
Q2_FREEAPTX_INSTALL_TARGET = NO

define Q2_FREEAPTX_BUILD_CMDS
	python3 $(BR2_EXTERNAL_VAMP_PATH)/support/pins.py freeaptx $(@D) "$(TARGET_CC) $(TARGET_CFLAGS)" "$(TARGET_AR)"
endef

# the stage's pkg-config file names the stage as its prefix: the staging directory's own here
define Q2_FREEAPTX_INSTALL_STAGING_CMDS
	cp -r $(@D)/stage/usr/include $(@D)/stage/usr/lib $(STAGING_DIR)/usr/
	sed -i 's|^prefix=.*|prefix=/usr|' $(STAGING_DIR)/usr/lib/pkgconfig/libfreeaptx.pc
endef

$(eval $(generic-package))
