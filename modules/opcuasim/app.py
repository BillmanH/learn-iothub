"""
OPC-UA Building Simulator — Server Entrypoint
Builds the OPC-UA node tree from building_structure.yaml and runs a server
that continuously updates all node values to simulate a live smart building.

Connect AIO's OPC-UA device endpoint to:
    opc.tcp://<pod-service>:4840/building
and browse/discover all nodes automatically.
"""

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

from asyncua import Server, ua
from asyncua.server.history import HistoryManager

from building import BuildingSimulator, ZoneState, LightState, HvacState, TempSensorState, DoorState

# ─────────────────────────────────────────────────────────────────────────────
# Configuration from environment variables
# ─────────────────────────────────────────────────────────────────────────────
OPCUA_HOST = os.environ.get('OPCUA_HOST', '0.0.0.0')
OPCUA_PORT = int(os.environ.get('OPCUA_PORT', '4840'))
OPCUA_ENDPOINT_PATH = os.environ.get('OPCUA_ENDPOINT_PATH', '/building')
CONFIG_PATH = os.environ.get('CONFIG_PATH', 'building_structure.yaml')

# Logging
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%SZ',
)
log = logging.getLogger('opcuasim')

# Suppress noisy asyncua internal logging unless DEBUG
if LOG_LEVEL != 'DEBUG':
    logging.getLogger('asyncua').setLevel(logging.WARNING)

# ─────────────────────────────────────────────────────────────────────────────
# Globals
# ─────────────────────────────────────────────────────────────────────────────
# Maps node_path string -> asyncua Variable node
_node_map: dict = {}
# Shutdown signal
_shutdown_event = asyncio.Event()

# Stats
_stats = {
    'ticks': 0,
    'nodes_registered': 0,
    'start_time': None,
}


# ─────────────────────────────────────────────────────────────────────────────
# Signal handling
# ─────────────────────────────────────────────────────────────────────────────
def _handle_signal(signum, frame):
    log.info(f"Received signal {signum}, initiating graceful shutdown...")
    _shutdown_event.set()


# ─────────────────────────────────────────────────────────────────────────────
# Node tree builder helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _get_or_create_folder(parent, browse_name: str):
    """Get or create a folder node under parent."""
    node = await parent.add_folder(browse_name, browse_name)
    return node


async def _add_variable(parent, path: str, browse_name: str,
                        initial_value, ua_type=None):
    """
    Add an OPC-UA variable node and register it in _node_map.
    Infers the UA data type from the Python type of initial_value when ua_type is None.
    """
    type_map = {
        bool: ua.VariantType.Boolean,
        int: ua.VariantType.Int32,
        float: ua.VariantType.Float,
        str: ua.VariantType.String,
        datetime: ua.VariantType.DateTime,
        type(None): ua.VariantType.String,
    }

    if ua_type is None:
        ua_type = type_map.get(type(initial_value), ua.VariantType.String)

    # OPC-UA does not allow None values — coerce to sensible defaults
    if initial_value is None:
        if ua_type == ua.VariantType.String:
            initial_value = ""
        elif ua_type == ua.VariantType.Boolean:
            initial_value = False
        elif ua_type == ua.VariantType.Float:
            initial_value = 0.0
        elif ua_type == ua.VariantType.Int32:
            initial_value = 0
        elif ua_type == ua.VariantType.DateTime:
            initial_value = datetime(2000, 1, 1)

    var = await parent.add_variable(browse_name, browse_name, initial_value)
    await var.set_writable()  # Allow AIO to write to writable nodes
    _node_map[path] = var
    return var


async def _build_light_nodes(parent_folder, zone_path: str, lights):
    """Create OPC-UA nodes for all lights in a zone."""
    lights_folder = await _get_or_create_folder(parent_folder, "Lighting")
    for light in lights:
        light_folder = await _get_or_create_folder(lights_folder, light.path.split('/')[-1])
        p = light.path
        await _add_variable(light_folder, f"{p}/status", "status", light.status)
        await _add_variable(light_folder, f"{p}/brightness_pct", "brightness_pct", light.brightness_pct)
        await _add_variable(light_folder, f"{p}/power_w", "power_w", light.power_w)


async def _build_hvac_nodes(parent_folder, zone_path: str, hvac_units):
    """Create OPC-UA nodes for all HVAC units in a zone."""
    hvac_folder = await _get_or_create_folder(parent_folder, "HVAC")
    for unit in hvac_units:
        unit_folder = await _get_or_create_folder(hvac_folder, unit.path.split('/')[-1])
        p = unit.path
        await _add_variable(unit_folder, f"{p}/status", "status", unit.status)
        await _add_variable(unit_folder, f"{p}/mode", "mode", unit.mode)
        await _add_variable(unit_folder, f"{p}/setpoint_c", "setpoint_c", unit.setpoint_c)
        await _add_variable(unit_folder, f"{p}/actual_temp_c", "actual_temp_c", unit.actual_temp_c)
        await _add_variable(unit_folder, f"{p}/fan_speed_pct", "fan_speed_pct", unit.fan_speed_pct)
        await _add_variable(unit_folder, f"{p}/power_kw", "power_kw", unit.power_kw)


