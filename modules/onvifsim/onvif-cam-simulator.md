# ONVIF Camera Simulator — `onvifsim`

Specification and build plan for `onvifsim`: a full-coverage fake ONVIF camera
server used for developing and troubleshooting the AKRI connector without needing
a physical camera.

**Connector spec**: [akri/bills-onvif/custom-onvif-connector.md](../../akri/bills-onvif/custom-onvif-connector.md)

---

## Purpose

- Provides a realistic ONVIF target for the connector during development without
  needing a physical camera
- Auth can be toggled between anonymous, basic, and digest in `config.yaml` — no
  code changes needed
- Can be deployed to the same K8s cluster as the connector for integrated testing
- Can be run locally with `uv run` pointing at a local images folder

---

## Directory Structure

```
modules/onvifsim/
    app.py                  <- Flask entrypoint — mounts all SOAP service blueprints
    auth.py                 <- WS-Security UsernameToken validator (none/basic/digest)
    device_service.py       <- ONVIF Device service (all 100+ operations)
    media_service.py        <- ONVIF Media service (profiles, streams, snapshots)
    ptz_service.py          <- ONVIF PTZ service (status, presets, move)
    imaging_service.py      <- ONVIF Imaging service (settings, options)
    events_service.py       <- ONVIF Events service (pull-point subscriptions)
    image_server.py         <- Image rotation loop + snapshot/MJPEG HTTP endpoints
    soap_utils.py           <- Shared SOAP envelope helpers and fault builder
    pyproject.toml          <- UV project definition
    uv.lock                 <- UV lock file
    Dockerfile              <- Python/uv container
    deployment.yaml         <- K8s Deployment manifest
    config.yaml             <- All simulator settings including auth
    README.md               <- Usage guide
    onvif-cam-simulator.md  <- This spec file
```

Images are **not** stored in the module folder. They are provided by the user and
mounted at runtime via the `IMAGES_DIR` environment variable.

---

## Service Endpoints

Each ONVIF service is a separate Flask Blueprint mounted at its standard path:

| Service | URL Path | WSDL Namespace |
|---|---|---|
| Device | `/onvif/device_service` | `http://www.onvif.org/ver10/device/wsdl` |
| Media | `/onvif/media_service` | `http://www.onvif.org/ver10/media/wsdl` |
| PTZ | `/onvif/ptz_service` | `http://www.onvif.org/ver20/ptz/wsdl` |
| Imaging | `/onvif/imaging_service` | `http://www.onvif.org/ver20/imaging/wsdl` |
| Events | `/onvif/events_service` | `http://www.onvif.org/ver10/events/wsdl` |

All SOAP requests are routed by the `SOAPAction` HTTP header. Unrecognised actions
receive a `ter:ActionNotSupported` fault.

---

## Device Service Operations (`/onvif/device_service`)

The device service implements all 100+ operations from the ONVIF Core Spec v2.5.
Operations are grouped into three tiers:

**Tier 1 — Fully implemented** (real logic, values from `config.yaml`):

| Operation | Response |
|---|---|
| `GetSystemDateAndTime` | Current UTC time |
| `GetDeviceInformation` | Manufacturer, Model, FirmwareVersion, SerialNumber, HardwareId from config |
| `GetCapabilities` | All service XAddrs, WS-Security capabilities |
| `GetServices` | Lists all 5 services with their XAddrs and versions |
| `GetServiceCapabilities` | Security caps: `UsernameToken=true`, `HttpDigest=false` (matches `auth.mode`) |
| `GetScopes` | Fixed scopes: `onvif://www.onvif.org/type/video_encoder`, `onvif://www.onvif.org/hardware/{device_name}`, `onvif://www.onvif.org/name/{device_name}` |
| `GetHostname` | Returns `device_name` from config |
| `GetEndpointReference` | Returns a stable GUID derived from `device_serial` |
| `GetUsers` | Returns single admin user (username from config, no password returned) |

