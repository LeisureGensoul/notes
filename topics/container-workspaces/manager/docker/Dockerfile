FROM ubuntu:24.04

ARG UBUNTU_MIRROR
RUN if [ -n "$UBUNTU_MIRROR" ]; then \
        sed -i "s|^URIs: .*|URIs: $UBUNTU_MIRROR|" /etc/apt/sources.list.d/ubuntu.sources; \
    fi

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    adb bison build-essential ca-certificates curl flex fontconfig git gnupg \
    libc6-dev-i386 lib32z1-dev libgl1-mesa-dev libx11-dev libxml2-utils \
    openssh-server python3 repo rsync unzip x11proto-core-dev xsltproc zip zlib1g-dev \
    && rm -f /etc/ssh/ssh_host_* \
    && rm -rf /var/lib/apt/lists/*

COPY --chmod=755 start-sshd.sh /usr/local/bin/local-manager-sshd
ENV LANG=C.UTF-8
