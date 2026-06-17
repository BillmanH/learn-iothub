# OPC-UA Building Simulator — Design Document

## Overview

A containerized OPC-UA server that simulates telemetry from a smart building: lighting, HVAC, temperature sensors, and door locks. Modeled after **edgemqttsim** — same deployment pattern, same YAML-driven configuration, same K8s/AIO integration approach.

The sim runs an embedded OPC-UA server (using `asyncua`). Once it is deployed and reachable on the cluster, you point an AIO **MQTT Connector for OPC-UA** device endpoint at it. AIO then discovers all nodes automatically — no manual asset registration required.

---

## Module File Structure

```
modules/opcuasim/
  app.py                    # OPC-UA server entrypoint, node builder, update loop
  building.py               # Building simulation logic (state, schedules, physics)
  building_structure.yaml   # Configuration: floors, zones, devices, parameters
  deployment.yaml           # Kubernetes Deployment manifest
  Dockerfile                # Container image definition
  requirements.txt          # Python deps (asyncua, pyyaml)
  README.md                 # Usage & deployment guide
```

---

## OPC-UA Node Hierarchy

The server exposes a single namespace `urn:opcuasim:building`. Nodes follow this tree:

```
Objects/
  Building/
    Metadata/
      building_id           (String)
      building_name         (String)
      total_floors          (Int32)
      sim_start_time        (DateTime)

    Floor-1/
      Zone-Office-A/
        Lighting/
          Light-01/
            status          (Boolean)   — on/off
            brightness_pct  (Float)     — 0–100
            power_w         (Float)     — watts consumed
          Light-02/  ...

        HVAC/
          Unit-01/
            status          (String)    — "heating" | "cooling" | "idle" | "off"
            mode            (String)    — "auto" | "heat" | "cool" | "fan_only"
            setpoint_c      (Float)     — target temperature
            actual_temp_c   (Float)     — supply air temp
            fan_speed_pct   (Float)     — 0–100
            power_kw        (Float)

        Temperature/
          Sensor-01/
            temperature_c   (Float)
            humidity_pct    (Float)
            sensor_status   (String)    — "ok" | "fault"

        DoorLocks/
          Door-01/
            locked          (Boolean)
            door_open       (Boolean)   — physically open/closed (separate from lock)
            last_access     (DateTime)
            lock_reason     (String)    — "schedule" | "manual" | "alarm"

    Floor-2/  ...  (same structure)

    CommonAreas/
      Lobby/
        Lighting/  ...
        Temperature/  ...
        DoorLocks/
          Main-Entrance/  ...
          Side-Door/  ...
      ServerRoom/
        Temperature/
          Sensor-01/        — tighter range, alerts if > 27°C
          Sensor-02/        — redundant sensor
        HVAC/
          Unit-01/          — always "cooling", higher priority
```

---

## Configuration: `building_structure.yaml`

Drives the number of floors, zones per floor, and device counts — mirroring `message_structure.yaml` in edgemqttsim.

```yaml
global:
  building_name: "Contoso HQ"
  update_interval_sec: 5.0        # How often all node values are refreshed
  timezone_offset_hours: -5       # Local time offset from UTC (for schedule logic)

schedule:
  business_hours_start: 8         # 08:00 local — doors unlock, lights come on
  business_hours_end: 18          # 18:00 local — doors lock, lights dim/off

floors:
  - id: 1
    name: "Floor-1"
    zones:
      - id: "Office-A"
        lights: 4
        hvac_units: 1
        temp_sensors: 2
        doors: 2
      - id: "Conference-B"
        lights: 3
        hvac_units: 1
        temp_sensors: 1
        doors: 1

  - id: 2
    name: "Floor-2"
    zones:
      - id: "Office-C"
        lights: 6
        hvac_units: 2
        temp_sensors: 2
        doors: 3

common_areas:
  - id: "Lobby"
    lights: 8
    hvac_units: 1
    temp_sensors: 2
    doors: 3               # Main entrance, side door, fire exit
  - id: "ServerRoom"
    lights: 2
    hvac_units: 2
    temp_sensors: 2        # Redundant sensors
    doors: 1

temperature:
  base_c: 21.0             # Comfortable office baseline
  daily_swing_c: 3.0       # Peak swing over 24-hour sinusoidal cycle
  noise_c: 0.3             # Random noise per update
  server_room_base_c: 22.0
  server_room_max_alert_c: 27.0

lighting:
  daytime_brightness_range: [70, 100]   # % range during business hours
  dimmed_brightness_range: [5, 20]      # % range outside hours / overnight
  flicker_probability: 0.005            # Rare random flicker event per update
```

---

## Simulation Logic

### Temperature (`building.py`)

Uses a **sinusoidal day/night cycle** plus Gaussian noise:

```
temp(t) = base_c
        + daily_swing_c * sin((hour_of_day - 6) * π / 12)
        + random.gauss(0, noise_c)
```

- Peaks around 18:00, troughs around 06:00
- Server room stays in a tighter band; raises an alert flag if threshold exceeded
- Humidity drifts slowly with a random walk bounded to 30–70%

### Door Locks

Rule: **doors are always locked between 18:00 and 08:00 local time** (configurable via `schedule` in YAML).