**Tier 2 — Stubbed** (valid minimal SOAP response, no-op for a simulator):

| Operation | Stub Behaviour |
|---|---|
| `SetSystemDateAndTime` | Accepts and returns empty success |
| `SystemReboot` | Returns `"Simulated reboot"` message, does not actually reboot |
| `SetScopes` / `AddScopes` / `RemoveScopes` | Accepts silently |
| `GetDiscoveryMode` / `SetDiscoveryMode` | Returns `Discoverable` |
| `GetRemoteDiscoveryMode` / `SetRemoteDiscoveryMode` | Returns `Discoverable` |
| `GetDNS` / `SetDNS` | Returns empty DNS config |
| `GetNTP` / `SetNTP` | Returns `FromDHCP=false`, no servers |
| `GetNetworkInterfaces` | Returns single fake eth0 with loopback IP |
| `GetNetworkProtocols` | Returns HTTP enabled on configured port |
| `GetNetworkDefaultGateway` | Returns `0.0.0.0` |
| `GetHostname` / `SetHostname` | Read/accept |
| `GetZeroConfiguration` | Returns disabled |
| `GetDynamicDNS` | Returns `NoUpdate` |
| `GetWsdlUrl` | Returns a URL pointing at public ONVIF WSDL |
| `GetSystemLog` | Returns empty string log |
| `GetSystemSupportInformation` | Returns `"Simulated device"` |
| `CreateUsers` / `DeleteUsers` / `SetUser` | Accept silently (no real user store) |
| `GetRelayOutputs` | Returns empty list |
| `GetIPAddressFilter` | Returns empty filter |
| `GetDot11Capabilities` | Returns all-false capabilities |
| `GetDot11Status` | Returns empty status |
| `GetStorageConfigurations` | Returns empty list |
| `GetGeoLocation` | Returns empty list |
| All remaining Dot1X, Certificate, AccessPolicy operations | Return empty or minimal valid response |

**Tier 3 — Not supported** (returns `ter:ActionNotSupported`):
Operations not applicable to a simulated device: `UpgradeSystemFirmware`,
`StartFirmwareUpgrade`, `StartSystemRestore`, `SetNetworkInterfaces`, etc.

---

## Media Service Operations (`/onvif/media_service`)

**Tier 1 — Fully implemented**:

| Operation | Response |
|---|---|
| `GetProfiles` | One profile: token=`profile_0`, name=`MainStream`, video source token=`video_0` |
| `GetProfile` | Returns `profile_0` if token matches |
| `GetVideoSources` | One source: token=`video_0`, resolution from config |
| `GetVideoSourceConfigurations` | One config bound to `video_0` |
| `GetVideoEncoderConfigurations` | One H.264 config at resolution from config |
| `GetStreamUri` | `http://<host>:<port>/stream` (MJPEG) for any transport |
| `GetSnapshotUri` | `http://<host>:<port>/snapshot` for `profile_0` |

**Tier 2 — Stubbed**:

| Operation | Stub |
|---|---|
| `GetVideoEncoderConfigurationOptions` | Returns minimal H.264 options |
| `GetCompatibleVideoEncoderConfigurations` | Returns the single encoder config |
| `GetAudioSources` | Empty list |
| `GetAudioSourceConfigurations` | Empty list |
| `GetAudioEncoderConfigurations` | Empty list |
| `GetMetadataConfigurations` | Empty list |
| `StartMulticastStreaming` / `StopMulticastStreaming` | Accept silently |
| `CreateProfile` / `DeleteProfile` | Accept silently |
| All Set configuration operations | Accept silently |

---

## PTZ Service Operations (`/onvif/ptz_service`)

**Tier 1 — Fully implemented**:

| Operation | Response |
|---|---|
| `GetNodes` | One node: token=`ptz_node_0`, supports absolute/relative/continuous move |
| `GetStatus` | Returns pan/tilt/zoom position from `config.yaml` `ptz.default_position` |
| `GetPresets` | Returns list of named presets from `config.yaml` |
| `GotoHomePosition` | Sets position to `ptz.home_position` from config, returns success |

