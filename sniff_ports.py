"""
Listen on multiple ports to find where the app sends DREF writes.

Run WITHOUT X-Plane. Provides fake beacon + replies on 49000/49001.
Also listens on 49002, 49050, 49051 for any stray traffic.

Connect the iPad, then change a COM frequency on the IFD.
"""

import socket
import struct
import select
import sys
import threading
import time
import binascii
import math

MCAST_GRP = "239.255.1.1"
MCAST_PORT = 49707
LOG_FILE = "sniff_ports.log"


class Tee:
    def __init__(self, logpath):
        self.terminal = sys.stdout
        self.log = open(logpath, "w", encoding="utf-8")
    def write(self, msg):
        self.terminal.write(msg)
        self.log.write(msg)
        self.log.flush()
    def flush(self):
        self.terminal.flush()
        self.log.flush()


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def parse_and_log(port, data, addr):
    """Parse a packet and log it. Returns (type, details) for subscription tracking."""
    pkt_len = len(data)
    header = data[0:4] if pkt_len >= 4 else b""
    src = f"{addr[0]}:{addr[1]}"

    if header == b"RREF" and data[4:5] == b"\x00" and pkt_len >= 413:
        results = []
        offset = 5
        while offset + 408 <= pkt_len:
            freq, index = struct.unpack_from("<ii", data, offset)
            dref_raw = data[offset + 8 : offset + 408]
            dref = dref_raw.split(b"\x00")[0].decode("utf-8", errors="replace")
            results.append(("sub", index, dref, freq, addr))
            if freq == 0:
                print(f"[:{port}] {src} UNSUB idx={index} {dref}")
            else:
                print(f"[:{port}] {src} SUB   idx={index} @{freq}Hz {dref}")
            offset += 408
        return results

    elif header == b"DREF":
        offset = 5
        if offset + 504 <= pkt_len:
            value = struct.unpack_from("<f", data, offset)[0]
            val_hex = binascii.hexlify(data[offset:offset+4]).decode()
            dref_raw = data[offset + 4 : offset + 504]
            dref = dref_raw.split(b"\x00")[0].decode("utf-8", errors="replace").strip()
            print(f"\n{'='*60}")
            print(f"[:{port}] *** DREF WRITE from {src} ***")
            print(f"  dataref: {dref}")
            print(f"  value:   {value} (hex={val_hex})")
            print(f"{'='*60}\n")
        else:
            print(f"[:{port}] *** SHORT DREF from {src}: {pkt_len} bytes ***")
            print(f"  hex: {binascii.hexlify(data).decode()}")
        return [("dref",)]

    elif header == b"CMND":
        cmd = data[5:].split(b"\x00")[0].decode("utf-8", errors="replace")
        print(f"\n{'='*60}")
        print(f"[:{port}] *** COMMAND from {src}: {cmd} ***")
        print(f"{'='*60}\n")
        return [("cmnd",)]

    else:
        tag = header.decode("utf-8", errors="replace") if pkt_len >= 4 else "?"
        # Don't spam with our own RREF replies
        if header != b"RREF":
            print(f"[:{port}] {src} {tag} ({pkt_len} bytes)")
        return []


