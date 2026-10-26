# Offline-staged experimental image; not an automatic production deployment.
# Based on soar999/qdc507-asterisk-gatewaY build/clccsafe4/Dockerfile.qdc507
FROM debian:trixie AS builder

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git cmake python3 \
    build-essential autoconf automake libtool pkg-config \
    binutils-dev libcurl4-openssl-dev libedit-dev libgsm1-dev \
    libicu-dev libjansson-dev libncurses-dev libogg-dev \
    libpopt-dev libpqxx-dev libresample1-dev libspandsp-dev \
    libspeex-dev libspeexdsp-dev libsqlite3-dev libssl-dev \
    libvorbis-dev libxml2-dev libxslt1-dev \
    odbcinst unixodbc unixodbc-dev uuid uuid-dev \
    xmlstarlet libsrtp2-dev portaudio19-dev libasound2-dev \
 && rm -rf /var/lib/apt/lists/*

# Identical Asterisk header-generation recipe as the accepted 8 kHz build.
WORKDIR /usr/src/asterisk
RUN curl -fsSL -o /tmp/asterisk.tar.gz \
    https://downloads.asterisk.org/pub/telephony/asterisk/releases/asterisk-22.10.1.tar.gz \
 && tar --strip-components=1 -xzf /tmp/asterisk.tar.gz \
 && rm /tmp/asterisk.tar.gz \
 && ./configure --with-pjproject-bundled --with-ssl=ssl --with-crypto \
 && make menuselect/menuselect menuselect-tree menuselect.makeopts \
 && menuselect/menuselect --disable BUILD_NATIVE menuselect.makeopts \
 && make include/asterisk/buildopts.h include/asterisk/build.h

# Exact historical upstream source, NEVER the moving master branch.
WORKDIR /usr/src
COPY source/ /usr/src/asterisk-chan-quectel/

WORKDIR /usr/src/asterisk-chan-quectel
RUN cmake -S . -B build \
      -DAST_HEADER_DIR=/usr/src/asterisk/include \
      -DASTERISK_VERSION_NUM=221001 \
 && cmake --build build -j2 \
 && mkdir -p /out \
 && cp "$(find build -type f -name chan_quectel.so -print -quit)" /out/chan_quectel.so

# The final image is a separate lab tag and is never started by the build script.
FROM andrius/asterisk@sha256:5933f0f383857f7fa419f3d52ed66734582a43ba90adb5fafa674461cbacb9ad
LABEL org.opencontainers.image.title="QDC507 serial 16k lab image" \
      org.opencontainers.image.description="Non-production experimental chan_quectel build; no automatic deployment" \
      qdc507.lab="serial16k-v7"
COPY --from=builder /out/chan_quectel.so /usr/lib/asterisk/modules/chan_quectel.so
RUN ldd /usr/lib/asterisk/modules/chan_quectel.so \
 && ! ldd /usr/lib/asterisk/modules/chan_quectel.so | grep -q 'not found'
