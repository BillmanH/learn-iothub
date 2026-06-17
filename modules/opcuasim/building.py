"""
Building Simulation Module for OPC-UA Building Simulator.
Pure simulation logic — no OPC-UA imports. Easily unit-testable.

Simulates: lighting, HVAC, temperature sensors, door locks
for a multi-floor commercial building.
"""

import math
import random
import yaml
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple


class DoorState:
    """Tracks the state of a single door lock."""

    def __init__(self, door_cfg: dict, zone_path: str):
        self.door_id = door_cfg['id']
        self.name = door_cfg['name']
        self.fire_exit = door_cfg.get('fire_exit', False)
        self.path = f"{zone_path}/DoorLocks/{self.door_id}"

        self.locked: bool = True
        self.door_open: bool = False
        self.last_access: Optional[datetime] = None
        self.lock_reason: str = "schedule"

    def as_dict(self) -> dict:
        return {
            f"{self.path}/locked": self.locked,
            f"{self.path}/door_open": self.door_open,
            f"{self.path}/last_access": self.last_access,
            f"{self.path}/lock_reason": self.lock_reason,
        }


class LightState:
    """Tracks the state of a single light fixture."""

    def __init__(self, light_id: str, zone_path: str, max_power_w: float):
        self.path = f"{zone_path}/Lighting/{light_id}"
        self.max_power_w = max_power_w

        self.status: bool = False
        self.brightness_pct: float = 0.0
        self.power_w: float = 0.0

    def as_dict(self) -> dict:
        return {
            f"{self.path}/status": self.status,
            f"{self.path}/brightness_pct": round(self.brightness_pct, 1),
            f"{self.path}/power_w": round(self.power_w, 1),
        }


class HvacState:
    """Tracks the state of a single HVAC unit."""

    def __init__(self, unit_id: str, zone_path: str):
        self.path = f"{zone_path}/HVAC/{unit_id}"

        self.status: str = "idle"       # heating | cooling | idle | off
        self.mode: str = "auto"         # auto | heat | cool | fan_only
        self.setpoint_c: float = 21.5
        self.actual_temp_c: float = 21.5
        self.fan_speed_pct: float = 15.0
        self.power_kw: float = 0.3

    def as_dict(self) -> dict:
        return {
            f"{self.path}/status": self.status,
            f"{self.path}/mode": self.mode,
            f"{self.path}/setpoint_c": round(self.setpoint_c, 1),
            f"{self.path}/actual_temp_c": round(self.actual_temp_c, 1),
            f"{self.path}/fan_speed_pct": round(self.fan_speed_pct, 1),
            f"{self.path}/power_kw": round(self.power_kw, 2),
        }


class TempSensorState:
    """Tracks the state of a single temperature/humidity sensor."""

    def __init__(self, sensor_id: str, zone_path: str):
        self.path = f"{zone_path}/Temperature/{sensor_id}"

        self.temperature_c: float = 21.0
        self.humidity_pct: float = 45.0
        self.sensor_status: str = "ok"   # ok | fault

    def as_dict(self) -> dict:
        return {
            f"{self.path}/temperature_c": round(self.temperature_c, 2),
            f"{self.path}/humidity_pct": round(self.humidity_pct, 1),
            f"{self.path}/sensor_status": self.sensor_status,
        }


