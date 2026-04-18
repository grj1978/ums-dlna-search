FROM ubuntu:22.04

LABEL org.opencontainers.image.title="Universal Media Server (ums-dlna-search)"

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \
    openjdk-17-jre-headless \
    mediainfo \
    ffmpeg \
    fonts-dejavu \
    python3 \
    python3-pip \
    dnsutils \
    && pip3 install mutagen \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /ums

# Build context must be the ums-dlna-search project root.
# Run `mvn clean package -Dmaven.test.skip=true` before building.
# The Dockerfile itself lives at src/main/external-resources/docker/Dockerfile.
COPY target/ums.jar search.py index_media.py ./
COPY src/main/external-resources/web/ ./web/
COPY src/main/external-resources/docker/seed/ ./seed/
COPY src/main/external-resources/docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

VOLUME /media
VOLUME /profile

# Informational — all ports are exposed automatically in host network mode
EXPOSE 1900/udp 5001/tcp 5353/udp 9001/tcp 9002/tcp

ENV UMS_PROFILE=/profile
ENV JDK_JAVA_OPTIONS="-Dfile.encoding=UTF-8 -Djava.net.preferIPv4Stack=true -Djna.nosys=true"

ENTRYPOINT ["/entrypoint.sh"]