async def _build_temp_sensor_nodes(parent_folder, zone_path: str, sensors):
    """Create OPC-UA nodes for all temperature sensors in a zone."""
    temp_folder = await _get_or_create_folder(parent_folder, "Temperature")
    for sensor in sensors:
        sensor_folder = await _get_or_create_folder(temp_folder, sensor.path.split('/')[-1])
        p = sensor.path
        await _add_variable(sensor_folder, f"{p}/temperature_c", "temperature_c", sensor.temperature_c)
        await _add_variable(sensor_folder, f"{p}/humidity_pct", "humidity_pct", sensor.humidity_pct)
        await _add_variable(sensor_folder, f"{p}/sensor_status", "sensor_status", sensor.sensor_status)


async def _build_door_nodes(parent_folder, zone_path: str, doors):
    """Create OPC-UA nodes for all door locks in a zone."""
    doors_folder = await _get_or_create_folder(parent_folder, "DoorLocks")
    for door in doors:
        door_folder = await _get_or_create_folder(doors_folder, door.path.split('/')[-1])
        p = door.path
        await _add_variable(door_folder, f"{p}/locked", "locked", door.locked)
        await _add_variable(door_folder, f"{p}/door_open", "door_open", door.door_open)
        await _add_variable(door_folder, f"{p}/last_access", "last_access",
                            door.last_access or datetime(2000, 1, 1),
                            ua_type=ua.VariantType.DateTime)
        await _add_variable(door_folder, f"{p}/lock_reason", "lock_reason",
                            door.lock_reason or "")


async def _build_zone_nodes(parent_folder, zone: ZoneState):
    """Build OPC-UA nodes for a single zone."""
    zone_folder = await _get_or_create_folder(parent_folder, zone.zone_id)

    if zone.lights:
        await _build_light_nodes(zone_folder, zone.path, zone.lights)
    if zone.hvac_units:
        await _build_hvac_nodes(zone_folder, zone.path, zone.hvac_units)
    if zone.temp_sensors:
        await _build_temp_sensor_nodes(zone_folder, zone.path, zone.temp_sensors)
    if zone.doors:
        await _build_door_nodes(zone_folder, zone.path, zone.doors)


async def build_node_tree(server: Server, namespace_idx: int, simulator: BuildingSimulator):
    """
    Construct the full OPC-UA node tree from the building simulator's zone list.

    Tree structure:
        Objects/
          Building/
            Metadata/  (static info)
            Floor-1/
              <Zone-A>/
                Lighting/ HVAC/ Temperature/ DoorLocks/
              ...
            Floor-2/  ...
            CommonAreas/
              <Area>/  ...
    """
    objects = server.nodes.objects

    building_folder = await _get_or_create_folder(objects, "Building")

    # ── Metadata ─────────────────────────────────────────────────
    meta_folder = await _get_or_create_folder(building_folder, "Metadata")
    meta = simulator.get_building_metadata()
    await _add_variable(meta_folder, "Building/Metadata/building_id",
                        "building_id", meta['building_id'])
    await _add_variable(meta_folder, "Building/Metadata/building_name",
                        "building_name", meta['building_name'])
    await _add_variable(meta_folder, "Building/Metadata/total_floors",
                        "total_floors", meta['total_floors'],
                        ua_type=ua.VariantType.Int32)
    await _add_variable(meta_folder, "Building/Metadata/total_zones",
                        "total_zones", meta['total_zones'],
                        ua_type=ua.VariantType.Int32)
    await _add_variable(meta_folder, "Building/Metadata/is_business_hours",
                        "is_business_hours", False)
    await _add_variable(meta_folder, "Building/Metadata/local_time",
                        "local_time", datetime.utcnow(),
                        ua_type=ua.VariantType.DateTime)

    # ── Floors ────────────────────────────────────────────────────
    floor_folders: dict = {}
    for floor in simulator.config.get('floors', []):
        fid = f"Floor-{floor['id']}"
        floor_folders[fid] = await _get_or_create_folder(building_folder, fid)

    # ── Common areas ─────────────────────────────────────────────
    common_folder = await _get_or_create_folder(building_folder, "CommonAreas")

    # ── Zones ─────────────────────────────────────────────────────
    for zone in simulator.get_zone_list():
        # Determine parent folder from path prefix
        parts = zone.path.split('/')  # e.g. ['Building', 'Floor-1', 'Office-A']
        if len(parts) >= 2 and parts[1].startswith('Floor-'):
            parent = floor_folders.get(parts[1], building_folder)
        else:
            parent = common_folder

        await _build_zone_nodes(parent, zone)

    _stats['nodes_registered'] = len(_node_map)
    log.info(f"Node tree built: {len(_node_map)} variable nodes registered")


