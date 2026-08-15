"""
Avidyne IFD Trainer XP <-> MSFS 2020 Bridge.

Impersonates X-Plane on the network so the Avidyne IFD Trainer XP iPad app
can connect, then translates between X-Plane datarefs and MSFS SimConnect.

Usage:
    AvidyneBridge.exe                     # auto-detect interface
    AvidyneBridge.exe 192.168.1.100       # force a specific interface
    AvidyneBridge.exe --fake              # test mode without MSFS
    AvidyneBridge.exe --log bridge.log    # write detailed log to file
"""

import logging
import math
import socket
import struct
import sys
import threading
import time
import binascii

log = logging.getLogger("bridge")


# ----- Network constants -----

MCAST_GRP = "239.255.1.1"
MCAST_PORT = 49707
LISTEN_PORT = 49000


# ----- X-Plane dataref → MSFS SimVar mapping -----

# Each entry: x-plane dataref → (msfs_simvar, unit_conversion_function)
# Conversion functions take the raw MSFS value and return the X-Plane equivalent.

def _identity(v):
    return v

def _rad_to_deg(v):
    return math.degrees(v) if v is not None else 0.0

def _mps_to_mps(v):
    """MSFS velocity vars are in ft/s, convert to m/s."""
    return v * 0.3048 if v is not None else 0.0

def _ft_to_m(v):
    return v * 0.3048 if v is not None else 0.0

def _fpm_to_fpm(v):
    return v if v is not None else 0.0

def _safe(v):
    return v if v is not None else 0.0

def _cdi_to_dots(v):
    """MSFS NAV_CDI is -127 to 127, X-Plane hdef_dot is -2.5 to 2.5 dots."""
    return (v / 127.0) * 2.5 if v is not None else 0.0

def _gsi_to_dots(v):
    """MSFS NAV_GSI is -127 to 127, X-Plane vdef_dot is -2.5 to 2.5 dots."""
    return (v / 127.0) * 2.5 if v is not None else 0.0

def _msfs_freq_to_xp(v):
    """MSFS returns MHz float (e.g. 124.375), X-Plane wants 10kHz int (e.g. 12437)."""
    return int(round(v * 100)) if v is not None else 0.0

def _accel_fps2_to_mps2(v):
    """ft/s² → m/s²."""
    return v * 0.3048 if v is not None else 0.0

def _inhg(v):
    return v if v is not None else 29.92

def _celsius(v):
    return v if v is not None else 15.0


# The master mapping table.
# Keys are X-Plane dataref names (as subscribed by the Avidyne app).
# Values are (MSFS_SimVar_name, conversion_function).
#
# SimVar names use underscores — the SimConnect library accepts both
# spaces and underscores.

