FROM ghcr.io/home-assistant/base:latest

ARG BUILD_VERSION
ARG BUILD_ARCH

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

RUN \
  apk add --no-cache \
    py3-pip \
    py3-requests \
    py3-setuptools \
    py3-yaml \
    python3

WORKDIR /opt/ambilight-wled-bridge

COPY package/ ./
RUN python3 -m pip install --break-system-packages --no-cache-dir --no-deps .

COPY run.sh /run.sh
COPY run_addon.py /opt/ambilight-wled-bridge/run_addon.py
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
