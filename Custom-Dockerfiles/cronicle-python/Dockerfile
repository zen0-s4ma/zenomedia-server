# syntax=docker/dockerfile:1

FROM soulteary/cronicle:0.9.80

# Community repo (para algunos paquetes)
RUN set -eux; \
  ALP_VER="$(cut -d. -f1,2 /etc/alpine-release)"; \
  grep -q "v${ALP_VER}/community" /etc/apk/repositories || \
    echo "https://dl-cdn.alpinelinux.org/alpine/v${ALP_VER}/community" >> /etc/apk/repositories; \
  apk update

# Tools + Python + ffmpeg + Rust + helpers
RUN set -eux; \
  apk add --no-cache \
    bash ca-certificates curl jq coreutils tzdata dos2unix \
    python3 py3-pip py3-virtualenv py3-setuptools py3-wheel \
    ffmpeg docker-cli-compose docker-cli \
    rust cargo \
    icu-libs krb5-libs libgcc libstdc++ zlib ncurses-terminfo-base less \
  ; \
  update-ca-certificates

# PowerShell (opcional pero tú lo estabas usando)
ARG POWERSHELL_VERSION=7.5.4
RUN set -eux; \
  mkdir -p /opt/microsoft/powershell/7; \
  curl -fsSL -o /tmp/powershell.tgz \
    "https://github.com/PowerShell/PowerShell/releases/download/v${POWERSHELL_VERSION}/powershell-${POWERSHELL_VERSION}-linux-musl-x64.tar.gz"; \
  tar -xzf /tmp/powershell.tgz -C /opt/microsoft/powershell/7; \
  ln -sf /opt/microsoft/powershell/7/pwsh /usr/bin/pwsh; \
  chmod +x /opt/microsoft/powershell/7/pwsh; \
  rm -f /tmp/powershell.tgz

# Runners (copiados desde ESTA carpeta, porque el build context es ./Custom-Dockerfiles/cronicle-python)
COPY ./run-python /usr/local/bin/run-python
COPY ./run-rust   /usr/local/bin/run-rust
COPY ./run-shell  /usr/local/bin/run-shell
COPY ./run-ps1    /usr/local/bin/run-ps1

RUN set -eux; \
  dos2unix -q /usr/local/bin/run-python /usr/local/bin/run-rust /usr/local/bin/run-shell /usr/local/bin/run-ps1 2>/dev/null || true; \
  chmod +x /usr/local/bin/run-python /usr/local/bin/run-rust /usr/local/bin/run-shell /usr/local/bin/run-ps1

# Sanity info (no rompe si algo no existe)
RUN set -eux; \
  python3 --version; \
  ffprobe -version | head -n 1 || true; \
  rustc --version || true; \
  pwsh -v || true
