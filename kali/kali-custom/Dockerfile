# Usa la imagen oficial de Kali Linux como base
FROM kalilinux/kali-rolling

ENV DEBIAN_FRONTEND=noninteractive

RUN echo "-- INICIANDO DOCKERFILE DE KALI --"

# Agrega el root
RUN echo "root:root" | chpasswd

# Actualiza e instala paquetes básicos
RUN apt update && apt install -y kali-linux-core nano fastfetch curl openssh-server zsh nvidia-driver nvidia-smi nvidia-cuda-toolkit firefox-esr && apt-get autoremove -y && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Configura el servidor SSH para permitir root y autenticación por contraseña
RUN sed -i 's/^#*PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
RUN sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config

# Se cambia la shell a ZSH
RUN chsh -s /usr/bin/zsh root

# Se monta el volumen cgroup para el systemd
VOLUME [ "/sys/fs/cgroup" ]

# Exponer puertos
# 22 -> SSH
# 80 -> HTTP
# 443 -> HTTPS
EXPOSE 22 80 443

# Se arranca el ZSH
CMD ["zsh"]

# Mensaje de confirmación
RUN echo "-- DOCKERFILE DE KALI EJECUTADO CORRECTAMENTE --"