# ─────────────────────────────────────────────────────────────────────────────
# Value update loop
# ─────────────────────────────────────────────────────────────────────────────

async def update_loop(simulator: BuildingSimulator):
    """Continuously tick the simulation and write updated values to OPC-UA nodes."""
    interval = simulator.get_update_interval()
    log.info(f"Starting update loop — interval: {interval}s")

    last_stats_log = asyncio.get_event_loop().time()

    while not _shutdown_event.is_set():
        tick_start = asyncio.get_event_loop().time()

        utc_now = datetime.now(timezone.utc)
        values = simulator.tick(utc_now)

        # Write all updated values to their OPC-UA nodes
        write_errors = 0
        for path, value in values.items():
            node = _node_map.get(path)
            if node is None:
                continue  # Node not yet registered (shouldn't happen)
            try:
                # Coerce None to empty string for string nodes
                if value is None:
                    value = ""
                await node.write_value(value)
            except Exception as e:
                write_errors += 1
                if write_errors <= 3:  # Cap noisy error output
                    log.warning(f"Write failed for {path}: {e}")

        _stats['ticks'] += 1

        # Periodic stats log
        now = asyncio.get_event_loop().time()
        if now - last_stats_log >= 60.0:
            uptime = now - _stats['start_time']
            log.info(
                f"Stats — ticks: {_stats['ticks']}, "
                f"nodes: {_stats['nodes_registered']}, "
                f"uptime: {uptime:.0f}s"
            )
            last_stats_log = now

        # Sleep for the remainder of the interval
        elapsed = asyncio.get_event_loop().time() - tick_start
        sleep_time = max(0.0, interval - elapsed)
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=sleep_time)
        except asyncio.TimeoutError:
            pass  # Normal — just means we didn't get a shutdown signal


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    log.info("=" * 70)
    log.info("OPC-UA Building Simulator — Azure IoT Operations")
    log.info(f"  Config:   {CONFIG_PATH}")
    log.info(f"  Endpoint: opc.tcp://{OPCUA_HOST}:{OPCUA_PORT}{OPCUA_ENDPOINT_PATH}")
    log.info("=" * 70)

    # ── Initialise simulator ─────────────────────────────────────
    log.info("Loading building configuration...")
    try:
        simulator = BuildingSimulator(CONFIG_PATH)
    except FileNotFoundError as e:
        log.error(f"Config file not found: {e}")
        sys.exit(1)

    meta = simulator.get_building_metadata()
    log.info(f"Building: {meta['building_name']} ({meta['building_id']})")
    log.info(f"  Floors: {meta['total_floors']}, Zones: {meta['total_zones']}")

    # ── Create OPC-UA server ─────────────────────────────────────
    server = Server()
    await server.init()

    endpoint_url = f"opc.tcp://{OPCUA_HOST}:{OPCUA_PORT}{OPCUA_ENDPOINT_PATH}"
    server.set_endpoint(endpoint_url)
    server.set_server_name(f"OPC-UA Building Sim — {meta['building_name']}")

    # Security: None/None (anonymous, no encryption) — appropriate for sim
    # Production would add certificate-based security policies here
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])

    # Register a custom namespace
    namespace_uri = "urn:opcuasim:building"
    namespace_idx = await server.register_namespace(namespace_uri)
    log.info(f"Namespace registered: {namespace_uri} (idx={namespace_idx})")

    # ── Build node tree ──────────────────────────────────────────
    log.info("Building OPC-UA node tree...")
    await build_node_tree(server, namespace_idx, simulator)

    # ── Start server ─────────────────────────────────────────────
    _stats['start_time'] = asyncio.get_event_loop().time()

    async with server:
        log.info(f"OPC-UA server started on {endpoint_url}")
        log.info("Ready for connections. Browse nodes with any OPC-UA client.")
        log.info("  AIO device endpoint URL:")
        log.info(f"  opc.tcp://opcuasim.default.svc.cluster.local:{OPCUA_PORT}{OPCUA_ENDPOINT_PATH}")

        # Run update loop until shutdown
        await update_loop(simulator)

    log.info("Server stopped cleanly.")


if __name__ == '__main__':
    # Register signal handlers for graceful Kubernetes pod termination
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    asyncio.run(main())