DATAREF_MAP = {
    # --- Position ---
    "sim/flightmodel/position/latitude":
        ("PLANE_LATITUDE", _safe),
    "sim/flightmodel/position/longitude":
        ("PLANE_LONGITUDE", _safe),
    "sim/flightmodel/position/elevation":
        ("PLANE_ALTITUDE", _ft_to_m),

    # --- Velocity (X-Plane wants m/s in world/local frame) ---
    "sim/flightmodel/position/local_vx":
        ("VELOCITY_WORLD_X", _mps_to_mps),
    "sim/flightmodel/position/local_vy":
        ("VELOCITY_WORLD_Y", _mps_to_mps),
    "sim/flightmodel/position/local_vz":
        ("VELOCITY_WORLD_Z", _mps_to_mps),

    # --- Attitude ---
    "sim/cockpit2/gauges/indicators/pitch_AHARS_deg_pilot":
        ("PLANE_PITCH_DEGREES", _rad_to_deg),
    "sim/cockpit2/gauges/indicators/roll_AHARS_deg_pilot":
        ("PLANE_BANK_DEGREES", _rad_to_deg),
    "sim/cockpit2/gauges/indicators/heading_AHARS_deg_mag_pilot":
        ("PLANE_HEADING_DEGREES_MAGNETIC", _rad_to_deg),
    "sim/cockpit2/gauges/indicators/sideslip_degrees":
        ("INCIDENCE_BETA", _rad_to_deg),

    # --- Rates ---
    "sim/cockpit2/gauges/indicators/turn_rate_roll_deg_pilot":
        ("TURN_INDICATOR_RATE", _safe),
    "sim/flightmodel/position/P":
        ("ROTATION_VELOCITY_BODY_X", _rad_to_deg),
    "sim/flightmodel/position/Q":
        ("ROTATION_VELOCITY_BODY_Y", _rad_to_deg),
    "sim/flightmodel/position/R":
        ("ROTATION_VELOCITY_BODY_Z", _rad_to_deg),

    # --- Acceleration (world frame to match X-Plane's local frame) ---
    "sim/flightmodel/position/local_ax":
        ("ACCELERATION_WORLD_X", _accel_fps2_to_mps2),
    "sim/flightmodel/position/local_ay":
        ("ACCELERATION_WORLD_Y", _accel_fps2_to_mps2),
    "sim/flightmodel/position/local_az":
        ("ACCELERATION_WORLD_Z", _accel_fps2_to_mps2),

    # --- Air data ---
    "sim/cockpit2/gauges/indicators/true_airspeed_kts_pilot":
        ("AIRSPEED_TRUE", _safe),
    "sim/cockpit2/gauges/indicators/airspeed_kts_pilot":
        ("AIRSPEED_INDICATED", _safe),
    "sim/cockpit2/gauges/indicators/altitude_ft_pilot":
        ("INDICATED_ALTITUDE", _safe),
    "sim/flightmodel2/misc/AoA_angle_degrees":
        ("INCIDENCE_ALPHA", _rad_to_deg),
    "sim/flightmodel/position/vh_ind":
        ("VERTICAL_SPEED", _safe),
    "sim/cockpit2/gauges/actuators/barometer_setting_in_hg_pilot":
        ("KOHLSMAN_SETTING_HG", _inhg),

    # --- Weather ---
    "sim/weather/temperature_ambient_c":
        ("AMBIENT_TEMPERATURE", _celsius),
    "sim/weather/temperature_le_c":
        ("TOTAL_AIR_TEMPERATURE", _celsius),

    # --- NAV / CDI ---
    "sim/cockpit/radios/nav1_fromto":
        ("NAV_TOFROM:1", _safe),
    "sim/cockpit/radios/nav1_CDI":
        ("NAV_CDI:1", _safe),
    "sim/cockpit/radios/nav1_hdef_dot":
        ("NAV_CDI:1", _cdi_to_dots),
    "sim/cockpit/radios/nav1_vdef_dot":
        ("NAV_GSI:1", _gsi_to_dots),
    "sim/cockpit/radios/nav1_course_degm":
        ("NAV_OBS:1", _safe),

    # --- Autopilot ---
    "sim/cockpit2/autopilot/gpss_status":
        ("GPS_DRIVES_NAV1", _safe),
    "sim/cockpit2/autopilot/approach_status":
        ("AUTOPILOT_APPROACH_HOLD", _safe),
    "sim/cockpit2/radios/actuators/HSI_source_select_pilot":
        ("GPS_DRIVES_NAV1", _safe),  # 0=nav, 1=gps

    # --- Radios ---
    "sim/cockpit2/radios/actuators/nav1_frequency_hz":
        ("NAV_ACTIVE_FREQUENCY:1", _msfs_freq_to_xp),
    "sim/cockpit2/radios/actuators/nav1_standby_frequency_hz":
        ("NAV_STANDBY_FREQUENCY:1", _msfs_freq_to_xp),
    "sim/cockpit2/radios/actuators/nav2_frequency_hz":
        ("NAV_ACTIVE_FREQUENCY:2", _msfs_freq_to_xp),
    "sim/cockpit2/radios/actuators/com1_frequency_hz":
        ("COM_ACTIVE_FREQUENCY:1", _msfs_freq_to_xp),
    "sim/cockpit2/radios/actuators/com1_standby_frequency_hz":
        ("COM_STANDBY_FREQUENCY:1", _msfs_freq_to_xp),

    # --- Nav ident (individual ASCII chars as floats) ---
    # MSFS doesn't expose these as individual chars easily; return 0 for now.
    "sim/cockpit2/radios/indicators/nav1_nav_id[0]": None,
    "sim/cockpit2/radios/indicators/nav1_nav_id[1]": None,
    "sim/cockpit2/radios/indicators/nav1_nav_id[2]": None,
    "sim/cockpit2/radios/indicators/nav1_nav_id[3]": None,
    "sim/cockpit2/radios/indicators/nav1_nav_id[4]": None,
}

