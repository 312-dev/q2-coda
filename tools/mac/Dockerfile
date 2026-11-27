# syntax=docker/dockerfile:1.7
# The Linux build and test environment on an Apple Silicon Mac, native arm64 (docs/building.md#building-on-macos).
# Ubuntu 24.04, as GRAYTHINKPAD runs, so clang, lld, squashfs-tools, ImageMagick, qemu-user and bwrap
# are the versions the laptop's builds use. Two kinds of x86 program still run here, each by the
# Colima VM's binfmt handlers (both registered with F, so they work inside a container):
# - x86-64 under Rosetta: the Codescape MIPS toolchain (tools/thirdparty.py), whose strip every build
#   runs on adbd; it needs only the amd64 libc, installed below as a foreign architecture;
# - i386 under qemu-i386: the -m32 test programs (test/coverflow.py, test/peq.py's audio writer),
#   built by i686-linux-gnu-gcc through tools/mac/cc and run against the i386 libc.
FROM --platform=linux/arm64 ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN sed -i 's/^Components:.*/&\nArchitectures: arm64/' /etc/apt/sources.list.d/ubuntu.sources \
    && printf '%s\n' 'Types: deb' 'URIs: http://archive.ubuntu.com/ubuntu/' \
        'Suites: noble noble-updates noble-security' 'Components: main universe' 'Architectures: amd64 i386' \
        'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg' > /etc/apt/sources.list.d/foreign.sources \
    && dpkg --add-architecture amd64 && dpkg --add-architecture i386 \
    && apt-get update && apt-get install -y --no-install-recommends \
        autoconf automake bc build-essential bubblewrap bzip2 ca-certificates ccache clang cmake \
        dbus file gcc-i686-linux-gnu libc6-dev-i386-cross git golang-go imagemagick jq lame ffmpeg \
        libavcodec-dev libavformat-dev libavutil-dev libcapstone-dev libcurl4-openssl-dev libdbus-1-dev \
        libglib2.0-dev libicu-dev libmad0-dev libssl-dev libtool libusb-1.0-0-dev lld llvm ninja-build \
        nodejs npm patchelf pkg-config qemu-user squashfs-tools unzip wget xz-utils zip \
        libc6:amd64 libc6:i386 \
    && rm -rf /var/lib/apt/lists/*

# test/fw needs a PIE python (CLAUDE.md, Traps). The build actions/setup-python installs, arm64, at the
# prefix its rpath names.
ADD --checksum=sha256:86c16c393f74e4d814f9606da32aed57be85a951e772bf1c7eb80a48ef3cb664 \
    https://github.com/actions/python-versions/releases/download/3.12.16-40260402378/python-3.12.16-linux-24.04-arm64.tar.gz \
    /tmp/py.tgz
ARG PY_DIR=/opt/hostedtoolcache/Python/3.12.16/arm64
RUN mkdir -p "$PY_DIR" && tar -C "$PY_DIR" -xzf /tmp/py.tgz && rm /tmp/py.tgz \
    && cd "$PY_DIR/bin" && ln -sf python3.12 python3 && ln -sf python3 python

COPY requirements.txt unit-requirements.txt /tmp/
RUN "$PY_DIR/bin/python3" -m venv /opt/q2venv \
    && /opt/q2venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt -r /tmp/unit-requirements.txt \
        pytest-xdist==3.8.0 mypy==2.4.1 \
    && rm /tmp/requirements.txt /tmp/unit-requirements.txt
ENV PATH=/opt/q2venv/bin:$PATH

# cc -m32 is i686-linux-gnu-gcc (tools/mac/cc); any other cc is the system's.
COPY cc /usr/local/bin/cc

# The Mac user's uid and home, so files written through the /Users mount keep their owner and every
# ~/... path is the same inside and out (tools/mac/q2env passes --user and HOME).
ARG UID=501
ARG GID=20
ARG USER_HOME=/Users/grayson
RUN (getent group "$GID" || groupadd -g "$GID" mac) \
    && useradd -M -d "$USER_HOME" -u "$UID" -g "$GID" -s /bin/bash mac
