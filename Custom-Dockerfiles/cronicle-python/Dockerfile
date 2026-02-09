ARG CRONICLE_TAG=0.9.80
FROM soulteary/cronicle:${CRONICLE_TAG}

SHELL ["/bin/sh", "-lc"]

ARG WITH_RUST=true
ARG WITH_POWERSHELL=true
ARG PWSH_VERSION=7.5.4

# Asegurar repo community (ffmpeg/rust suelen estar aquÃ­)
RUN ALP_VER="$(cut -d. -f1,2 /etc/alpine-release)" \
 && grep -q "/community" /etc/apk/repositories || echo "https://dl-cdn.alpinelinux.org/alpine/v${ALP_VER}/community" >> /etc/apk/repositories \
 && apk update

# Base tools + Python + ffmpeg/ffprobe
RUN apk add --no-cache \
      bash ca-certificates curl jq coreutils tzdata \
      python3 py3-pip py3-virtualenv \
      ffmpeg \
 && python3 -m pip install --no-cache-dir --upgrade pip setuptools wheel

# Rust toolchain + build essentials
RUN if [ "${WITH_RUST}" = "true" ]; then \
      apk add --no-cache \
        rust cargo \
        build-base musl-dev \
        openssl-dev pkgconfig \
        libffi-dev ; \
    fi

# PowerShell en Alpine (tar.gz musl)
RUN if [ "${WITH_POWERSHELL}" = "true" ]; then \
      apk add --no-cache \
        ca-certificates less ncurses-terminfo-base krb5-libs libgcc libintl libssl3 libstdc++ tzdata userspace-rcu zlib icu-libs curl ; \
      apk -X https://dl-cdn.alpinelinux.org/alpine/edge/main add --no-cache lttng-ust openssh-client || true ; \
      mkdir -p /opt/microsoft/powershell/7 ; \
      curl -L "https://github.com/PowerShell/PowerShell/releases/download/v${PWSH_VERSION}/powershell-${PWSH_VERSION}-linux-musl-x64.tar.gz" -o /tmp/powershell.tar.gz ; \
      tar zxf /tmp/powershell.tar.gz -C /opt/microsoft/powershell/7 ; \
      chmod +x /opt/microsoft/powershell/7/pwsh ; \
      ln -sf /opt/microsoft/powershell/7/pwsh /usr/bin/pwsh ; \
      rm -f /tmp/powershell.tar.gz ; \
    fi

# Runners
COPY Custom-Dockerfiles/cronicle-python/run-python /usr/local/bin/run-python
COPY Custom-Dockerfiles/cronicle-python/run-rust   /usr/local/bin/run-rust
COPY Custom-Dockerfiles/cronicle-python/run-shell  /usr/local/bin/run-shell
COPY Custom-Dockerfiles/cronicle-python/run-ps1    /usr/local/bin/run-ps1

RUN chmod +x /usr/local/bin/run-python \
             /usr/local/bin/run-rust \
             /usr/local/bin/run-shell \
             /usr/local/bin/run-ps1 \
 && mkdir -p /scripts /artifacts /opt/cronicle/data/python /opt/cronicle/data/rust