FROM ubuntu:22.04 AS builder

ARG DEBIAN_FRONTEND=noninteractive
ARG VPP_REF=v26.06
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        lsb-release \
        make \
        sudo \
    && rm -rf /var/lib/apt/lists/*

RUN for i in 1 2 3; do \
        git clone --depth 1 --branch "${VPP_REF}" https://gerrit.fd.io/r/vpp && break; \
        rm -rf vpp; \
        sleep 10; \
    done \
    && test -d vpp/.git

WORKDIR /build/vpp

RUN awk ' \
    /if \(udp_connection_port_used_extern \(clib_net_to_host_u16 \(lcl->port\),/ { skip = 1; found = 1; next } \
    skip && /^    }$/ { skip = 0; next } \
    skip { next } \
    { print } \
    END { if (!found) exit 42 } \
    ' src/vnet/udp/udp.c > /tmp/udp.c \
    && mv /tmp/udp.c src/vnet/udp/udp.c \
    && ! grep -n "udp_connection_port_used_extern (clib_net_to_host_u16 (lcl->port)" src/vnet/udp/udp.c

RUN yes | make install-dep
RUN make build-release
RUN make pkg-deb

RUN mkdir -p /tmp/vpp-debs \
    && find build-root -maxdepth 1 -type f -name '*.deb' \
        ! -name '*-dbg_*.deb' \
        ! -name '*-dbgsym_*.deb' \
        -exec cp {} /tmp/vpp-debs/ \;

RUN cmake -S extras/libmemif -B /tmp/libmemif-build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr/local \
    && cmake --build /tmp/libmemif-build --parallel "$(nproc)" \
    && DESTDIR=/tmp/libmemif-root cmake --install /tmp/libmemif-build


FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive

COPY --from=builder /tmp/vpp-debs /tmp/vpp-debs
COPY --from=builder /tmp/libmemif-root/usr/local /usr/local

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        supervisor \
        vim \
        /tmp/vpp-debs/*.deb \
    && ldconfig \
    && rm -rf /tmp/vpp-debs /var/lib/apt/lists/*

CMD ["/bin/bash"]