class ZoneState:
    """Aggregates all device states for a zone."""

    def __init__(self, zone_cfg: dict, parent_path: str, global_cfg: dict,
                 lighting_cfg: dict, hvac_cfg: dict):
        self.zone_id = zone_cfg['id']
        self.path = f"{parent_path}/{self.zone_id}"
        self.is_server_room = zone_cfg.get('is_server_room', False)

        max_power_w = lighting_cfg.get('max_power_w', 60.0)

        # Lights
        self.lights: List[LightState] = [
            LightState(f"Light-{i:02d}", self.path, max_power_w)
            for i in range(1, zone_cfg.get('lights', 0) + 1)
        ]

        # HVAC units
        self.hvac_units: List[HvacState] = [
            HvacState(f"Unit-{i:02d}", self.path)
            for i in range(1, zone_cfg.get('hvac_units', 0) + 1)
        ]

        # Temperature sensors
        self.temp_sensors: List[TempSensorState] = [
            TempSensorState(f"Sensor-{i:02d}", self.path)
            for i in range(1, zone_cfg.get('temp_sensors', 0) + 1)
        ]

        # Door locks — zones may provide a list of door dicts or just a count
        doors_cfg = zone_cfg.get('doors', [])
        self.doors: List[DoorState] = [
            DoorState(d, self.path) for d in doors_cfg
        ]

        # Stable humidity per sensor (random walk base)
        self._humidity_base = [random.uniform(40.0, 55.0)
                               for _ in self.temp_sensors]

    def as_dict(self) -> dict:
        values: dict = {}
        for light in self.lights:
            values.update(light.as_dict())
        for unit in self.hvac_units:
            values.update(unit.as_dict())
        for sensor in self.temp_sensors:
            values.update(sensor.as_dict())
        for door in self.doors:
            values.update(door.as_dict())
        return values