**Tier 2 — Stubbed**:

| Operation | Stub |
|---|---|
| `AbsoluteMove` / `RelativeMove` / `ContinuousMove` | Accept silently, update in-memory position |
| `Stop` | Accept silently |
| `GetConfigurations` / `GetConfiguration` / `GetConfigurationOptions` | Return minimal config |
| `SetPreset` / `RemovePreset` | Accept silently |
| `GotoPreset` | Accept silently |
| `SetHomePosition` | Accept silently |

---

## Imaging Service Operations (`/onvif/imaging_service`)

| Operation | Tier | Behaviour |
|---|---|---|
| `GetImagingSettings` | Tier 1 | Returns brightness/contrast/saturation from config |
| `GetOptions` | Tier 2 | Returns ranges 0–100 for all settings |
| `SetImagingSettings` | Tier 2 | Accept silently |
| `GetStatus` | Tier 2 | Returns `Idle` |
| `GetMoveOptions` | Tier 2 | Returns empty options |
| `Move` / `Stop` | Tier 2 | Accept silently |

---

## Events Service Operations (`/onvif/events_service`)

| Operation | Tier | Behaviour |
|---|---|---|
| `GetServiceCapabilities` | Tier 1 | Returns `WSPullPointSupport=true` |
| `GetEventProperties` | Tier 2 | Returns empty topic set |
| `CreatePullPointSubscription` | Tier 1 | Creates in-memory subscription, returns endpoint reference |
| `PullMessages` | Tier 1 | Returns empty `NotificationMessage` list (no events generated) |
| `Renew` | Tier 2 | Extends subscription, returns new termination time |
| `Unsubscribe` | Tier 2 | Removes subscription |

---

## Image HTTP Endpoints

| Endpoint | Behaviour |
|---|---|
| `GET /snapshot` | Returns the current image from the rotation loop as `image/jpeg` |
| `GET /stream` | MJPEG multipart stream — sends each image in the loop with configurable frame delay |
| `GET /health` | Returns `{"status": "ok", "current_image": "<filename>", "auth_mode": "<mode>"}` |

---

## Authentication

Auth is configured entirely in `config.yaml` — **no code changes needed** to toggle
between modes. The `auth.py` module reads the config at startup and applies the
chosen mode to every SOAP endpoint.

### `config.yaml` auth section

```yaml
auth:
  mode: digest          # none | basic | digest
  username: admin
  password: "Password1!"
```

| Mode | Behaviour |
|---|---|
| `none` | Anonymous — WS-Security header is optional; if present, it is ignored |
| `basic` | WS-UsernameToken with `PasswordText` type — password sent in clear text |
| `digest` | WS-UsernameToken with `PasswordDigest` type — SHA-1(nonce + created + password), Base64-encoded |

### `auth.py` — WS-Security middleware

Implemented as a Flask `before_request` hook applied to all SOAP service blueprints:

```python
# Pseudocode for digest validation
import hashlib, base64, hmac

def validate_digest(token_username, password_digest, nonce_b64, created, expected_password):
    nonce = base64.b64decode(nonce_b64)
    expected = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + expected_password.encode()).digest()
    ).decode()
    return hmac.compare_digest(password_digest, expected)
```

On auth failure, the service returns HTTP 401 with a SOAP fault body:
```xml
<s:Fault>
  <s:Code><s:Value>s:Sender</s:Value>
    <s:Subcode><s:Value>ter:NotAuthorized</s:Value></s:Subcode>
  </s:Code>
  <s:Reason><s:Text>Sender not authorized</s:Text></s:Reason>
</s:Fault>
```

`GetSystemDateAndTime` is **always unauthenticated** — per the ONVIF spec, it must
be callable without credentials so clients can sync the nonce timestamp.

---

## `config.yaml` — Full Structure

