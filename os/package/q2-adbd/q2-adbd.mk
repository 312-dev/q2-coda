################################################################################
#
# q2-adbd: tools/thirdparty.py's adbd (SOURCES["adbd"], ADBD_DEBIAN, ADBD_MINCRYPT, tools/adbd.patch), its own
# recipe run by os/support/pins.py on the files Buildroot fetched (test/os_image.py checks the pins agree)
#
################################################################################

Q2_ADBD_VERSION = 4.2.2+git20130218
Q2_ADBD_SITE = https://launchpad.net/ubuntu/+archive/primary/+files
Q2_ADBD_SOURCE = android-tools_$(Q2_ADBD_VERSION).orig.tar.xz
Q2_ADBD_MINCRYPT = https://raw.githubusercontent.com/aosp-mirror/platform_system_core/android-4.2.2_r1/libmincrypt
Q2_ADBD_EXTRA_DOWNLOADS = \
	https://launchpad.net/ubuntu/+archive/primary/+files/android-tools_4.2.2+git20130218-3ubuntu41.debian.tar.gz \
	$(Q2_ADBD_MINCRYPT)/rsa.c \
	$(Q2_ADBD_MINCRYPT)/rsa_e_3.c \
	$(Q2_ADBD_MINCRYPT)/rsa_e_f4.c \
	$(Q2_ADBD_MINCRYPT)/sha.c \
	$(Q2_ADBD_MINCRYPT)/NOTICE 
Q2_ADBD_LICENSE = Apache-2.0, BSD-3-Clause (libmincrypt)
Q2_ADBD_LICENSE_FILES = debian/copyright core/libmincrypt/NOTICE

define Q2_ADBD_BUILD_CMDS
	cd $(@D) && $(TARGET_MAKE_ENV) python3 $(BR2_EXTERNAL_VAMP_PATH)/support/pins.py adbd $(@D) $(Q2_ADBD_DL_DIR) \
		"$(TARGET_CC) $(TARGET_CFLAGS)"
endef

define Q2_ADBD_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/stage/usr/bin/adbd $(TARGET_DIR)/usr/bin/adbd
endef

$(eval $(generic-package))