# DREF writes from the app → MSFS.
# "event" entries use SimConnect events (for radios).
# "simvar" entries use SimVar set (for CDI/GPS).
# None entries are acknowledged but not forwarded.

def _xp_freq_to_bcd(v):
    """Convert X-Plane freq (e.g. 12590) directly to BCD for SimConnect.
    12590 → 0x00012590 in BCD."""
    val = int(v)
    bcd = 0
    shift = 0
    while val > 0:
        bcd |= (val % 10) << shift
        val //= 10
        shift += 4
    return bcd

DREF_WRITE_MAP = {
    # Radio frequencies — use SimConnect events with BCD encoding
    "sim/cockpit2/radios/actuators/com1_frequency_hz":
        ("event", "COM_RADIO_SET", _xp_freq_to_bcd),
    "sim/cockpit2/radios/actuators/com1_standby_frequency_hz":
        ("event", "COM_STBY_RADIO_SET", _xp_freq_to_bcd),
    "sim/cockpit2/radios/actuators/nav1_frequency_hz":
        ("event", "NAV1_RADIO_SET", _xp_freq_to_bcd),
    "sim/cockpit2/radios/actuators/nav1_standby_frequency_hz":
        ("event", "NAV1_STBY_SET", _xp_freq_to_bcd),
    "sim/cockpit2/radios/actuators/nav2_frequency_hz":
        ("event", "NAV2_RADIO_SET", _xp_freq_to_bcd),

    # GPS desired track → MSFS heading bug
    # -360 = no course (skip), negative values need +360 normalization
    "sim/cockpit/radios/gps_course_degtm":
        ("event", "HEADING_BUG_SET", lambda v: int(round(v % 360)) if v > -360 else None),

    # CDI/GPS — continuous writes, acknowledged but not forwarded
    "sim/cockpit/radios/gps_has_glideslope": None,
    "sim/cockpit/radios/gps_hdef_dot": None,
    "sim/cockpit/radios/gps_vdef_dot": None,
    "sim/cockpit/radios/gps_fromto": None,
    "sim/cockpit/radios/nav1_hdef_dot": None,
    "sim/cockpit/radios/nav1_vdef_dot": None,
    "sim/cockpit/radios/nav1_fromto": None,

    # Overrides and source selects
    "sim/cockpit2/radios/actuators/HSI_source_select_pilot": "hsi_sync",
    "sim/cockpit2/radios/actuators/RMI_source_select_pilot": None,
    "sim/operation/override/override_gps": None,
    "sim/operation/override/override_navneedles": None,
}


# ----- SimConnect data source -----