```yaml
device:
  manufacturer: "Simulator Inc."
  model: "onvifsim-v1"
  firmware_version: "1.0.0"
  serial: "SIM-001"
  hardware_id: "HW-001"
  name: "onvifsim"            # used for hostname and WS-Discovery scopes

media:
  video_resolution:
    width: 1920
    height: 1080
  frame_rate: 25

ptz:
  enabled: true
  default_position:
    pan: 0.0
    tilt: 0.0
    zoom: 0.0
  home_position:
    pan: 0.0
    tilt: 0.0
    zoom: 0.0
  presets:
    - name: "Home"
      token: "preset_0"
      pan: 0.0
      tilt: 0.0
      zoom: 0.0

imaging:
  brightness: 50
  contrast: 50
  saturation: 50

auth:
  mode: digest              # none | basic | digest
  username: admin
  password: "Password1!"

images:
  dir: /images              # overridden by IMAGES_DIR env var
  loop_interval_ms: 2000    # time between image advances
  supported_extensions:
    - .jpg
    - .jpeg
    - .png
    - .bmp

server:
  port: 8080                # overridden by ONVIF_PORT env var

logging:
  level: info               # error | warning | info | debug
  show_soap_bodies: false   # if true, logs full SOAP XML at debug level
```

All `config.yaml` values can be overridden via environment variables. Env vars
take precedence and follow the pattern `ONVIF_<SECTION>_<KEY>` (e.g.
`ONVIF_AUTH_MODE=none`, `ONVIF_DEVICE_NAME=lobby-cam`).

---

## Environment Variables (override `config.yaml`)

| Variable | Overrides | Description |
|---|---|---|
| `IMAGES_DIR` | `images.dir` | Path to folder of images to loop through |
| `ONVIF_PORT` | `server.port` | Port to listen on |
| `ONVIF_AUTH_MODE` | `auth.mode` | `none`, `basic`, or `digest` |
| `ONVIF_AUTH_USERNAME` | `auth.username` | Username for auth |
| `ONVIF_AUTH_PASSWORD` | `auth.password` | Password for auth |
| `ONVIF_DEVICE_NAME` | `device.name` | Appears in scopes and hostname |
| `ONVIF_DEVICE_SERIAL` | `device.serial` | Appears in `GetDeviceInformation` |
| `ONVIF_LOG_LEVEL` | `logging.level` | `error`, `warning`, `info`, or `debug` |
| `ONVIF_SHOW_SOAP_BODIES` | `logging.show_soap_bodies` | `true` to log full SOAP XML (debug aid) |

---

## Python Dependencies

```toml
[project]
name = "onvifsim"
version = "1.0.0"
requires-python = ">=3.11"
dependencies = [
    "flask>=3.0",
    "lxml>=5.0",
    "pyyaml>=6.0",
]

[tool.uv]
dev-dependencies = [
    "pytest>=8.0",
    "requests>=2.32",
]
```

`flask` handles HTTP routing and MJPEG streaming. `lxml` builds and parses SOAP XML.
`pyyaml` loads `config.yaml`. Auth digest uses only stdlib `hashlib`, `base64`, `hmac`.

---

## Logging

The simulator emits structured, timestamped log lines to stdout so that
`kubectl logs` or a local terminal gives immediate insight into what a
client is actually doing. All log output uses Python's `logging` module with
a consistent format — no JSON, no external sinks, just human-readable lines.

### Log Format

```
[ONVIFSIM] 2026-05-21 10:31:04.123 | INFO  | device_service  | 10.0.0.5 | GetDeviceInformation -> 200 OK (1.2ms)
[ONVIFSIM] 2026-05-21 10:31:04.456 | WARN  | auth            | 10.0.0.5 | Digest mismatch for user 'admin' (nonce expired or bad password)
[ONVIFSIM] 2026-05-21 10:31:05.001 | DEBUG | media_service   | 10.0.0.5 | GetStreamUri profile_token=profile_0 -> http://10.0.0.8:8080/stream
```

