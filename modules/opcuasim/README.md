# OPC-UA Building Simulator

Containerized OPC-UA server that simulates a smart building: lighting, HVAC, temperature sensors, and door locks across multiple floors and zones.

Built for **Azure IoT Operations** — deploy it to the cluster, point an AIO OPC-UA device endpoint at it, and AIO will browse and discover all nodes automatically.

---

## Architecture

| File | Purpose |
|---|---|
| `app.py` | OPC-UA server, node tree builder, async update loop |
| `building.py` | Pure simulation logic — temperature physics, door schedules, HVAC control |
| `building_structure.yaml` | Configuration: floors, zones, device counts, parameters |
| `deployment.yaml` | Kubernetes Deployment + ClusterIP Service |
| `Dockerfile` | Container image (python:3.11-slim + uv) |
| `requirements.txt` | `asyncua`, `pyyaml` |

---

## OPC-UA Node Tree

```
Objects/
  Building/
    Metadata/
      building_id, building_name, total_floors, total_zones,
      is_business_hours, local_time
    Floor-1/
      Office-A/
        Lighting/
          Light-01/ — status (bool), brightness_pct (float), power_w (float)
          Light-02/ ...
        HVAC/
          Unit-01/ — status, mode, setpoint_c, actual_temp_c, fan_speed_pct, power_kw
        Temperature/
          Sensor-01/ — temperature_c, humidity_pct, sensor_status
        DoorLocks/
          Door-01/ — locked (bool), door_open (bool), last_access (DateTime), lock_reason
      ...
    Floor-2/ ...
    CommonAreas/
      Lobby/ ...
      ServerRoom/ ...
      Parking-Garage/ ...
```

AIO discovers this entire tree via OPC-UA Browse — no manual node configuration needed.

---

## Simulation Behaviour

### Temperature
Sinusoidal 24-hour cycle peaking ~18:00, troughing ~06:00, with Gaussian noise. Server room uses a tighter range with a configurable alert threshold.

### Door Locks
- **Locked:** outside business hours (default: before 08:00 and from 18:00 local time)
- **Unlocked:** during business hours with simulated access events
- **Fire exits:** always unlocked regardless of schedule (safety override, per-door flag in YAML)

### Lighting
Full brightness during business hours; offices dark after hours; common areas dim. Rare random flicker events simulate real-world noise.

### HVAC
Tracks zone temperature against a setpoint. Switches between `heating`, `cooling`, and `idle` based on a configurable dead-band. Setpoint relaxes after hours for energy saving.

---

## Deployment

### 1. Build and push the image

Use the existing `Deploy-EdgeModules.ps1` script:

```powershell
cd external_configuration
.\Deploy-EdgeModules.ps1 -AppFolder "opcuasim" -RegistryName "<your-acr-name>"
```

### 2. Update the image reference

Edit `deployment.yaml` and replace `<YOUR_REGISTRY>` with your ACR login server:

```yaml
image: <your-acr>.azurecr.io/opcuasim:latest
```

### 3. Deploy to the cluster

```bash
kubectl apply -f modules/opcuasim/deployment.yaml
```

### 4. Verify the pod is running

```bash
kubectl get pods -l app=opcuasim
kubectl logs -l app=opcuasim -f
```

Expected log output:
```
OPC-UA Building Simulator — Azure IoT Operations
  Endpoint: opc.tcp://0.0.0.0:4840/building
Building: Contoso HQ (BLD-001)
  Floors: 2, Zones: 7
Node tree built: 148 variable nodes registered
OPC-UA server started on opc.tcp://0.0.0.0:4840/building
```

### 5. Test locally via port-forward

```bash
kubectl port-forward svc/opcuasim 4840:4840
```

Then connect any OPC-UA client (e.g. UaExpert, Prosys OPC-UA Browser) to:
```
opc.tcp://localhost:4840/building
```
Browse the tree to confirm all nodes are visible and updating.

---

## Connecting AIO

Once the pod is Running and the Service is confirmed reachable:

1. **AIO Portal** → Connector configurations → OPC-UA connector (create if not present)
2. **Device endpoints** → New → Type: **OPC-UA**
   - Endpoint URL: `opc.tcp://opcuasim.default.svc.cluster.local:4840/building`
   - Authentication: **Anonymous**
3. AIO browses the node tree and discovers all Floor/Zone/Device nodes
4. From discovered nodes, create **Assets** for any nodes you want to route through dataflows

---

## Configuration

Key settings in `building_structure.yaml`:

| Setting | Default | Description |
|---|---|---|
| `global.update_interval_sec` | `5.0` | How often all node values refresh |
| `global.timezone_offset_hours` | `-5` | UTC offset for schedule logic |
| `schedule.business_hours_start` | `8` | Doors unlock, lights on full |
| `schedule.business_hours_end` | `18` | Doors lock, offices go dark |
| `temperature.base_c` | `21.0` | Office baseline temperature |
| `temperature.daily_swing_c` | `3.0` | Peak-to-trough swing over 24h |
| `temperature.server_room_alert_threshold_c` | `27.0` | Temp that sets sensor_status = "alert" |

Add floors, zones, and devices by editing the `floors` and `common_areas` sections. The node tree is built dynamically from the YAML — no code changes needed for layout changes.
