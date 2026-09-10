# nws xApp — backend (E2 Slice SM + Swagger REST) and frontend console.
# Same image, different ports (API 18080, UI 18081).
#
# Build FlexRIC first if needed:
#   nws/build_scripts/build_oai_flexric.sh

ARG FLEXRIC_IMAGE=oai-flexric:latest
FROM ${FLEXRIC_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONPATH=/usr/local/flexric/xApp/python3 \
    LD_LIBRARY_PATH=/usr/local/lib:/flexric/build/src/xApp \
    FLEXRIC_CONF=/usr/local/etc/flexric/flexric.conf \
    NWS_XAPP_IN_DOCKER=1 \
    NWS_XAPP_API_HOST=0.0.0.0 \
    NWS_XAPP_API_PORT=18080 \
    NWS_XAPP_UI_PORT=18081

WORKDIR /xapp

COPY backend /xapp/backend
COPY frontend /xapp/frontend
COPY entrypoint.sh /xapp/entrypoint.sh

RUN mkdir -p /xapp/out /usr/local/etc/flexric \
    && chmod +x /xapp/entrypoint.sh

EXPOSE 18080 18081

ENTRYPOINT ["/xapp/entrypoint.sh"]