Fields: `[ONVIFSIM]`, ISO timestamp, log level (padded), originating module,
client IP, message.

### Log Level Guide

| Level | Set Via | What Appears |
|---|---|---|
| `ERROR` | `ONVIF_LOG_LEVEL=error` | Exceptions, panics, failed SOAP serialisation |
| `WARNING` | `ONVIF_LOG_LEVEL=warning` | Auth failures, unsupported operations, missing images |
| `INFO` | `ONVIF_LOG_LEVEL=info` (default) | Every SOAP call with status code and latency, startup/shutdown |
| `DEBUG` | `ONVIF_LOG_LEVEL=debug` | Full SOAP request XML, full SOAP response XML, image loop ticks |

### Events Logged at Each Level

**Startup (INFO)**
```
[ONVIFSIM] ... | INFO  | app             | -         | Starting onvifsim v1.0.0 on port 8080
[ONVIFSIM] ... | INFO  | app             | -         | Auth mode: digest (username: admin)
[ONVIFSIM] ... | INFO  | app             | -         | Images dir: /images (14 files found)
[ONVIFSIM] ... | INFO  | image_server    | -         | Image loop started: interval=2000ms, first=dsc_001.jpg
```

**Connection / SOAP Calls (INFO)**
```
[ONVIFSIM] ... | INFO  | device_service  | 10.0.0.5  | GetSystemDateAndTime -> 200 OK (0.4ms) [no-auth]
[ONVIFSIM] ... | INFO  | device_service  | 10.0.0.5  | GetCapabilities -> 200 OK (0.8ms)
[ONVIFSIM] ... | INFO  | media_service   | 10.0.0.5  | GetProfiles -> 200 OK (0.6ms)
[ONVIFSIM] ... | INFO  | media_service   | 10.0.0.5  | GetStreamUri profile_token=profile_0 -> 200 OK (0.5ms)
[ONVIFSIM] ... | INFO  | media_service   | 10.0.0.5  | GetSnapshotUri profile_token=profile_0 -> 200 OK (0.3ms)
[ONVIFSIM] ... | INFO  | ptz_service     | 10.0.0.5  | GetStatus -> 200 OK pan=0.0 tilt=0.0 zoom=0.0 (0.4ms)
[ONVIFSIM] ... | INFO  | ptz_service     | 10.0.0.5  | AbsoluteMove pan=0.3 tilt=-0.1 zoom=0.0 -> 200 OK (0.3ms)
[ONVIFSIM] ... | INFO  | events_service  | 10.0.0.5  | CreatePullPointSubscription -> subscriptionId=sub_001 expires=PT60S (0.9ms)
[ONVIFSIM] ... | INFO  | events_service  | 10.0.0.5  | PullMessages subscriptionId=sub_001 -> 0 messages (0.2ms)
[ONVIFSIM] ... | INFO  | image_server    | 10.0.0.6  | GET /snapshot -> 200 OK current=dsc_003.jpg (1.1ms)
[ONVIFSIM] ... | INFO  | image_server    | 10.0.0.6  | GET /stream -> client connected (MJPEG)
[ONVIFSIM] ... | INFO  | image_server    | 10.0.0.6  | GET /stream -> client disconnected after 14 frames
```

**Auth Events (INFO/WARNING)**
```
[ONVIFSIM] ... | INFO  | auth            | 10.0.0.5  | Digest auth OK for user 'admin'
[ONVIFSIM] ... | WARN  | auth            | 10.0.0.9  | Auth FAILED for user 'admin' — digest mismatch (bad password or nonce reuse)
[ONVIFSIM] ... | WARN  | auth            | 10.0.0.9  | Auth FAILED — no WS-Security header present (mode=digest requires credentials)
[ONVIFSIM] ... | WARN  | auth            | 10.0.0.9  | Auth FAILED — unknown user 'root'
```