class BuildingSimulator:
    """
    Top-level building simulator.

    Call tick(utc_now) each update cycle to advance the simulation.
    Returns a flat dict of {node_path: value} ready to write into OPC-UA nodes.
    """

    def __init__(self, config_path: str = "building_structure.yaml"):
        self.config = self._load_config(config_path)
        self.global_cfg = self.config.get('global', {})
        self.schedule_cfg = self.config.get('schedule', {})
        self.temp_cfg = self.config.get('temperature', {})
        self.lighting_cfg = self.config.get('lighting', {})
        self.hvac_cfg = self.config.get('hvac', {})
        self.door_cfg = self.config.get('doors', {})

        self.building_name: str = self.global_cfg.get('building_name', 'Building')
        self.building_id: str = self.global_cfg.get('building_id', 'BLD-001')
        self.update_interval: float = self.global_cfg.get('update_interval_sec', 5.0)
        self.tz_offset: int = int(self.global_cfg.get('timezone_offset_hours', 0))

        self.business_start: int = self.schedule_cfg.get('business_hours_start', 8)
        self.business_end: int = self.schedule_cfg.get('business_hours_end', 18)

        self.zones: List[ZoneState] = []
        self._build_zones()

    # ─────────────────────────────────────────────────────────────
    # Initialisation helpers
    # ─────────────────────────────────────────────────────────────

    def _load_config(self, config_path: str) -> dict:
        cfg_file = Path(config_path)
        if not cfg_file.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
        with open(cfg_file, 'r') as f:
            return yaml.safe_load(f)

    def _build_zones(self):
        """Create ZoneState objects for every zone in the building layout."""
        # Floors
        for floor in self.config.get('floors', []):
            floor_path = f"Building/Floor-{floor['id']}"
            for zone_cfg in floor.get('zones', []):
                self.zones.append(ZoneState(
                    zone_cfg, floor_path,
                    self.global_cfg, self.lighting_cfg, self.hvac_cfg
                ))

        # Common areas
        for area_cfg in self.config.get('common_areas', []):
            area_path = "Building/CommonAreas"
            self.zones.append(ZoneState(
                area_cfg, area_path,
                self.global_cfg, self.lighting_cfg, self.hvac_cfg
            ))

    # ─────────────────────────────────────────────────────────────
    # Public interface
    # ─────────────────────────────────────────────────────────────

    def get_update_interval(self) -> float:
        return self.update_interval

    def tick(self, utc_now: Optional[datetime] = None) -> dict:
        """
        Advance the simulation by one step.
        Returns a flat dict of {node_path: value} for all building nodes.
        """
        if utc_now is None:
            utc_now = datetime.now(timezone.utc)

        local_now = utc_now + timedelta(hours=self.tz_offset)
        local_hour = local_now.hour + local_now.minute / 60.0
        is_business_hours = self.business_start <= local_hour < self.business_end

        values: dict = {}

        # Metadata
        values["Building/Metadata/building_id"] = self.building_id
        values["Building/Metadata/building_name"] = self.building_name
        values["Building/Metadata/is_business_hours"] = is_business_hours
        values["Building/Metadata/local_time"] = local_now.replace(tzinfo=None)

        for zone in self.zones:
            self._update_zone(zone, local_hour, is_business_hours, utc_now)
            values.update(zone.as_dict())

        return values

    # ─────────────────────────────────────────────────────────────
    # Zone update logic
    # ─────────────────────────────────────────────────────────────

    def _update_zone(self, zone: ZoneState, local_hour: float,
                     is_business_hours: bool, utc_now: datetime):
        """Update all devices in a zone for the current tick."""
        temp = self._compute_zone_temperature(zone, local_hour)

        self._update_temperature_sensors(zone, local_hour)
        self._update_hvac(zone, temp, is_business_hours)
        self._update_lighting(zone, is_business_hours)
        self._update_doors(zone, is_business_hours, utc_now)

    # ─────────────────────────────────────────────────────────────
    # Temperature
    # ─────────────────────────────────────────────────────────────

    def _compute_zone_temperature(self, zone: ZoneState, local_hour: float) -> float:
        """Compute the current ambient temperature for a zone using sinusoidal model."""
        if zone.is_server_room:
            base = self.temp_cfg.get('server_room_base_c', 22.0)
            swing = self.temp_cfg.get('server_room_swing_c', 1.0)
            noise_std = self.temp_cfg.get('server_room_noise_c', 0.1)
        else:
            base = self.temp_cfg.get('base_c', 21.0)
            swing = self.temp_cfg.get('daily_swing_c', 3.0)
            noise_std = self.temp_cfg.get('noise_c', 0.3)

        # Sinusoidal: peaks ~18:00, troughs ~06:00
        angle = (local_hour - 6.0) * math.pi / 12.0
        temp = base + swing * math.sin(angle) + random.gauss(0, noise_std)
        return round(temp, 2)

    def _update_temperature_sensors(self, zone: ZoneState, local_hour: float):
        """Update temp/humidity readings for each sensor in the zone."""
        noise_std = (self.temp_cfg.get('server_room_noise_c', 0.1)
                     if zone.is_server_room
                     else self.temp_cfg.get('noise_c', 0.3))

        base_temp = self._compute_zone_temperature(zone, local_hour)

        hum_drift = self.temp_cfg.get('humidity_drift_max', 0.5)
        hum_min = self.temp_cfg.get('humidity_min_pct', 30.0)
        hum_max = self.temp_cfg.get('humidity_max_pct', 70.0)

        for i, sensor in enumerate(zone.temp_sensors):
            # Small per-sensor offset so redundant sensors aren't identical
            offset = random.gauss(0, noise_std * 0.5)
            sensor.temperature_c = round(base_temp + offset, 2)

            # Humidity random walk
            drift = random.uniform(-hum_drift, hum_drift)
            zone._humidity_base[i] = max(hum_min, min(hum_max,
                                         zone._humidity_base[i] + drift))
            sensor.humidity_pct = round(zone._humidity_base[i], 1)

            # Server room alert: mark sensor_status if over threshold
            if zone.is_server_room:
                alert_threshold = self.temp_cfg.get('server_room_alert_threshold_c', 27.0)
                sensor.sensor_status = "alert" if sensor.temperature_c >= alert_threshold else "ok"
            else:
                sensor.sensor_status = "ok"

    # ─────────────────────────────────────────────────────────────
    # HVAC
    # ─────────────────────────────────────────────────────────────

    def _update_hvac(self, zone: ZoneState, ambient_temp: float, is_business_hours: bool):
        """Update HVAC unit states based on zone temperature and occupancy."""
        if not zone.hvac_units:
            return

        deadband = self.hvac_cfg.get('deadband_c', 0.5)
        fan_active_range = self.hvac_cfg.get('fan_speed_active_range', [40, 90])
        fan_idle = self.hvac_cfg.get('fan_speed_idle', 15)
        p_heat = self.hvac_cfg.get('power_heating_kw', 3.5)
        p_cool = self.hvac_cfg.get('power_cooling_kw', 4.2)
        p_idle = self.hvac_cfg.get('power_idle_kw', 0.3)
        sp_occ = self.hvac_cfg.get('setpoint_occupied_c', 21.5)
        sp_unocc = self.hvac_cfg.get('setpoint_unoccupied_c', 18.5)

        setpoint = sp_occ if is_business_hours else sp_unocc

        for unit in zone.hvac_units:
            unit.setpoint_c = setpoint
            unit.actual_temp_c = ambient_temp

            if ambient_temp > setpoint + deadband:
                unit.status = "cooling"
                unit.fan_speed_pct = round(random.uniform(*fan_active_range), 1)
                unit.power_kw = p_cool
            elif ambient_temp < setpoint - deadband:
                unit.status = "heating"
                unit.fan_speed_pct = round(random.uniform(*fan_active_range), 1)
                unit.power_kw = p_heat
            else:
                unit.status = "idle"
                unit.fan_speed_pct = float(fan_idle)
                unit.power_kw = p_idle

            unit.mode = "auto"

    # ─────────────────────────────────────────────────────────────
    # Lighting
    # ─────────────────────────────────────────────────────────────

    def _update_lighting(self, zone: ZoneState, is_business_hours: bool):
        """Update lighting state based on schedule and flicker probability."""
        day_range = self.lighting_cfg.get('daytime_brightness_range', [70, 100])
        dim_range = self.lighting_cfg.get('dimmed_brightness_range', [5, 20])
        max_power = self.lighting_cfg.get('max_power_w', 60.0)
        flicker_prob = self.lighting_cfg.get('flicker_probability', 0.005)

        for light in zone.lights:
            # Rare flicker event
            if random.random() < flicker_prob:
                light.status = not light.status
                light.brightness_pct = 0.0 if not light.status else light.brightness_pct
                light.power_w = 0.0 if not light.status else light.power_w
                continue

            if is_business_hours:
                light.status = True
                light.brightness_pct = round(random.uniform(*day_range), 1)
            else:
                # Offices go fully off; common areas dim
                if zone.is_server_room:
                    # Server room always has minimal lighting on
                    light.status = True
                    light.brightness_pct = round(random.uniform(*dim_range), 1)
                else:
                    light.status = False
                    light.brightness_pct = 0.0

            light.power_w = round(max_power * (light.brightness_pct / 100.0), 1)

    # ─────────────────────────────────────────────────────────────
    # Door Locks
    # ─────────────────────────────────────────────────────────────

    def _update_doors(self, zone: ZoneState, is_business_hours: bool,
                      utc_now: datetime):
        """
        Update door lock states.

        Rule: doors are ALWAYS locked outside business hours (18:00–08:00 local).
        Fire exits are ALWAYS unlocked (safety override).
        """
        access_prob = self.door_cfg.get('access_event_probability', 0.08)
        open_prob = self.door_cfg.get('open_probability', 0.05)

        for door in zone.doors:
            if door.fire_exit:
                # Safety override — fire exits are never locked
                door.locked = False
                door.lock_reason = "fire_exit"
                # Fire exits may still open briefly
                door.door_open = random.random() < (open_prob * 0.1)
            elif not is_business_hours:
                # Schedule lock — after hours ALL non-fire-exit doors lock
                door.locked = True
                door.lock_reason = "schedule"
                door.door_open = False  # No one should be opening locked doors
            else:
                # Business hours — unlocked
                door.locked = False
                door.lock_reason = None

                # Simulate access events
                if random.random() < access_prob:
                    door.last_access = utc_now.replace(tzinfo=None)

                # Simulate door briefly opening/closing
                door.door_open = random.random() < open_prob

    # ─────────────────────────────────────────────────────────────
    # Introspection helpers (used by app.py to build the node tree)
    # ─────────────────────────────────────────────────────────────

    def get_zone_list(self) -> List[ZoneState]:
        """Return all zone states (used by app.py to build OPC-UA node tree)."""
        return self.zones

    def get_building_metadata(self) -> dict:
        """Return static building metadata fields."""
        return {
            'building_id': self.building_id,
            'building_name': self.building_name,
            'total_floors': len(self.config.get('floors', [])),
            'common_areas': len(self.config.get('common_areas', [])),
            'total_zones': len(self.zones),
        }
