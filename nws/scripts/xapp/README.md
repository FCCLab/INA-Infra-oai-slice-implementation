# Moved

The FlexRIC slice xApp now lives in `nws/app_xapp` (backend REST + frontend console).

```bash
cd nws/app_xapp
docker compose up --build
```

- Backend Swagger: http://127.0.0.1:18080/docs
- Frontend console: http://127.0.0.1:18081/

The Non-RT rApp is `nws/app_rapp` (API :18090, console :18091).
