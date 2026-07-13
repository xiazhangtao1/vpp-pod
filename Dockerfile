ARG VPP_SOURCE=local
ARG VPP_REF=v26.06

FROM scratch AS vpp-local
COPY vpp /

FROM ubuntu:22.04 AS vpp-fetch

ARG DEBIAN_FRONTEND=noninteractive
ARG VPP_REF
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
    && rm -rf /var/lib/apt/lists/*

RUN for i in 1 2 3; do \
        git clone --depth 1 --branch "${VPP_REF}" https://gerrit.fd.io/r/vpp && break; \
        rm -rf vpp; \
        sleep 10; \
    done \
    && test -d vpp/.git

FROM scratch AS vpp-online
COPY --from=vpp-fetch /build/vpp /

FROM vpp-${VPP_SOURCE} AS vpp-source


FROM ubuntu:22.04 AS builder

ARG DEBIAN_FRONTEND=noninteractive
ARG VPP_REF
ARG VPP_SOURCE
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        lsb-release \
        make \
        sudo \
    && rm -rf /var/lib/apt/lists/*

COPY --from=vpp-source / /build/vpp

WORKDIR /build/vpp

RUN test -f Makefile \
    && test -f src/vnet/udp/udp.c

RUN if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then \
        git init; \
        git config user.name "VPP build"; \
        git config user.email "vpp-build@localhost"; \
        git add -f .; \
        git commit -m "VPP ${VPP_REF} source snapshot"; \
        git tag -a "${VPP_REF}" -m "VPP ${VPP_REF}"; \
    fi

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
RUN if [ "${VPP_SOURCE}" = "local" ]; then \
        git tag -a "${VPP_REF}-rc0" -m "VPP ${VPP_REF} rc0"; \
    fi
RUN if [ "${VPP_SOURCE}" = "local" ]; then \
        export http_proxy=http://127.0.0.1:9; \
        export https_proxy=http://127.0.0.1:9; \
        export PIP_NO_INDEX=1; \
        export PIP_FIND_LINKS=/build/vpp/build/external/downloads; \
    fi; \
    make build-release
RUN if [ "${VPP_SOURCE}" = "local" ]; then \
        export http_proxy=http://127.0.0.1:9; \
        export https_proxy=http://127.0.0.1:9; \
        export PIP_NO_INDEX=1; \
        export PIP_FIND_LINKS=/build/vpp/build/external/downloads; \
    fi; \
    make pkg-deb

RUN mkdir -p /tmp/vpp-debs \
    && find build-root -maxdepth 1 -type f -name '*.deb' \
        ! -name '*-dbg_*.deb' \
        ! -name '*-dbgsym_*.deb' \
        -exec cp {} /tmp/vpp-debs/ \;

RUN cmake -S extras/libmemif -B /tmp/libmemif-build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr/local \
        -DBUILD_TESTING=OFF \
    && cmake --build /tmp/libmemif-build --parallel "$(nproc)" \
    && DESTDIR=/tmp/libmemif-root cmake --install /tmp/libmemif-build


FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive

COPY --from=builder /tmp/vpp-debs /tmp/vpp-debs
COPY --from=builder /tmp/libmemif-root/usr/local /usr/local

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        python3 \
        supervisor \
        vim \
        /tmp/vpp-debs/*.deb \
    && ldconfig \
    && rm -rf /tmp/vpp-debs /var/lib/apt/lists/*

COPY config /usr/share/vpp/templates
COPY scripts/vpp-entrypoint.py /usr/local/bin/vpp-entrypoint

ENTRYPOINT ["/usr/local/bin/vpp-entrypoint"]
