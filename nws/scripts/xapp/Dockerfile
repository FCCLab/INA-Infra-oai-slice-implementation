# nws xApp runtime — reuse FlexRIC image (same as nws-nearRT-RIC).
#
# Package:
#   nws/build_scripts/build_nws_xapp.sh
#   nws/build_scripts/build_nws_xapp.sh --with-flexric   # if xapp_sdk.py missing
# FlexRIC image:
#   nws/build_scripts/build_oai_flexric.sh
# (nws/build_scripts/Dockerfile.flexric.ubuntu uses -DXAPP_MULTILANGUAGE=ON)

ARG FLEXRIC_IMAGE=oai-flexric:latest
FROM ${FLEXRIC_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONPATH=/usr/local/flexric/xApp/python3 \
    LD_LIBRARY_PATH=/usr/local/lib:/flexric/build/src/xApp \
    FLEXRIC_CONF=/usr/local/etc/flexric/flexric.conf \
    NWS_XAPP_IN_DOCKER=1 \
    NWS_XAPP_API_HOST=0.0.0.0 \
    NWS_XAPP_API_PORT=18080

WORKDIR /xapp

RUN test -f /usr/local/flexric/xApp/python3/xapp_sdk.py

COPY xapp_slice.py /xapp/xapp_slice.py
COPY slice_gui.html /xapp/slice_gui.html

RUN mkdir -p /xapp/out /usr/local/etc/flexric

# Host network: REST on :18080 (override via NWS_XAPP_API_PORT / compose env).
CMD ["python3", "-u", "/xapp/xapp_slice.py", \
     "--conf", "/usr/local/etc/flexric/flexric.conf", \
     "--out", "/xapp/out/rt_slice_stats.json", \
     "--ns-out", "/xapp/out/rt_ns_slice_policy.json", \
     "--print"]
