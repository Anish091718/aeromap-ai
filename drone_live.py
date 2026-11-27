"""Live drone telemetry hub.

One DroneHub exists per logged-in user. It receives telemetry from one source at a time:

  simulator - built-in demo drone flying a survey pattern (no hardware needed)
  mavlink   - real drone / SITL / ground station via pymavlink (ArduPilot, PX4)
  http      - any external app that POSTs JSON to /api/drone/push with an API token
  browser   - this device's GPS (phone/laptop) pushed by the Live page itself

The web page polls snapshot() about once per second.
"""
import math
import re
import threading
import time
import uuid
from collections import deque

from config import DRONE_STALE_SECONDS, DRONE_TRACK_MAX_POINTS

EARTH_RADIUS_M = 6371000.0
SOURCES = ("simulator", "mavlink", "http", "browser")

# Only these connection strings are accepted, so users cannot make the server
# open arbitrary files or devices.
MAVLINK_CONN_RE = re.compile(
    r"^(?:"
    r"(?:udpin|udpout|udp|tcpin|tcp):[A-Za-z0-9_.\-]+:\d{2,5}"
    r"|COM\d{1,3}(?:,\d{3,7})?"
    r"|/dev/tty[A-Za-z0-9_.]+(?:,\d{3,7})?"
    r")$"
)


# ---------------------------------------------------------------- geometry
def haversine(lat1, lon1, lat2, lon2):
    """Distance in metres between two lat/lon points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def bearing(lat1, lon1, lat2, lon2):
    """Compass bearing in degrees (0 = north) from point 1 to point 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


# -------------------------------------------------------------- validation
def _num(data, key, lo, hi):
    value = data.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"'{key}' must be a number")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"'{key}' must be a number")
    if not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f"'{key}' is out of range")
    return value


def parse_fields(data):
    """Validate a telemetry dict (from HTTP push) and return clean values."""
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    fields = {
        "lat": _num(data, "lat", -90, 90),
        "lon": _num(data, "lon", -180, 180),
        "alt": _num(data, "alt", -1000, 100000),
        "rel_alt": _num(data, "rel_alt", -1000, 100000),
        "heading": _num(data, "heading", 0, 360),
        "speed": _num(data, "speed", 0, 1000),
        "battery": _num(data, "battery", 0, 100),
        "voltage": _num(data, "voltage", 0, 1000),
        "sats": _num(data, "sats", 0, 255),
    }
    if fields["heading"] is not None:
        fields["heading"] %= 360.0
    if isinstance(data.get("mode"), str):
        fields["mode"] = data["mode"][:24]
    if isinstance(data.get("armed"), bool):
        fields["armed"] = data["armed"]
    return {k: v for k, v in fields.items() if v is not None}


def validate_connection_string(conn):
    conn = (conn or "").strip()
    if not MAVLINK_CONN_RE.match(conn):
        raise ValueError(
            "Invalid connection string. Examples: udpin:0.0.0.0:14550, "
            "tcp:127.0.0.1:5760, COM5,57600"
        )
    return conn


