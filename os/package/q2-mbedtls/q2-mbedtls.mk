################################################################################
#
# q2-mbedtls: the release and settings of tools/thirdparty.py's mbedTLS (test/os_image.py checks the two agree)
#
################################################################################

Q2_MBEDTLS_VERSION = 3.6.8
Q2_MBEDTLS_SITE = https://github.com/Mbed-TLS/mbedtls/releases/download/mbedtls-$(Q2_MBEDTLS_VERSION)
Q2_MBEDTLS_SOURCE = mbedtls-$(Q2_MBEDTLS_VERSION).tar.bz2
Q2_MBEDTLS_LICENSE = Apache-2.0 or GPL-2.0+
Q2_MBEDTLS_LICENSE_FILES = LICENSE
Q2_MBEDTLS_INSTALL_STAGING = YES
Q2_MBEDTLS_INSTALL_TARGET = NO

define Q2_MBEDTLS_CONFIGURE_CMDS
	for option in $$(python3 $(BR2_EXTERNAL_VAMP_PATH)/support/pins.py mbedtls-unset); do \
		(cd $(@D) && python3 scripts/config.py unset $$option) || exit 1; \
	done
endef

define Q2_MBEDTLS_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D)/library CC="$(TARGET_CC)" AR="$(TARGET_AR)" \
		CFLAGS="$(TARGET_CFLAGS) -fPIC -ffunction-sections -fdata-sections" static
endef

define Q2_MBEDTLS_INSTALL_STAGING_CMDS
	mkdir -p $(STAGING_DIR)/usr/include $(STAGING_DIR)/usr/lib
	cp -r $(@D)/include/mbedtls $(@D)/include/psa $(STAGING_DIR)/usr/include/
	$(INSTALL) -m 0644 $(@D)/library/libmbedtls.a $(@D)/library/libmbedx509.a \
		$(@D)/library/libmbedcrypto.a $(STAGING_DIR)/usr/lib/
endef

$(eval $(generic-package))