```
if local_hour < business_hours_start or local_hour >= business_hours_end:
    locked = True
    lock_reason = "schedule"
else:
    locked = False   # unlocked during business hours
    lock_reason = None
```

Additional behaviors:
- `door_open` is a separate boolean — a door can be physically open while locked (alarm condition)
- `last_access` updates randomly during business hours to simulate foot traffic
- Fire exits always remain unlocked (safety override, configurable per door)

### Lighting

- During business hours: brightness drawn from `daytime_brightness_range`, `status = True`
- Outside hours: brightness from `dimmed_brightness_range` for common areas, `status = False` for offices
- Rare flicker: `status` briefly toggles then restores (simulates real-world noise)
- Power derived from brightness: `power_w = max_power_w * (brightness_pct / 100)`

### HVAC

- Tracks `actual_temp_c` from the zone's temperature sensors
- Status cycles: if `actual_temp_c > setpoint_c + 0.5` → `"cooling"`, `< setpoint_c - 0.5` → `"heating"`, else `"idle"`
- Fan speed correlates to heating/cooling demand
- After hours: setpoint relaxes by 3°C (energy saving mode)

---

## Python Dependencies (`requirements.txt`)

```
asyncua>=1.0.0
pyyaml>=6.0
```

`asyncua` is the async OPC-UA server/client library (replaces the older `opcua` sync library). Lightweight, no compiled C deps needed.

---

## `app.py` — Responsibilities

1. **Load config** from `building_structure.yaml`
2. **Instantiate `BuildingSimulator`** (from `building.py`)
3. **Build OPC-UA node tree** — walks config to create all Floor/Zone/Device nodes with typed variables
4. **Start OPC-UA server** on `opc.tcp://0.0.0.0:4840` (standard OPC-UA port)
5. **Update loop** — every `update_interval_sec`, call `BuildingSimulator.tick()` which returns updated values for all nodes, then write each value to its UA variable
6. **Signal handling** — graceful shutdown on SIGTERM (Kubernetes pod termination)

Server endpoint published as:
```
opc.tcp://opcuasim.default.svc.cluster.local:4840/building
```

No authentication on the sim server (security mode: `None`). AIO connects anonymously to the sim for discovery. Production would use certificates.

---

## `building.py` — Responsibilities

- `BuildingSimulator` class initialized from config
- Maintains in-memory state for every simulated device (temperature, lock state, HVAC mode, etc.)
- `tick(utc_now)` — advances simulation by one step, returns a flat dict of `{node_path: value}` ready to write into the OPC-UA tree
- All time-based schedule logic lives here (door lock schedule, HVAC setpoint adjustment)
- No OPC-UA imports — pure simulation, easily unit-testable

---

## `deployment.yaml` — Kubernetes

Same pattern as edgemqttsim:

- Namespace: `default`
- ServiceAccount: `mqtt-client` (existing, already has cluster access)
- Exposes port `4840` via a **ClusterIP Service** so AIO can reach it
- Single replica — state is in-memory, no persistence needed
- Environment variables: `OPCUA_PORT`, `CONFIG_PATH`, `TZ` (timezone for schedule logic)

```yaml
# Service (needed so AIO device endpoint has a stable DNS name)
kind: Service
spec:
  selector:
    app: opcuasim
  ports:
    - port: 4840
      targetPort: 4840
  type: ClusterIP
```

---

## Connecting AIO After the Sim is Running

Once the pod is healthy and the Service is up, create an AIO device endpoint pointing at the sim. No OPC-UA connector config in AIO is needed beforehand.

**Steps (after sim is deployed):**

1. Verify the sim is reachable:
   ```bash
   kubectl run test-opcua --rm -it --image=alpine -- \
     sh -c "apk add nmap; nmap -p 4840 opcuasim.default.svc.cluster.local"
   ```

2. In AIO Portal → **Device endpoints** → **New** → type: **OPC-UA**
   - Endpoint URL: `opc.tcp://opcuasim.default.svc.cluster.local:4840/building`
   - Authentication: Anonymous

3. AIO will **browse the OPC-UA node tree** and discover all Floor/Zone/Device nodes automatically. No manual asset definition required.

4. From the discovered nodes, create **Assets** in AIO Device Registry as needed for dataflows.

---

## Implementation Steps (for next phase)

1. [ ] Create `modules/opcuasim/` directory
2. [ ] Write `building_structure.yaml` (full config with 2 floors + common areas)
3. [ ] Write `building.py` (`BuildingSimulator` class, tick logic, schedule, physics)
4. [ ] Write `app.py` (OPC-UA server, node tree builder, update loop)
5. [ ] Write `Dockerfile` (same base as edgemqttsim — python:3.11-slim + uv)
6. [ ] Write `requirements.txt` (`asyncua`, `pyyaml`)
7. [ ] Write `deployment.yaml` (Deployment + ClusterIP Service)
8. [ ] Write `README.md`
9. [ ] Build and push image via `Deploy-EdgeModules.ps1`
10. [ ] Deploy to cluster, verify pod is Running
11. [ ] Port-forward to test OPC-UA browsing locally: `kubectl port-forward svc/opcuasim 4840:4840`
12. [ ] Create AIO device endpoint → discover nodes → create assets
