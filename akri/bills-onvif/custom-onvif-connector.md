# Custom ONVIF Akri Connector — Specification & Build Plan

## Overview

This document specifies a custom Python-based AKRI connector for Azure IoT Operations (AIO)
that polls ONVIF-compatible IP cameras and video devices. The connector discovers ONVIF
devices, retrieves device information, stream URIs, and snapshot data, and publishes
telemetry to the AIO MQTT broker.

**Reference baseline**: [Build and deploy custom Akri connectors](https://learn.microsoft.com/en-us/azure/iot-operations/develop-edge-apps/howto-develop-akri-connectors)  
**Connector contract**: [Akri operator and connector contract](https://github.com/Azure/iot-operations-sdks/blob/main/doc/akri_connector/Akri%20operator%20and%20connector%20contract.md)

---

## Directory Structure

```
akri/bills-onvif/
    custom-onvif-connector.md       <- This spec file
    pyproject.toml                  <- UV project definition + dependencies
    uv.lock                         <- UV lock file (committed to source control)
    connector-metadata.json         <- AKRI connector metadata for AIO portal
    Dockerfile                      <- Multi-stage Python container image
    app.py                          <- Main connector entrypoint
    onvif_sampler.py                <- ONVIF device polling logic
    mqtt_client.py                  <- MQTT broker connection + SAT auth
    config_watcher.py               <- Mounted file system config reader
    deployment.yaml                 <- Kubernetes StatefulSet manifest (reference)
    Build-ConnectorImage.ps1        <- Build + push image to ACR via az acr build
    Publish-ConnectorMetadata.ps1   <- Push connector-metadata.json to ACR via ORAS
    README.md                       <- Operational guide
    test/
        local_config/               <- Mock mounted files for local testing
            MQTT_CONNECTION_CONFIGURATION
            adr_resources_names/
        run_local.py                <- Local test runner (sets env vars, runs app.py)
```

---

## Connector Contract Requirements

The connector must implement the [Akri operator and connector contract](https://github.com/Azure/iot-operations-sdks/blob/main/doc/akri_connector/Akri%20operator%20and%20connector%20contract.md).

### Environment Variables (set by Akri operator)

| Variable | Path | Description |
|---|---|---|
| `CONNECTOR_ID` | n/a | Pod name — **must** be used as MQTT client ID |
| `CONNECTOR_NAMESPACE` | n/a | Kubernetes namespace of connector pod |
| `CONNECTOR_CONFIGURATION_MOUNT_PATH` | `/etc/akri/config/connector_configuration` | MQTT config, diagnostics |
| `BROKER_SAT_MOUNT_PATH` | `/etc/akri/secrets/broker-sat` | SAT token file (auto-refreshed) |
| `ADR_RESOURCES_NAME_MOUNT_PATH` | `/etc/akri/config/adr_resources_names` | Device → asset name mapping |
| `DEVICE_ENDPOINT_CREDENTIALS_MOUNT_PATH` | `/etc/akri/secrets/device_endpoint_auth` | ONVIF device credentials |
| `BROKER_TLS_TRUST_BUNDLE_CACERT_MOUNT_PATH` | `/etc/akri/cacerts/broker_tls_trust_bundle` | Broker TLS CA certs |
| `DEVICE_ENDPOINT_TLS_TRUST_BUNDLE_CA_CERT_MOUNT_PATH` | `/etc/akri/cacerts/device_endpoint_tls_trust_bundle` | Device TLS CA certs |
| `CONNECTOR_SECRETS_MOUNT_PATH` | `/etc/akri/secrets/connector_secrets` | Connector-level secrets |
| `CONNECTOR_SECRETS_METADATA_MOUNT_PATH` | `/etc/akri/config/connector_secrets_metadata` | Alias → path mapping |

### Mounted Configuration Files (under `CONNECTOR_CONFIGURATION_MOUNT_PATH`)

**`MQTT_CONNECTION_CONFIGURATION`** (always present):
```json
{
  "host": "<broker host>",
  "keepAliveSeconds": 60,
  "maxInflightMessages": 100,
  "protocol": "mqtts",
  "sessionExpirySeconds": 3600,
  "tls": { "mode": "enabled" }
}
```

**`DIAGNOSTICS`** (optional):
```json
{ "logs": { "level": "info" } }
```

**`ADDITIONAL_CONNECTOR_CONFIGURATION`** (optional — connector-defined schema):
```json
{ "defaultSamplingIntervalMs": 5000 }
```

### ADR Resources File (under `ADR_RESOURCES_NAME_MOUNT_PATH`)

One file per device inbound endpoint:
- **Filename**: `{DeviceName}_{InboundEndpointName}`
- **Content**: Asset names (newline-separated)

The connector must watch this directory for additions, removals, and updates.

---

## ONVIF Connector Design

### Scenario

The connector connects to ONVIF-compliant IP cameras registered as **Devices** in AIO.
Each device has an inbound endpoint with the camera's address and optional credentials.
The connector periodically polls each camera and publishes JSON telemetry to the MQTT broker
on a per-asset topic.

### Supported Datasets

| Dataset Name | Description | Data Points |
|---|---|---|
| `device_info` | Static device metadata | manufacturer, model, firmware, serial |
| `stream_status` | Live stream URI and health | stream_uri, snapshot_uri, profile_name |
| `ptz_status` | Pan/Tilt/Zoom position (if supported) | pan, tilt, zoom |

### MQTT Topic Pattern

```
onvif/{device_name}/{asset_name}/{dataset_name}
```

Example:
```
onvif/camera-lobby/lobby-cam-asset/device_info
```

### Message Schema

```json
{
  "timestamp": "2026-05-21T10:30:00Z",
  "device_name": "camera-lobby",
  "asset_name": "lobby-cam-asset",
  "dataset": "device_info",
  "data": {
    "manufacturer": "Axis",
    "model": "P3245-V",
    "firmware_version": "10.12.4",
    "serial_number": "ACCC8E123456"
  }
}
```

---

## Python Architecture

### `app.py` — Main Entrypoint

Responsibilities:
- Read environment variables
- Load `MQTT_CONNECTION_CONFIGURATION`
- Initialize `ConfigWatcher` and `MqttClient`
- Start the polling loop
- Handle graceful shutdown (SIGTERM)

Key patterns:
- Uses `asyncio` event loop
- Registers SIGTERM handler to flush MQTT and exit cleanly
- Passes `CONNECTOR_ID` directly as MQTT client ID (contract requirement)

### `config_watcher.py` — Configuration & ADR Resource Monitor

Responsibilities:
- Read all mounted config files on startup
- Watch `ADR_RESOURCES_NAME_MOUNT_PATH` for file changes using `watchdog`
- Parse device → asset mappings from ADR resource files
- Expose callback API: `on_device_added(device_name, endpoint_name, asset_names)`  
  and `on_device_removed(device_name, endpoint_name)`
- Read and refresh SAT token from `BROKER_SAT_MOUNT_PATH`

### `mqtt_client.py` — MQTT Broker Client

Responsibilities:
- Connect to broker using config from `MQTT_CONNECTION_CONFIGURATION`
- Authenticate with SAT token (MQTT v5 enhanced auth or password field)
- Use `CONNECTOR_ID` as client ID (required by contract)
- Load TLS trust bundle from `BROKER_TLS_TRUST_BUNDLE_CACERT_MOUNT_PATH`
- Publish telemetry JSON payloads
- Monitor SAT token file for rotation and reconnect if needed

Dependencies: `paho-mqtt>=2.0`

### `onvif_sampler.py` — ONVIF Device Poller

Responsibilities:
- Build ONVIF client for each device endpoint (using `onvif-zeep`)
- Read credentials from `DEVICE_ENDPOINT_CREDENTIALS_MOUNT_PATH`
- Implement per-dataset sampling:
  - `sample_device_info()` → calls `GetDeviceInformation`
  - `sample_stream_status()` → calls `GetProfiles` + `GetStreamUri` + `GetSnapshotUri`
  - `sample_ptz_status()` → calls `GetStatus` on PTZ service (if supported)
- Configurable sampling interval per dataset (from `ADDITIONAL_CONNECTOR_CONFIGURATION`)
- Retry logic: 3 attempts with 1s backoff before raising

---

## Files to Build

### Phase 1: Project Foundation

#### `pyproject.toml`
UV project file — the single source of truth for dependencies:
```toml
[project]
name = "bills-onvif-connector"
version = "1.0.0"
requires-python = ">=3.11"
dependencies = [
    "paho-mqtt>=2.0.0",
    "onvif-zeep>=0.2.12",
    "watchdog>=4.0.0",
    "cryptography>=42.0.0",
]

[tool.uv]
dev-dependencies = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]
```

`uv.lock` is generated from this file and committed to source control so the
Dockerfile and local environment always use identical resolved versions.

#### `Dockerfile`
- Base: `python:3.11-slim`
- Copies `uv` binary from `ghcr.io/astral-sh/uv:latest` (consistent with repo pattern)
- Runs `uv pip install --system --no-cache -r pyproject.toml` to install from lock file
- Non-root user for security
- Copies: `app.py`, `onvif_sampler.py`, `mqtt_client.py`, `config_watcher.py`

#### `connector-metadata.json`
- Schema: `aio-connector-metadata-9.0-preview.json`
- Name: `BillsOnvifConnector`
- endpointType: `Bills.Onvif`
- Configurable fields: address, username, password
- Dataset config: `SamplingIntervalMs`
- Data point fields: `dataSource` (ONVIF service path override)
- `supportedArchitectures`: `["linux/amd64", "linux/arm64"]`

### Phase 2: Core Python Modules

#### `config_watcher.py`
- `ConfigWatcher` class
- `load_mqtt_config()` → returns `MqttConnectionConfig` dataclass
- `load_adr_resources()` → returns `dict[str, list[str]]` (endpoint → asset list)
- `watch_adr_resources(callback)` → starts `watchdog` observer thread
- `get_sat_token()` → reads current SAT token from file

#### `mqtt_client.py`
- `AkriMqttClient` class wrapping `paho.mqtt.client.Client`
- `connect(mqtt_config, client_id, sat_token, ca_cert_path)`
- `publish(topic, payload_dict, qos=1)`
- `disconnect()`
- SAT token rotation handler

#### `onvif_sampler.py`
- `OnvifDeviceSampler` class
- `__init__(endpoint_address, credentials)`
- `sample_dataset(dataset_name, data_points)` → returns `dict`
- Internal methods per dataset
- Handles `onvif.exceptions.ONVIFError` gracefully

#### `app.py`
- `ConnectorApp` class
- `run()` — main async loop
- `_poll_device(device_name, endpoint_name, asset_names)` coroutine
- `_on_device_added(...)` / `_on_device_removed(...)` callbacks
- Graceful shutdown with asyncio cancellation

### Phase 3: Build & Deployment Scripts

#### `Build-ConnectorImage.ps1`
- Reads `aio_config.json` using same Find-ConfigFile pattern as `Deploy-EdgeModules.ps1`
- Extracts `azure.container_registry` for ACR name
- Runs `az acr build` (no Docker required — cloud build)
- Parameters: `-ImageTag` (default: `latest`), `-ConfigPath`

#### `Publish-ConnectorMetadata.ps1`
- Reads `aio_config.json` for ACR name
- Uses `oras push` to publish `connector-metadata.json` with correct media type:
  `application/vnd.microsoft.akri-connector.v1+json`
- Full ORAS command:
  ```
  oras push --config /dev/null:application/vnd.microsoft.akri-connector.v1+json \
      <ACR>.azurecr.io/bills-onvif-connector-metadata:latest \
      connector-metadata.json:application/json
  ```
- Checks that ORAS CLI is installed; prints install instructions if not
- Parameters: `-MetadataTag` (default: `latest`), `-ConfigPath`

### Phase 4: Kubernetes Manifest

#### `deployment.yaml`
- Reference-only StatefulSet (Akri operator manages the actual deployment)
- Shows required volume mounts for all `CONNECTOR_*_MOUNT_PATH` env vars
- Namespace: `azure-iot-operations`

---

## ONVIF Simulator

The `onvifsim` module is the test target for Phases 3–4. It provides a full-coverage
fake ONVIF camera server with configurable WS-Security auth (none/basic/digest) and
a looping image feed.

See [modules/onvifsim/onvif-cam-simulator.md](../../modules/onvifsim/onvif-cam-simulator.md)
for the full specification and build plan.

---

## `connector-metadata.json` — Full Specification

```json
{
  "$schema": "https://raw.githubusercontent.com/SchemaStore/schemastore/refs/heads/master/src/schemas/json/aio-connector-metadata-9.0-preview.json",
  "name": "BillsOnvifConnector",
  "description": "Custom ONVIF connector for polling IP cameras and video devices",
  "version": "1.0.0",
  "imageConfigurationSettings": {
    "imageName": "bills-onvif-connector",
    "tag": "latest"
  },
  "supportedArchitectures": ["linux/amd64", "linux/arm64"],
  "inboundEndpoints": [
    {
      "endpointType": "Bills.Onvif",
      "version": "1.0",
      "fields": {
        "address": {
          "input": "required",
          "exampleValue": "http://192.168.1.100:80/onvif/device_service",
          "description": "ONVIF device service URL"
        },
        "username": {
          "input": "optional",
          "exampleValue": "admin",
          "description": "ONVIF device username"
        },
        "password": {
          "input": "optional",
          "description": "ONVIF device password (use secret reference)"
        }
      },
      "datasets": {
        "limits": { "minimum": 1 },
        "fields": {
          "dataSource": {
            "input": "optional",
            "exampleValue": "device_info"
          }
        },
        "datasetConfigurationSchema": {
          "$schema": "http://json-schema.org/draft-07/schema#",
          "type": "object",
          "properties": {
            "SamplingIntervalMs": {
              "description": "How frequently to poll this dataset (milliseconds)",
              "type": "integer",
              "default": 5000
            }
          }
        },
        "dataPoints": {
          "limits": { "minimum": 0 },
          "fields": {
            "dataSource": {
              "input": "optional",
              "exampleValue": "manufacturer"
            }
          },
          "dataPointConfigurationSchema": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
              "OvvifProfile": {
                "description": "ONVIF media profile token to use (stream_status dataset)",
                "type": "string"
              }
            }
          }
        }
      }
    }
  ]
}
```

---

## Local Development with UV

All local testing and troubleshooting uses UV to manage the Python environment.
No global package installs are needed.

### Initial Setup

```powershell
# From akri/bills-onvif/

# Create the virtual environment and install all dependencies
uv sync

# Install with dev dependencies (pytest, etc.)
uv sync --dev
```

### Running the Connector Locally

The connector reads from mounted file paths set by env vars. For local testing,
`test/run_local.py` sets these env vars to point at `test/local_config/` mock files
before importing and running `app.py`:

```powershell
# Run the connector against mock config and a real ONVIF camera
uv run python test/run_local.py

# Or run directly with env vars pointing at test fixtures
$env:CONNECTOR_ID = "test-connector-pod"
$env:CONNECTOR_CONFIGURATION_MOUNT_PATH = "test/local_config"
$env:ADR_RESOURCES_NAME_MOUNT_PATH = "test/local_config/adr_resources_names"
$env:BROKER_SAT_MOUNT_PATH = "test/local_config/broker-sat"
uv run python app.py
```

### Running Tests

```powershell
# Run all tests
uv run pytest

# Run a specific test file
uv run pytest test/test_onvif_sampler.py -v

# Run with output (useful for connector log debugging)
uv run pytest -s
```

### Adding a Dependency

```powershell
# Add a runtime dependency (updates pyproject.toml and uv.lock)
uv add requests

# Add a dev-only dependency
uv add --dev pytest-mock
```

### Mock Config Files for Local Testing

`test/local_config/MQTT_CONNECTION_CONFIGURATION` — points at a local Mosquitto instance:
```json
{
  "host": "localhost",
  "keepAliveSeconds": 60,
  "maxInflightMessages": 100,
  "protocol": "mqtt",
  "sessionExpirySeconds": 3600,
  "tls": { "mode": "disabled" }
}
```

`test/local_config/adr_resources_names/camera-lobby_onvif-endpoint` — one asset per line:
```
lobby-cam-asset
```

`test/local_config/broker-sat` — dummy token string for local testing:
```
local-test-token
```

---

## Build Phases

### Phase 1 — Scaffold & Metadata (Start Here)

Deliverables:
- [ ] `pyproject.toml` with all dependencies
- [ ] `uv.lock` (generated via `uv sync`)
- [ ] `Dockerfile` (uv-based, non-root, multi-arch hints)
- [ ] `connector-metadata.json` with `Bills.Onvif` endpoint type
- [ ] `Build-ConnectorImage.ps1` (reads `aio_config.json`, runs `az acr build`)
- [ ] `Publish-ConnectorMetadata.ps1` (reads `aio_config.json`, runs `oras push`)

Validation:
```powershell
# From akri/bills-onvif/
.\Build-ConnectorImage.ps1 -ImageTag dev
.\Publish-ConnectorMetadata.ps1 -MetadataTag latest
```

### Phase 2 — Config & MQTT Layer

Deliverables:
- [ ] `config_watcher.py` — reads all mounted config files + ADR resource watcher
- [ ] `mqtt_client.py` — MQTT v5 connection with SAT auth and TLS

Validation:
- Unit test with mocked file system (no cluster required)
- `config_watcher` reads sample JSON files from a local test directory
- `mqtt_client` connects to a local Mosquitto instance

### Phase 3 — ONVIF Sampler

**Develop in parallel with Phase 3b (onvifsim). Use onvifsim as the test target.**

Deliverables:
- [ ] `onvif_sampler.py` — ONVIF device polling for all three datasets

Validation:
- Start onvifsim locally (`uv run python app.py` in `modules/onvifsim/`)
- Point `onvif_sampler.py` at `http://localhost:8080/onvif/device_service`
- Verify `device_info` returns the fake manufacturer/model from `config.yaml`
- Verify `stream_status` returns `http://localhost:8080/stream` as the stream URI
- Verify `ptz_status` returns dummy pan/tilt/zoom without raising

### Phase 3b — ONVIF Simulator (`modules/onvifsim`)

**Parallel track to Phase 3. Complete enough to unblock sampler testing before Phase 3 is finished.**

See [modules/onvifsim/onvif-cam-simulator.md](../../modules/onvifsim/onvif-cam-simulator.md)
for deliverables and validation steps.

Minimum milestone to unblock Phase 3: onvifsim running locally with `uv run python app.py`,
responding to `GetDeviceInformation` and `GetStreamUri`, and returning a valid snapshot at `/snapshot`.

### Phase 4 — Main App & Integration

Deliverables:
- [ ] `app.py` — ties all modules together, async polling loop, SIGTERM handler
- [ ] `deployment.yaml` — reference manifest with all required volume mounts

Validation:
- Deploy to AIO cluster manually via `kubectl apply`
- Check pod logs for successful MQTT connection and telemetry publish
- Subscribe to `onvif/#` on AIO broker and verify messages arrive

### Phase 5 — AIO Portal Integration

Steps:
1. Run `Publish-ConnectorMetadata.ps1` to push `connector-metadata.json` to ACR
2. In Azure portal → IoT Operations instance → Components → Connector templates
3. Select `+ Create` → choose `BillsOnvifConnector` from the list
4. Name the template instance (e.g., `bills-onvif-v1`)
5. Create a Device with endpoint type `Bills.Onvif` and camera address
6. Create an Asset with dataset `device_info` (SamplingIntervalMs: 5000)
7. Verify telemetry arrives on `onvif/{device}/{asset}/device_info`

---

## aio_config.json Fields Used

The scripts read from `config/aio_config.json` (same file used across this repo):

| Field | Used By | Purpose |
|---|---|---|
| `azure.subscription_id` | Both scripts | Azure login context |
| `azure.resource_group` | Both scripts | ACR lookup |
| `azure.container_registry` | Both scripts | ACR name (short or FQDN) |

The scripts use the same `Find-ConfigFile` search order as `Deploy-EdgeModules.ps1`:
1. Explicit `-ConfigPath` parameter
2. `../config/aio_config.json` (relative to script)
3. Current directory `aio_config.json`

---

## Prerequisites

| Tool | Required For | Install |
|---|---|---|
| Azure CLI | Both build scripts | `winget install Microsoft.AzureCLI` |
| ORAS CLI | `Publish-ConnectorMetadata.ps1` | `winget install oras-project.oras` |
| UV | Local dev + testing | `winget install astral-sh.uv` |
| Python 3.11+ | UV environment target | `winget install Python.Python.3.11` |
| `kubectl` | Phase 4 validation | Included with AIO setup |

No Docker Desktop is required — all container builds use `az acr build` (cloud build).
UV manages the local `.venv` inside `akri/bills-onvif/` and is never installed globally
into the system Python.

---

## Security Notes

- ONVIF credentials are passed via `DEVICE_ENDPOINT_CREDENTIALS_MOUNT_PATH` mounted by Akri; they are never embedded in the image or config files.
- MQTT auth uses SAT token (K8S-SAT); the token file is monitored for rotation.
- The container runs as a non-root user.
- TLS is required for both broker and device connections when available.
- Do not hardcode any connection strings, passwords, or tokens in source files.

---

## Known ONVIF Considerations

- **WS-UsernameToken auth**: Most ONVIF devices require digest authentication. `onvif-zeep` handles this with `wsse:UsernameToken`.
- **Profile tokens**: `GetProfiles` must be called before `GetStreamUri`; the profile token is required.
- **PTZ support**: Not all cameras support PTZ; the `ptz_status` dataset should degrade gracefully to empty/null if the PTZ service is unavailable.
- **RTSP URIs**: The stream URI returned by `GetStreamUri` typically requires the same credentials for playback. The connector should not log these URIs at DEBUG level.
- **Firmware quirks**: Some cameras return partial WSDL or non-standard namespaces. `onvif-zeep` with `no_cache=True` and `transport` settings handles most of these cases.
