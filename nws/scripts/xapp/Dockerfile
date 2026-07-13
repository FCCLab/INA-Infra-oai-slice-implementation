# nws xApp runtime — reuse FlexRIC image (same as nws-nearRT-RIC).
#
# Build FlexRIC first if needed:
#   nws/build_scripts/build_oai_flexric.sh
# which builds from:
#   openairinterface5g/openair2/E2AP/flexric/docker/Dockerfile.flexric.ubuntu

ARG FLEXRIC_IMAGE=oai-flexric:latest
FROM ${FLEXRIC_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONPATH=/usr/local/flexric/xApp/python3 \
    LD_LIBRARY_PATH=/usr/local/lib:/flexric/build/src/xApp \
    FLEXRIC_CONF=/etc/flexric/flexric.conf \
    NWS_XAPP_IN_DOCKER=1 \
    NWS_XAPP_API_HOST=0.0.0.0 \
    NWS_XAPP_API_PORT=18080

WORKDIR /xapp

COPY xapp_slice.py /xapp/xapp_slice.py

RUN mkdir -p /xapp/out /etc/flexric

# Host network: REST on :18080 (override via NWS_XAPP_API_PORT / compose env).
CMD ["python3", "-u", "/xapp/xapp_slice.py", \
     "--conf", "/etc/flexric/flexric.conf", \
     "--out", "/xapp/out/rt_slice_stats.json", \
     "--ns-out", "/xapp/out/rt_ns_slice_policy.json", \
     "--print"]
