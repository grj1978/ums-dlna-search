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

# Build context is the ums-dlna-search project root (run `mvn clean package -Dmaven.test.skip=true` first).
# seed/ and entrypoint.sh come from the `dockerconfig` additional context (host_service/ums/).
COPY target/ums.jar search.py index_media.py dlna_tools.py browse.py ./
COPY src/main/external-resources/web/ ./web/
COPY src/main/external-resources/docker/seed/ ./seed/
COPY src/main/external-resources/docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

VOLUME /media
VOLUME /profile

# Informational — all ports are exposed automatically in host network mode
EXPOSE 1900/udp 5001/tcp 5353/udp 9001/tcp 9002/tcp

ENV UMS_PROFILE=/profile
# C.UTF-8 sets sun.jnu.encoding=UTF-8 so Java's native FS calls handle non-ASCII filenames.
# -Dsun.jnu.encoding=UTF-8 is silently ignored by Java 17+ on Linux; the locale is the real fix.
ENV LANG=C.UTF-8
ENV JDK_JAVA_OPTIONS="-Dfile.encoding=UTF-8 -Djava.net.preferIPv4Stack=true -Djna.nosys=true"

ENTRYPOINT ["/entrypoint.sh"]