**Unsupported Operations (WARNING)**
```
[ONVIFSIM] ... | WARN  | device_service  | 10.0.0.5  | UpgradeSystemFirmware -> 400 ActionNotSupported
[ONVIFSIM] ... | WARN  | device_service  | 10.0.0.5  | Unknown SOAPAction 'http://custom.ns/DoThing' -> 400 ActionNotSupported
```

**Image Loop (DEBUG)**
```
[ONVIFSIM] ... | DEBUG | image_server    | -         | Loop tick: advanced to dsc_004.jpg (3/14)
[ONVIFSIM] ... | DEBUG | image_server    | -         | Loop tick: wrapped around to dsc_001.jpg
```

**SOAP Payloads (DEBUG)**
```
[ONVIFSIM] ... | DEBUG | device_service  | 10.0.0.5  | REQUEST:
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">...
[ONVIFSIM] ... | DEBUG | device_service  | 10.0.0.5  | RESPONSE:
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">...
```

**Errors (ERROR)**
```
[ONVIFSIM] ... | ERROR | device_service  | 10.0.0.5  | Failed to serialise GetCapabilities response: KeyError 'server.host'
[ONVIFSIM] ... | ERROR | image_server    | -         | Images dir '/images' is empty or unreadable — snapshot will return 503
```

Setting `show_soap_bodies: true` (or `ONVIF_LOG_LEVEL=debug`) is the primary
troubleshooting tool — it lets you compare the exact SOAP XML the simulator sends
against what the connector's `onvif-zeep` client expects to receive.

---

## Local Run

```powershell
# From modules/onvifsim/
uv sync

# Anonymous mode — simplest for initial connector testing
$env:IMAGES_DIR = "C:\path\to\your\images"
$env:ONVIF_AUTH_MODE = "none"
uv run python app.py

# Digest mode — matches what a real camera uses
$env:ONVIF_AUTH_MODE = "digest"
$env:ONVIF_AUTH_USERNAME = "admin"
$env:ONVIF_AUTH_PASSWORD = "Password1!"
uv run python app.py

# Verify
curl http://localhost:8080/health
curl http://localhost:8080/snapshot -o snapshot.jpg

# Confirm a SOAP call (GetDeviceInformation)
$body = @"
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
  <s:Body><GetDeviceInformation xmlns="http://www.onvif.org/ver10/device/wsdl"/></s:Body>
</s:Envelope>
"@
Invoke-RestMethod -Uri http://localhost:8080/onvif/device_service -Method Post `
    -Body $body -ContentType "application/soap+xml"
```

---

## K8s Deployment

The `deployment.yaml` mounts the images from a `ConfigMap` (for small test images)
or a `PersistentVolumeClaim` (for larger sets). The connector's `test/local_config`
ADR resource files should point at the simulator's service address:

```
http://onvifsim.default.svc.cluster.local:8080/onvif/device_service
```

---

## Build Checklist

- [ ] `pyproject.toml` + `uv.lock`
- [ ] `soap_utils.py` — envelope builder, fault builder, SOAPAction router
- [ ] `auth.py` — WS-Security middleware (none/basic/digest)
- [ ] `image_server.py` — image rotation loop, `/snapshot`, `/stream`, `/health`
- [ ] `device_service.py` — all Device service operations (Tier 1 + Tier 2 + Tier 3)
- [ ] `media_service.py` — Media service operations
- [ ] `ptz_service.py` — PTZ service operations
- [ ] `imaging_service.py` — Imaging service operations
- [ ] `events_service.py` — Events service (pull-point subscriptions)
- [ ] `app.py` — Flask app wiring all blueprints + config loader
- [ ] `config.yaml` — default values matching this spec
- [ ] `Dockerfile` — uv-based, non-root, `IMAGES_DIR` as volume mount point
- [ ] `deployment.yaml` — K8s Deployment with image volume mount
- [ ] `README.md` — operational guide (quickstart + troubleshooting tips)