# --------------------------------------------------------------------- hub
class DroneHub:
    def __init__(self):
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._reset()

    def _reset(self):
        self.run_id = uuid.uuid4().hex[:8]
        self.source = None
        self.status = "disconnected"   # disconnected | connecting | waiting | live | error
        self.message = ""
        self.telemetry = {}
        self.track = deque(maxlen=DRONE_TRACK_MAX_POINTS)   # (seq, lat, lon, height)
        self.seq = 0
        self.last_update = 0.0
        self.last_pos = None
        self.distance = 0.0
        self.started_at = None

    # ---- connection control
    def connect(self, source, connection="", lat=None, lon=None):
        if source not in SOURCES:
            raise ValueError("Unknown source")

        if source == "mavlink":
            connection = validate_connection_string(connection)
        if source == "simulator":
            try:
                lat, lon = float(lat), float(lon)
            except (TypeError, ValueError):
                raise ValueError("The simulator needs a start position")
            if not (-85 <= lat <= 85 and -180 <= lon <= 180):
                raise ValueError("Start position is out of range")

        self.disconnect()

        with self._lock:
            self._reset()
            self.source = source
            self.started_at = time.time()
            self._stop = threading.Event()
            run_id, stop = self.run_id, self._stop

            if source == "simulator":
                self.status, self.message = "connecting", "Starting simulator..."
                target, args = self._run_simulator, (run_id, stop, lat, lon)
            elif source == "mavlink":
                self.status, self.message = "connecting", "Connecting to " + connection
                target, args = self._run_mavlink, (run_id, stop, connection)
            else:
                self.status = "waiting"
                self.message = ("Waiting for this device's GPS..." if source == "browser"
                                else "Waiting for data from your app...")
                target, args = None, ()

            if target:
                self._thread = threading.Thread(target=target, args=args, daemon=True)
                self._thread.start()

    def disconnect(self):
        with self._lock:
            self._stop.set()
            thread, self._thread = self._thread, None
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3)
        with self._lock:
            self.status = "disconnected"
            self.message = ""
            self.source = None

    # ---- data in
    def push(self, data):
        """Telemetry from the HTTP API or the browser GPS."""
        with self._lock:
            if self.source not in ("http", "browser"):
                raise ValueError(
                    "Select 'External app' or 'This device GPS' on the Live page and press Connect first."
                )
        fields = parse_fields(data)
        if "lat" not in fields or "lon" not in fields:
            raise ValueError("'lat' and 'lon' are required")
        self._ingest(fields)

    def _ingest(self, fields, run_id=None):
        with self._lock:
            if run_id is not None and run_id != self.run_id:
                return  # a stale worker from an older connection
            fields = dict(fields)
            lat, lon = fields.get("lat"), fields.get("lon")

            if lat is not None and lon is not None:
                moved = None
                if self.last_pos:
                    moved = haversine(self.last_pos[0], self.last_pos[1], lat, lon)
                    if fields.get("heading") is None and moved > 1.0:
                        fields["heading"] = bearing(self.last_pos[0], self.last_pos[1], lat, lon)

                if self.last_pos is None or moved >= 0.3:
                    height = fields.get("rel_alt", fields.get("alt", 0.0))
                    self.seq += 1
                    self.track.append((self.seq, round(lat, 7), round(lon, 7), round(height, 1)))
                    if moved:
                        self.distance += moved
                    self.last_pos = (lat, lon)

                self.last_update = time.time()
                self.status, self.message = "live", ""

            self.telemetry.update(fields)

    def _set_message(self, run_id, text):
        with self._lock:
            if run_id == self.run_id and self.status in ("connecting", "waiting"):
                self.message = text

    def _fail(self, run_id, text):
        with self._lock:
            if run_id == self.run_id:
                self.status, self.message = "error", text

    # ---- data out
    def snapshot(self, since=0):
        with self._lock:
            now = time.time()
            age = (now - self.last_update) if self.last_update else None
            status = self.status
            if status == "live" and age is not None and age > DRONE_STALE_SECONDS:
                status = "stale"
            points = [list(p) for p in self.track if p[0] > since] if since < self.seq else []
            return {
                "run": self.run_id,
                "source": self.source,
                "status": status,
                "message": self.message,
                "telemetry": dict(self.telemetry),
                "age": age,
                "seq": self.seq,
                "points": points,
                "distance": round(self.distance, 1),
                "flight_time": int(now - self.started_at) if self.started_at else 0,
            }

    def geojson(self):
        with self._lock:
            coords = [[p[2], p[1], p[3]] for p in self.track]
        features = []
        if len(coords) >= 2:
            features.append({
                "type": "Feature",
                "properties": {"name": "Flight path"},
                "geometry": {"type": "LineString", "coordinates": coords},
            })
        if coords:
            features.append({
                "type": "Feature",
                "properties": {"name": "Last position"},
                "geometry": {"type": "Point", "coordinates": coords[-1]},
            })
        return {"type": "FeatureCollection", "features": features}

    # ---- workers
    def _run_simulator(self, run_id, stop, lat0, lon0):
        """Demo drone flying a lawn-mower survey pattern around the start point."""
        rows, length, spacing = 6, 200.0, 25.0   # metres
        pts = []
        for r in range(rows):
            y = r * spacing
            xs = (0.0, length) if r % 2 == 0 else (length, 0.0)
            pts += [(xs[0], y), (xs[1], y)]
        pts = [(x - length / 2, y - (rows - 1) * spacing / 2) for x, y in pts]

        x, y = pts[0]
        idx, speed, dt = 1, 8.0, 0.5
        battery, t0 = 100.0, time.time()
        m_per_deg_lat = 111320.0
        m_per_deg_lon = 111320.0 * math.cos(math.radians(lat0))

        while not stop.wait(dt):
            tx, ty = pts[idx]
            dx, dy = tx - x, ty - y
            dist = math.hypot(dx, dy)
            step = speed * dt
            if dist <= step:
                x, y = tx, ty
                idx = (idx + 1) % len(pts)
            else:
                x += dx / dist * step
                y += dy / dist * step

            height = 60.0 + 2.0 * math.sin((time.time() - t0) / 5.0)
            battery = max(5.0, battery - 0.02)
            self._ingest({
                "lat": lat0 + y / m_per_deg_lat,
                "lon": lon0 + x / m_per_deg_lon,
                "alt": 300.0 + height,
                "rel_alt": height,
                "heading": (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0 if dist > 0.01 else None,
                "speed": speed,
                "battery": round(battery, 1),
                "voltage": round(14.8 + battery / 100.0 * 2.0, 2),
                "sats": 14,
                "mode": "AUTO (sim)",
                "armed": True,
            }, run_id)

    def _run_mavlink(self, run_id, stop, conn):
        master = None
        try:
            try:
                from pymavlink import mavutil
            except ImportError:
                self._fail(run_id, "pymavlink is not installed. Run: pip install pymavlink pyserial")
                return

            device, kwargs = conn, {}
            if "," in conn:
                device, baud = conn.split(",", 1)
                kwargs["baud"] = int(baud)

            master = mavutil.mavlink_connection(device, autoreconnect=True, **kwargs)

            self._set_message(run_id, "Waiting for heartbeat...")
            deadline, got_heartbeat = time.time() + 20, False
            while not stop.is_set() and time.time() < deadline:
                if master.recv_match(type="HEARTBEAT", blocking=True, timeout=1):
                    got_heartbeat = True
                    break
            if stop.is_set():
                return
            if not got_heartbeat:
                self._fail(run_id, "No MAVLink heartbeat within 20 seconds. "
                                   "Check the connection string, port and drone power.")
                return

            try:   # ask the autopilot to stream telemetry at 4 Hz
                master.mav.request_data_stream_send(
                    master.target_system, master.target_component,
                    mavutil.mavlink.MAV_DATA_STREAM_ALL, 4, 1)
            except Exception:
                pass
            self._set_message(run_id, "Heartbeat OK, waiting for GPS position...")

            while not stop.is_set():
                msg = master.recv_match(blocking=True, timeout=1)
                if msg is None:
                    continue
                kind = msg.get_type()
                fields = {}

                if kind == "GLOBAL_POSITION_INT":
                    if msg.lat == 0 and msg.lon == 0:
                        continue   # no GPS fix yet
                    fields = {
                        "lat": msg.lat / 1e7,
                        "lon": msg.lon / 1e7,
                        "alt": msg.alt / 1000.0,
                        "rel_alt": msg.relative_alt / 1000.0,
                    }
                    if msg.hdg != 65535:
                        fields["heading"] = msg.hdg / 100.0
                elif kind == "VFR_HUD":
                    fields = {"speed": msg.groundspeed, "heading": float(msg.heading) % 360}
                elif kind == "SYS_STATUS":
                    if msg.battery_remaining >= 0:
                        fields["battery"] = float(msg.battery_remaining)
                    if msg.voltage_battery != 65535:
                        fields["voltage"] = msg.voltage_battery / 1000.0
                elif kind == "GPS_RAW_INT":
                    if msg.satellites_visible != 255:
                        fields["sats"] = msg.satellites_visible
                elif kind == "HEARTBEAT":
                    if msg.type == mavutil.mavlink.MAV_TYPE_GCS:
                        continue
                    fields = {
                        "mode": mavutil.mode_string_v10(msg),
                        "armed": bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED),
                    }

                if fields:
                    self._ingest(fields, run_id)
        except Exception as exc:
            self._fail(run_id, f"MAVLink error: {exc}")
        finally:
            if master is not None:
                try:
                    master.close()
                except Exception:
                    pass


# ------------------------------------------------------------ hub registry
_hubs = {}
_hubs_lock = threading.Lock()


def get_hub(user_id):
    with _hubs_lock:
        hub = _hubs.get(user_id)
        if hub is None:
            hub = _hubs[user_id] = DroneHub()
        return hub
