################################################################################
#
# vamp-bluez: tools/thirdparty.py's bluez (SOURCES["bluez"], BLUEZ_OPTIONS, BLUEZ_STORAGE, tools/bluez/vamp.patch)
# for a base that is not stock's (docs/bluez.md#bases): no Shanling hooks, no libbtctl, and this image's own
# D-Bus and GLib through pkg-config. test/os_image.py checks the pins agree.
#
################################################################################

VAMP_BLUEZ_VERSION = 5.88
VAMP_BLUEZ_SITE = https://www.kernel.org/pub/linux/bluetooth
VAMP_BLUEZ_SOURCE = bluez-$(VAMP_BLUEZ_VERSION).tar.xz
VAMP_BLUEZ_LICENSE = GPL-2.0+, LGPL-2.1+
VAMP_BLUEZ_LICENSE_FILES = COPYING COPYING.LIB
VAMP_BLUEZ_DEPENDENCIES = dbus libglib2 host-pkgconf
VAMP_BLUEZ_INSTALL_STAGING = YES
# the base's own settings beside thirdparty's: libbluetooth too, for bluez-alsa (stock's rootfs has 5.66's)
VAMP_BLUEZ_CONF_OPTS = $(shell python3 $(BR2_EXTERNAL_VAMP_PATH)/support/pins.py bluez-options) --enable-library

define VAMP_BLUEZ_VAMP_PATCH
	python3 $(BR2_EXTERNAL_VAMP_PATH)/support/pins.py bluez-patch $(@D)
endef
VAMP_BLUEZ_POST_PATCH_HOOKS += VAMP_BLUEZ_VAMP_PATCH

define VAMP_BLUEZ_STORE
	python3 $(BR2_EXTERNAL_VAMP_PATH)/support/pins.py bluez-storage $(@D)
endef
VAMP_BLUEZ_POST_CONFIGURE_HOOKS += VAMP_BLUEZ_STORE

# bluetoothd, its D-Bus policy and libbluetooth; main.conf is the overlay's (os/board/q2/overlay/etc/bluetooth)
define VAMP_BLUEZ_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/src/bluetoothd $(TARGET_DIR)/usr/libexec/bluetooth/bluetoothd
	$(INSTALL) -D -m 0644 $(@D)/src/bluetooth.conf $(TARGET_DIR)/etc/dbus-1/system.d/bluetooth.conf
	cp -a $(@D)/lib/.libs/libbluetooth.so* $(TARGET_DIR)/usr/lib/
endef

$(eval $(autotools-package))
