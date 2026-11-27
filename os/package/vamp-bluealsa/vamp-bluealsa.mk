################################################################################
#
# vamp-bluealsa: tools/thirdparty.py's bluealsa (SOURCES["bluealsa"], BLUEALSA_OPTIONS, BLUEALSA_PATCHES) for a
# base that is not stock's (docs/bluealsa.md#bases): this image's libraries through pkg-config, nothing of stock's.
# The same source as an --own-bluealsa build's; --shanling is compiled in and left off at run time.
# test/os_image.py checks the pins agree.
#
################################################################################

VAMP_BLUEALSA_VERSION = 5.0.0
VAMP_BLUEALSA_SITE = https://github.com/arkq/bluez-alsa/archive/v$(VAMP_BLUEALSA_VERSION)
VAMP_BLUEALSA_SOURCE = bluez-alsa-$(VAMP_BLUEALSA_VERSION).tar.gz
VAMP_BLUEALSA_LICENSE = MIT
VAMP_BLUEALSA_LICENSE_FILES = LICENSE
VAMP_BLUEALSA_AUTORECONF = YES
VAMP_BLUEALSA_DEPENDENCIES = alsa-lib vamp-bluez dbus libglib2 sbc q2-freeaptx q2-ldac host-pkgconf host-libglib2
VAMP_BLUEALSA_CONF_OPTS = $(filter-out $(if $(BR2_PACKAGE_VAMP_BLUEALSA_AAC),,--enable-aac),\
	$(shell python3 $(BR2_EXTERNAL_VAMP_PATH)/support/pins.py bluealsa-options))
ifeq ($(BR2_PACKAGE_VAMP_BLUEALSA_AAC),y)
VAMP_BLUEALSA_DEPENDENCIES += fdk-aac
endif

define VAMP_BLUEALSA_VAMP_PATCHES
	python3 $(BR2_EXTERNAL_VAMP_PATH)/support/pins.py bluealsa-patch $(@D)
endef
VAMP_BLUEALSA_POST_PATCH_HOOKS += VAMP_BLUEALSA_VAMP_PATCHES

# src/Makefile.am ends AM_CFLAGS with -static, which plain libtool takes, at the daemon's link, as "the uninstalled
# libtool libraries statically"; Buildroot's libtool makes -static a fully static link, which this image's shared
# glibc, D-Bus and GLib refuse ("attempted static link of dynamic object"). The daemon's own convenience library is
# static either way.
define VAMP_BLUEALSA_NOT_STATIC
	sed -i '/^AM_CFLAGS = /,/^$$/{/^\t-static$$/d}' $(@D)/src/Makefile.am
	! grep -q '^[[:space:]]-static$$' $(@D)/src/Makefile.am
endef
VAMP_BLUEALSA_POST_PATCH_HOOKS += VAMP_BLUEALSA_NOT_STATIC

# upstream names its daemon bluealsad; the image's services, like today's Q2 and its callers, call it bluealsa
define VAMP_BLUEALSA_NAME
	mv $(TARGET_DIR)/usr/bin/bluealsad $(TARGET_DIR)/usr/bin/bluealsa
endef
VAMP_BLUEALSA_POST_INSTALL_TARGET_HOOKS += VAMP_BLUEALSA_NAME

$(eval $(autotools-package))
