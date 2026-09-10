# nws rApp — Non-RT slice intent backend (Swagger REST) + frontend console.
# Same image, different ports (API 18090, UI 18091).

FROM python:3.12-slim

ENV NWS_RAPP_API_HOST=0.0.0.0 \
    NWS_RAPP_API_PORT=18090 \
    NWS_RAPP_UI_PORT=18091 \
    NWS_XAPP_API_BASE=http://127.0.0.1:18080

WORKDIR /rapp

COPY backend /rapp/backend
COPY frontend /rapp/frontend
COPY entrypoint.sh /rapp/entrypoint.sh

RUN mkdir -p /rapp/out && chmod +x /rapp/entrypoint.sh

EXPOSE 18090 18091

ENTRYPOINT ["/rapp/entrypoint.sh"]
