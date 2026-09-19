FROM python:3.12-alpine
ARG BUILD_VERSION
ARG BUILD_ARCH
RUN apk add --no-cache ca-certificates ffmpeg && pip install --no-cache-dir requests websocket-client Pillow
COPY run.py /run.py
RUN chmod +x /run.py
LABEL io.hass.type="app" io.hass.version="${BUILD_VERSION}" io.hass.arch="${BUILD_ARCH}"
CMD ["/run.py"]