class SimConnectSource:
    """Reads SimVars from MSFS via SimConnect. Reconnects automatically."""

    RETRY_INTERVAL = 5  # seconds between connection attempts

    def __init__(self):
        self.sm = None
        self.aq = None
        self.ae = None
        self._cache = {}
        self._cache_time = 0
        self._cache_ttl = 0.05
        self._lock = threading.Lock()
        self._connected = False
        self._connect_thread = threading.Thread(
            target=self._connection_loop, daemon=True)
        self._connect_thread.start()

    def _try_connect(self):
        from SimConnect import SimConnect, AircraftRequests, AircraftEvents
        self.sm = SimConnect()
        self.aq = AircraftRequests(self.sm, _time=50)
        self.ae = AircraftEvents(self.sm)
        self._connected = True

    def _connection_loop(self):
        logged_waiting = False
        while not self._connected:
            try:
                self._try_connect()
                log.info("Connected to MSFS.")
            except Exception:
                if not logged_waiting:
                    log.info("Waiting for MSFS... (retrying every %ds)",
                             self.RETRY_INTERVAL)
                    logged_waiting = True
                time.sleep(self.RETRY_INTERVAL)

    def get(self, simvar_name):
        """Read a SimVar value. Returns float or None."""
        if not self._connected:
            return None
        try:
            return self.aq.get(simvar_name)
        except Exception:
            self._handle_disconnect()
            return None

    def get_state(self, subscribed_drefs):
        """Return {dataref_name: float_value} for all subscribed datarefs."""
        if not self._connected:
            return {dref: 0.0 for dref in subscribed_drefs}

        now = time.time()
        with self._lock:
            if now - self._cache_time < self._cache_ttl:
                return dict(self._cache)

        state = {}
        for dref in subscribed_drefs:
            mapping = DATAREF_MAP.get(dref)
            if mapping is None:
                state[dref] = 0.0
            else:
                simvar, convert = mapping
                raw = self.get(simvar)
                state[dref] = convert(raw)

        with self._lock:
            self._cache = state
            self._cache_time = now
        return state

    def write_dref(self, dref, value):
        """Handle a DREF write from the app -> MSFS."""
        if not self._connected:
            return

        mapping = DREF_WRITE_MAP.get(dref)
        if mapping is None:
            return

        if mapping == "hsi_sync":
            self._sync_hsi_source(value)
            return

        kind = mapping[0]
        if kind == "event":
            _, event_name, convert = mapping
            try:
                param = convert(value)
                if param is None:
                    return
                event = self.ae.find(event_name)
                event(int(param))
            except Exception as e:
                log.warning("Event %s failed: %s", event_name, e)
                self._handle_disconnect()

    def _sync_hsi_source(self, value):
        """Sync HSI source: app sends 0=NAV, 1=GPS. Toggle MSFS if mismatched."""
        try:
            wanted_gps = int(value) != 0
            current = self.aq.get("GPS_DRIVES_NAV1")
            current_gps = current is not None and int(current) != 0
            if wanted_gps != current_gps:
                event = self.ae.find("TOGGLE_GPS_DRIVES_NAV1")
                event()
                log.debug("HSI source toggled to %s", "GPS" if wanted_gps else "NAV")
        except Exception as e:
            log.warning("HSI source sync failed: %s", e)
            self._handle_disconnect()

    def _handle_disconnect(self):
        if not self._connected:
            return
        self._connected = False
        log.warning("Lost connection to MSFS. Reconnecting...")
        try:
            self.sm.exit()
        except Exception:
            pass
        self.sm = None
        self.aq = None
        self.ae = None
        self._connect_thread = threading.Thread(
            target=self._connection_loop, daemon=True)
        self._connect_thread.start()

    def close(self):
        self._connected = False
        try:
            if self.sm:
                self.sm.exit()
        except Exception:
            pass