def main():
    sys.stdout = Tee(LOG_FILE)
    bind_ip = sys.argv[1] if len(sys.argv) > 1 else get_local_ip()

    print("=" * 60)
    print("  Multi-Port Sniffer")
    print(f"  Logging to: {LOG_FILE}")
    print("=" * 60)
    print(f"  Interface: {bind_ip}")
    print()

    # Create sockets on multiple ports
    ports = [49000, 49001, 49002, 49050, 49051]
    sockets = {}
    for port in ports:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("0.0.0.0", port))
            sock.setblocking(False)
            sockets[port] = sock
            print(f"  Listening on :{port}")
        except OSError as e:
            print(f"  :{port} FAILED ({e}) — is something else using it?")

    reply_sock = sockets.get(49001)
    if not reply_sock:
        print("ERROR: Could not bind 49001 for replies!")
        return

    print()
    print("  Make sure X-Plane is NOT running!")
    print("  Ctrl+C to stop.")
    print()

    # Beacon
    beacon_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    beacon_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    beacon_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(bind_ip))
    becn = b"BECN\x00" + struct.pack("<BBiiIH", 1, 2, 1, 115501, 1, 49000)
    becn += b"AVIDYNE-SNIFF\x00" + struct.pack("<H", 49010)

    # State
    subscriptions = {}  # {index: (dref, freq)}
    client_addr = None
    t0 = time.time()
    last_beacon = 0
    sub_logged = False

    all_socks = list(sockets.values())

    try:
        while True:
            now = time.time()

            # Beacon
            if now - last_beacon > 1.0:
                beacon_sock.sendto(becn, (MCAST_GRP, MCAST_PORT))
                last_beacon = now

            # Check all sockets for incoming data
            readable, _, _ = select.select(all_socks, [], [], 0.01)

            for sock in readable:
                port = next(p for p, s in sockets.items() if s is sock)
                data, addr = sock.recvfrom(4096)
                results = parse_and_log(port, data, addr)

                for r in results:
                    if r[0] == "sub":
                        _, index, dref, freq, a = r
                        client_addr = a
                        if freq == 0:
                            subscriptions.pop(index, None)
                        else:
                            subscriptions[index] = (dref, freq)

            # Send replies if we have subscriptions
            if subscriptions and client_addr:
                t = now - t0
                angle = (2 * math.pi * t) / 60.0
                heading = math.degrees(angle + math.pi / 2) % 360

                fake = {
                    "sim/flightmodel/position/latitude": -33.8568 + 0.01 * math.cos(angle),
                    "sim/flightmodel/position/longitude": 151.2153 + 0.01 * math.sin(angle),
                    "sim/flightmodel/position/elevation": 914.4,
                    "sim/flightmodel/position/local_vx": 30.0,
                    "sim/flightmodel/position/local_vy": 0.0,
                    "sim/flightmodel/position/local_vz": 50.0,
                    "sim/cockpit2/gauges/indicators/altitude_ft_pilot": 3000.0,
                    "sim/cockpit2/gauges/indicators/heading_AHARS_deg_mag_pilot": heading,
                    "sim/cockpit2/gauges/indicators/airspeed_kts_pilot": 120.0,
                    "sim/cockpit2/gauges/indicators/true_airspeed_kts_pilot": 120.0,
                    "sim/cockpit2/gauges/indicators/pitch_AHARS_deg_pilot": 2.0,
                    "sim/cockpit2/gauges/indicators/roll_AHARS_deg_pilot": 15.0,
                    "sim/cockpit2/gauges/indicators/sideslip_degrees": 0.0,
                    "sim/cockpit2/gauges/indicators/turn_rate_roll_deg_pilot": 6.0,
                    "sim/flightmodel/position/P": 0.0,
                    "sim/flightmodel/position/Q": 0.0,
                    "sim/flightmodel/position/R": 6.0,
                    "sim/flightmodel/position/local_ax": 0.0,
                    "sim/flightmodel/position/local_ay": 9.81,
                    "sim/flightmodel/position/local_az": 0.0,
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
                    "sim/cockpit2/radios/actuators/com1_frequency_hz": 13595.0,
                    "sim/cockpit2/radios/actuators/com1_standby_frequency_hz": 12180.0,
                    "sim/cockpit2/radios/actuators/nav1_frequency_hz": 11550.0,
                    "sim/cockpit2/radios/actuators/nav1_standby_frequency_hz": 10900.0,
                    "sim/cockpit2/radios/actuators/nav2_frequency_hz": 11700.0,
                }

                pkt = b"RREF,"
                for index, (dref, freq) in subscriptions.items():
                    pkt += struct.pack("<if", index, float(fake.get(dref, 0.0)))

                reply_sock.sendto(pkt, client_addr)

                if not sub_logged and len(subscriptions) > 40:
                    sub_logged = True
                    print(f"\n[REPLY] Sending {len(subscriptions)} values from :{49001}")
                    print(f"        COM1: {fake.get('sim/cockpit2/radios/actuators/com1_frequency_hz')}")
                    print(f"        Waiting for DREF writes...\n")

    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
