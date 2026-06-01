# onvifsim — ONVIF Camera Simulator

Full-coverage fake ONVIF camera server for testing the AKRI connector without
a physical camera. Exposes all five standard ONVIF services plus MJPEG snapshot
and stream endpoints.

## Quick Start (local, no auth)

```powershell
cd modules/onvifsim
uv sync

$env:IMAGES_DIR = "C:\path\to\your\images"
$env:ONVIF_AUTH_MODE = "none"
uv run python app.py
```

Verify:
```powershell
curl http://localhost:8080/health
curl http://localhost:8080/snapshot -o snapshot.jpg
```

## Auth Modes

Set `ONVIF_AUTH_MODE` (or `auth.mode` in `config.yaml`):

| Mode | Description |
|---|---|
| `none` | Anonymous — no credentials required |
| `basic` | WS-UsernameToken PasswordText |
| `digest` | WS-UsernameToken PasswordDigest (SHA-1 nonce+created+password) |

```powershell
$env:ONVIF_AUTH_MODE = "digest"
$env:ONVIF_AUTH_USERNAME = "admin"
$env:ONVIF_AUTH_PASSWORD = "Password1!"
uv run python app.py
```

## SOAP Endpoints

| Service | URL |
|---|---|
| Device | `http://localhost:8080/onvif/device_service` |
| Media | `http://localhost:8080/onvif/media_service` |
| PTZ | `http://localhost:8080/onvif/ptz_service` |
| Imaging | `http://localhost:8080/onvif/imaging_service` |
| Events | `http://localhost:8080/onvif/events_service` |

## Image Endpoints

| Endpoint | Description |
|---|---|
| `GET /snapshot` | Current image as `image/jpeg` |
| `GET /stream` | MJPEG multipart stream |
| `GET /health` | JSON status |

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `IMAGES_DIR` | `/images` | Folder of images to loop through |
| `ONVIF_PORT` | `8080` | Listen port |
| `ONVIF_AUTH_MODE` | `digest` | `none`, `basic`, or `digest` |
| `ONVIF_AUTH_USERNAME` | `admin` | Auth username |
| `ONVIF_AUTH_PASSWORD` | `Password1!` | Auth password |
| `ONVIF_DEVICE_NAME` | `onvifsim` | Device name (scopes, hostname) |
| `ONVIF_DEVICE_SERIAL` | `SIM-001` | Serial number |
| `ONVIF_LOG_LEVEL` | `info` | `error`, `warning`, `info`, `debug` |
| `ONVIF_SHOW_SOAP_BODIES` | `false` | Log full SOAP XML at debug level |

## Troubleshooting

Set `ONVIF_SHOW_SOAP_BODIES=true` and `ONVIF_LOG_LEVEL=debug` to see the full
SOAP exchange — useful when comparing what the simulator sends vs. what
`onvif-zeep` in the connector expects.

```powershell
$env:ONVIF_LOG_LEVEL = "debug"
$env:ONVIF_SHOW_SOAP_BODIES = "true"
uv run python app.py
```

## K8s Deployment

```powershell
# Create credentials secret
kubectl create secret generic onvifsim-credentials --from-literal=password='Password1!'

# Deploy (edit deployment.yaml to set your registry and image volume first)
kubectl apply -f deployment.yaml

# Check logs
kubectl logs -l app=onvifsim -f
```

The connector should point at:
```
http://onvifsim.default.svc.cluster.local:8080/onvif/device_service
```

## Full Spec

See [onvif-cam-simulator.md](onvif-cam-simulator.md) for the complete specification.