class FakeSource:
    """Returns synthetic orbit data (no MSFS needed)."""

    def __init__(self):
        self.t0 = time.time()
        self.center_lat = -33.8568
        self.center_lon = 151.2153
        log.info("Using FAKE data source (no MSFS).")

    def get_state(self, subscribed_drefs):
        t = time.time() - self.t0
        angle = (2 * math.pi * t) / 60.0
        lat = self.center_lat + 0.01 * math.cos(angle)
        lon = self.center_lon + 0.01 * math.sin(angle)
        heading = math.degrees(angle + math.pi / 2) % 360
        speed_ms = 120.0 * 0.5144

        state = {
            "sim/flightmodel/position/latitude": lat,
            "sim/flightmodel/position/longitude": lon,
            "sim/flightmodel/position/elevation": 3000 * 0.3048,
            "sim/flightmodel/position/local_vx": speed_ms * math.sin(math.radians(heading)),
            "sim/flightmodel/position/local_vy": 0.0,
            "sim/flightmodel/position/local_vz": speed_ms * math.cos(math.radians(heading)),
            "sim/cockpit2/gauges/indicators/pitch_AHARS_deg_pilot": 2.0,
            "sim/cockpit2/gauges/indicators/roll_AHARS_deg_pilot": 15.0,
            "sim/cockpit2/gauges/indicators/heading_AHARS_deg_mag_pilot": heading,
            "sim/cockpit2/gauges/indicators/sideslip_degrees": 0.0,
            "sim/cockpit2/gauges/indicators/turn_rate_roll_deg_pilot": 6.0,
            "sim/flightmodel/position/P": 0.0,
            "sim/flightmodel/position/Q": 0.0,
            "sim/flightmodel/position/R": 6.0,
            "sim/flightmodel/position/local_ax": 0.0,
            "sim/flightmodel/position/local_ay": 9.81,
            "sim/flightmodel/position/local_az": 0.0,
            "sim/cockpit2/gauges/indicators/true_airspeed_kts_pilot": 120.0,
            "sim/cockpit2/gauges/indicators/airspeed_kts_pilot": 120.0,
            "sim/cockpit2/gauges/indicators/altitude_ft_pilot": 3000.0,
            "sim/flightmodel2/misc/AoA_angle_degrees": 3.0,
            "sim/flightmodel/position/vh_ind": 0.0,
            "sim/cockpit2/gauges/actuators/barometer_setting_in_hg_pilot": 29.92,
            "sim/weather/temperature_ambient_c": 15.0,
            "sim/weather/temperature_le_c": 15.0,
            "sim/cockpit/radios/nav1_fromto": 0.0,
            "sim/cockpit/radios/nav1_CDI": 0.0,
            "sim/cockpit/radios/nav1_hdef_dot": 0.0,
            "sim/cockpit/radios/nav1_vdef_dot": 0.0,
            "sim/cockpit/radios/nav1_course_degm": 0.0,
            "sim/cockpit2/autopilot/gpss_status": 0.0,
            "sim/cockpit2/autopilot/approach_status": 0.0,
            "sim/cockpit2/radios/actuators/HSI_source_select_pilot": 0.0,
            # Distinctive test frequencies — check if IFD displays these
            "sim/cockpit2/radios/actuators/nav1_frequency_hz": 11550,    # 115.50
            "sim/cockpit2/radios/actuators/nav1_standby_frequency_hz": 10900,  # 109.00
            "sim/cockpit2/radios/actuators/nav2_frequency_hz": 11700,    # 117.00
            "sim/cockpit2/radios/actuators/com1_frequency_hz": 13595,    # 135.95 (same as X-Plane test)
            "sim/cockpit2/radios/actuators/com1_standby_frequency_hz": 12180,  # 121.80
        }
        # Fill anything missing with 0.0
        for dref in subscribed_drefs:
            if dref not in state:
                state[dref] = 0.0
        return state

    def write_dref(self, dref, value):
        pass  # fake source ignores writes

    def close(self):
        pass


# ----- Network helpers -----

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def build_becn_payload(listen_port):
    header = b"BECN\x00"
    body = struct.pack("<BBiiIH",
        1, 2, 1, 115501, 1, listen_port,
    )
    name = b"AVIDYNE-BRIDGE\x00"
    raknet_port = struct.pack("<H", 49010)
    return header + body + name + raknet_port


def build_rref_reply(values):
    """Build an RREF reply. X-Plane uses 'RREF,' (comma) separator in replies."""
    pkt = b"RREF,"
    for index, value in values:
        pkt += struct.pack("<if", index, value)
    return pkt


# ----- Shared state -----

