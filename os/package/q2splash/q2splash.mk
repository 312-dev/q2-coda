################################################################################
#
# q2splash: patch/splash.c and assets/boot-anim.q2a from the commit tools/vampos.py exported into $(VAMP_SRC)/vamp
#
################################################################################

Q2SPLASH_VERSION = $(shell cat $(VAMP_SRC)/vamp/COMMIT 2>/dev/null)
Q2SPLASH_SITE = $(VAMP_SRC)/vamp
Q2SPLASH_SITE_METHOD = local
Q2SPLASH_LICENSE = GPL-3.0+
Q2SPLASH_LICENSE_FILES = LICENSE

# splash.c declares the few libc calls it makes itself (it is built for today's firmware with no MIPS sysroot), so
# no header is included and those are glibc's long-standing 32-bit time and offset symbols, which this glibc keeps
define Q2SPLASH_BUILD_CMDS
	$(TARGET_CC) -Wall $(TARGET_CFLAGS) -o $(@D)/q2splash $(@D)/patch/splash.c $(TARGET_LDFLAGS)
endef

define Q2SPLASH_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/q2splash $(TARGET_DIR)/usr/bin/q2splash
	$(INSTALL) -D -m 0644 $(@D)/assets/boot-anim.q2a $(TARGET_DIR)/usr/share/vamp/boot-anim.q2a
endef

$(eval $(generic-package))