class SubscriptionTable:
    """Thread-safe subscription tracker."""

    def __init__(self):
        self.lock = threading.Lock()
        self.subs = {}       # {index: (dataref_name, freq_hz)}
        self.client_addr = None

    def add(self, index, dataref, freq, addr):
        with self.lock:
            self.client_addr = addr
            if freq == 0:
                self.subs.pop(index, None)
            else:
                self.subs[index] = (dataref, freq)

    def get_all(self):
        with self.lock:
            return dict(self.subs), self.client_addr

    def get_dref_names(self):
        with self.lock:
            return [dref for dref, freq in self.subs.values()]


# ----- Threads -----

def beacon_thread(bind_ip):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.setsockopt(
        socket.IPPROTO_IP,
        socket.IP_MULTICAST_IF,
        socket.inet_aton(bind_ip),
    )
    payload = build_becn_payload(LISTEN_PORT)
    log.debug("Beaconing on %s:%s via %s", MCAST_GRP, MCAST_PORT, bind_ip)
    while True:
        sock.sendto(payload, (MCAST_GRP, MCAST_PORT))
        time.sleep(1)


def listener_thread(sock, subs, source):
    """Receive packets from the app and update the subscription table."""
    log.debug("Listening for packets on :%s", LISTEN_PORT)

    dref_write_counts = {}  # suppress repeated log spam

    while True:
        data, addr = sock.recvfrom(4096)
        pkt_len = len(data)
        header = data[0:4]

        if header == b"RREF":
            offset = 5
            while offset + 408 <= pkt_len:
                freq, index = struct.unpack_from("<ii", data, offset)
                dref_raw = data[offset + 8 : offset + 408]
                dref = dref_raw.split(b"\x00")[0].decode("utf-8", errors="replace")
                subs.add(index, dref, freq, addr)
                mapped = dref in DATAREF_MAP and DATAREF_MAP[dref] is not None
                tag = "" if mapped else " [UNMAPPED]"
                if freq == 0:
                    log.debug("UNSUB idx=%d %s", index, dref)
                else:
                    log.debug("SUB   idx=%d @%dHz %s%s", index, freq, dref, tag)
                offset += 408

        elif header == b"DREF":
            offset = 5
            if offset + 504 <= pkt_len:
                value = struct.unpack_from("<f", data, offset)[0]
                dref_raw = data[offset + 4 : offset + 504]
                dref = dref_raw.split(b"\x00")[0].decode("utf-8", errors="replace").strip()

                # Track writes — log changes for GPS datarefs, throttle others
                last_val = dref_write_counts.get(dref, (0, None))
                count = last_val[0] + 1
                prev_value = last_val[1]
                dref_write_counts[dref] = (count, value)

                # Always log GPS course/CDI changes (when value actually changes)
                is_gps = "gps_" in dref or "nav1_" in dref
                value_changed = prev_value is None or abs(value - prev_value) > 0.01

                if is_gps and value_changed:
                    log.debug("WRITE %s = %.4f", dref, value)
                elif count == 1:
                    log.debug("WRITE %s = %s", dref, value)
                elif not is_gps and count % 50 == 0:
                    log.debug("WRITE %s = %s (x%d)", dref, value, count)

                # Forward write to MSFS
                source.write_dref(dref, value)

        elif header == b"CMND":
            cmd = data[5:].split(b"\x00")[0].decode("utf-8", errors="replace")
            log.debug("COMMAND %s", cmd)

        else:
            log.debug("UNKNOWN %s", binascii.hexlify(data[:40]).decode())


def responder_thread(sock, subs, source):
    """Send RREF replies grouped by update frequency, mimicking X-Plane."""
    logged_count = 0
    tick_count = 0
    last_debug = 0
    last_com1 = 0

    # Send cycle at ~60 Hz base rate, but only send each group at its own rate
    BASE_TICK = 0.016  # ~60 Hz base loop

    while True:
        tick_count += 1
        all_subs, client_addr = subs.get_all()

        if all_subs and client_addr:
            dref_names = [dref for dref, freq in all_subs.values()]
            state = source.get_state(dref_names)

            # Group subscriptions by their requested frequency
            groups = {}  # {freq_hz: [(index, value), ...]}
            for index, (dref, freq) in all_subs.items():
                val = state.get(dref, 0.0)
                try:
                    fval = float(val)
                except (TypeError, ValueError):
                    fval = 0.0
                groups.setdefault(freq, []).append((index, fval))

            # Send each group at its requested rate
            # tick_count * BASE_TICK = elapsed ticks
            # Send group if: tick_count % (BASE_TICK_HZ / freq) == 0
            for freq, values in groups.items():
                # How many base ticks between sends for this frequency
                interval = max(1, int(round((1.0 / BASE_TICK) / freq)))
                if tick_count % interval == 0:
                    pkt = build_rref_reply(values)
                    sock.sendto(pkt, client_addr)

            # Log once when subscriptions arrive
            if len(all_subs) != logged_count:
                logged_count = len(all_subs)
                log.info("App connected: %d datarefs subscribed from %s",
                         len(all_subs), client_addr[0])
                for freq in sorted(groups.keys(), reverse=True):
                    indices = [str(idx) for idx, _ in groups[freq]]
                    log.debug("  %2d Hz: %d values (idx %s)",
                              freq, len(groups[freq]), ",".join(indices))

            # Periodic status
            now = time.time()
            if now - last_debug > 5.0 and state:
                last_debug = now
                com1 = state.get("sim/cockpit2/radios/actuators/com1_frequency_hz", 0)
                com1s = state.get("sim/cockpit2/radios/actuators/com1_standby_frequency_hz", 0)
                alt = state.get("sim/cockpit2/gauges/indicators/altitude_ft_pilot", 0)
                hdef = state.get("sim/cockpit/radios/nav1_hdef_dot", 0)
                vdef = state.get("sim/cockpit/radios/nav1_vdef_dot", 0)
                log.debug("alt=%.0fft com1=%.0f/%.0f cdi=%.2f/%.2fdots",
                           alt, com1, com1s, hdef, vdef)
                if com1 != last_com1 and last_com1 != 0:
                    log.debug("COM1 changed: %.0f -> %.0f", last_com1, com1)
                last_com1 = com1

        time.sleep(BASE_TICK)


def setup_logging(log_file=None):
    """Configure logging: INFO to console, DEBUG to file if specified."""
    log.setLevel(logging.DEBUG)

    # Console: INFO only, clean format
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(console)

    # File: DEBUG, timestamped
    if log_file:
        fh = logging.FileHandler(log_file, mode="w", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(message)s",
                                          datefmt="%H:%M:%S"))
        log.addHandler(fh)
        log.info("Logging to %s", log_file)


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__.strip())
        return

    use_fake = "--fake" in sys.argv

    # Parse --log <filename>
    log_file = None
    if "--log" in sys.argv:
        idx = sys.argv.index("--log")
        if idx + 1 < len(sys.argv):
            log_file = sys.argv[idx + 1]

    args = [a for a in sys.argv[1:]
            if not a.startswith("--") and a != log_file]
    bind_ip = args[0] if args else get_local_ip()

    setup_logging(log_file)

    log.info("Avidyne IFD <-> MSFS Bridge")
    log.info("Interface: %s", bind_ip)

    if use_fake:
        log.info("Data source: FAKE (orbit)")
        source = FakeSource()
    else:
        log.info("Data source: MSFS SimConnect")
        source = SimConnectSource()

    log.info("Ready. Ctrl+C to stop.")

    subs = SubscriptionTable()

    listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listen_sock.bind(("0.0.0.0", LISTEN_PORT))

    reply_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    reply_sock.bind(("0.0.0.0", LISTEN_PORT + 1))

    t1 = threading.Thread(target=beacon_thread, args=(bind_ip,), daemon=True)
    t2 = threading.Thread(target=listener_thread, args=(listen_sock, subs, source), daemon=True)
    t3 = threading.Thread(target=responder_thread, args=(reply_sock, subs, source), daemon=True)

    t1.start()
    t2.start()
    t3.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down...")
        source.close()
        log.info("Stopped.")


if __name__ == "__main__":
    main()
