"""
Radio-focused proxy between Avidyne app and real X-Plane.

Logs every RREF reply value for radio indices (37-42) with raw hex,
and captures ALL DREF writes and CMNDs from the app in full detail.

Usage: Start X-Plane first, then run this, then connect the iPad app.
       Try changing frequencies on both X-Plane and the IFD.

    python proxy_radio.py
"""

import socket
import struct
import sys
import threading
import time
import binascii

MCAST_GRP = "239.255.1.1"
MCAST_PORT = 49707
LOG_FILE = "proxy_radio.log"

# The radio-related subscription indices from the app
RADIO_INDICES = {37, 38, 39, 40, 41, 42}
RADIO_NAMES = {
    37: "nav1_standby_freq",
    38: "nav2_freq",
    39: "nav1_freq",
    40: "com1_standby_freq",
    41: "com1_freq",
    42: "HSI_source",
}


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


def find_xplane(bind_ip):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", MCAST_PORT))
    mreq = struct.pack("4s4s", socket.inet_aton(MCAST_GRP), socket.inet_aton(bind_ip))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.settimeout(15)
    print("Waiting for X-Plane beacon...")
    while True:
        data, addr = sock.recvfrom(1024)
        if data[:4] == b"BECN" and addr[0] == bind_ip:
            port = struct.unpack_from("<H", data, 19)[0]
            print(f"Found X-Plane at {addr[0]}:{port}")
            sock.close()
            return addr[0], port


def main():
    sys.stdout = Tee(LOG_FILE)
    bind_ip = sys.argv[1] if len(sys.argv) > 1 else get_local_ip()

    print("=" * 60)
    print("  Radio-Focused Proxy — X-Plane ↔ Avidyne App")
    print(f"  Logging to: {LOG_FILE}")
    print("=" * 60)
    print()

    xp_ip, xp_port = find_xplane(bind_ip)

    # Listen on a different port since X-Plane has 49000
    proxy_port = 49050
    proxy_reply_port = 49051

    print(f"  X-Plane:  {xp_ip}:{xp_port}")
    print(f"  Proxy:    :{proxy_port} (listen), :{proxy_reply_port} (reply)")
    print()

    # Sockets
    listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listen_sock.bind(("0.0.0.0", proxy_port))
    listen_sock.settimeout(0.05)

    fwd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    fwd_sock.bind(("0.0.0.0", 49017))
    fwd_sock.settimeout(0.05)

    reply_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    reply_sock.bind(("0.0.0.0", proxy_reply_port))

    # Beacon
    beacon_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    beacon_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    beacon_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(bind_ip))
    becn = b"BECN\x00" + struct.pack("<BBiiIH", 1, 2, 1, 115501, 1, proxy_port)
    becn += b"AVIDYNE-PROXY\x00" + struct.pack("<H", 49010)

    app_addr = None
    sub_count = 0
    rref_reply_count = 0
    last_radio_values = {}
    last_beacon = 0
    dref_write_count = 0

    print("Proxy running. Connect the iPad app.")
    print("Try changing COM1 in X-Plane and on the IFD.")
    print("Ctrl+C to stop.\n")

    try:
        while True:
            now = time.time()

            # Beacon every 0.5s
            if now - last_beacon > 0.5:
                beacon_sock.sendto(becn, (MCAST_GRP, MCAST_PORT))
                last_beacon = now

            # App → X-Plane
            try:
                data, addr = listen_sock.recvfrom(4096)
                app_addr = addr
                pkt_len = len(data)
                header = data[0:4]

                if header == b"RREF" and data[4:5] == b"\x00" and pkt_len >= 413:
                    # Subscription — forward and log
                    offset = 5
                    while offset + 408 <= pkt_len:
                        freq, index = struct.unpack_from("<ii", data, offset)
                        dref_raw = data[offset + 8 : offset + 408]
                        dref = dref_raw.split(b"\x00")[0].decode("utf-8", errors="replace")
                        sub_count += 1
                        if sub_count <= 50:
                            print(f"[APP→XP] SUB idx={index:<3} @{freq:>3}Hz {dref}")
                        offset += 408
                    fwd_sock.sendto(data, (xp_ip, xp_port))

                elif header == b"DREF":
                    # DREF write — LOG EVERYTHING
                    dref_write_count += 1
                    offset = 5
                    if offset + 504 <= pkt_len:
                        value = struct.unpack_from("<f", data, offset)[0]
                        val_hex = binascii.hexlify(data[offset:offset+4]).decode()
                        dref_raw = data[offset + 4 : offset + 504]
                        dref = dref_raw.split(b"\x00")[0].decode("utf-8", errors="replace").strip()
                        print(f"[APP→XP] *** DREF WRITE #{dref_write_count}: {dref} = {value} (hex={val_hex}) ***")
                    else:
                        print(f"[APP→XP] *** DREF WRITE #{dref_write_count}: short packet ({pkt_len} bytes)")
                        print(f"         hex: {binascii.hexlify(data).decode()}")
                    fwd_sock.sendto(data, (xp_ip, xp_port))

                elif header == b"CMND":
                    cmd = data[5:].split(b"\x00")[0].decode("utf-8", errors="replace")
                    print(f"[APP→XP] *** COMMAND: {cmd} ***")
                    fwd_sock.sendto(data, (xp_ip, xp_port))

                else:
                    tag = header.decode("utf-8", errors="replace")
                    print(f"[APP→XP] {tag} ({pkt_len} bytes)")
                    fwd_sock.sendto(data, (xp_ip, xp_port))

            except socket.timeout:
                pass

            # X-Plane → App
            try:
                data, addr = fwd_sock.recvfrom(4096)
                if app_addr:
                    reply_sock.sendto(data, app_addr)

                # Parse RREF replies, focus on radio indices
                if data[:4] == b"RREF" and len(data) >= 13:
                    rref_reply_count += 1
                    offset = 5
                    radio_in_this_pkt = []
                    total_values = 0
                    while offset + 8 <= len(data):
                        idx, val = struct.unpack_from("<if", data, offset)
                        total_values += 1
                        if idx in RADIO_INDICES:
                            val_hex = binascii.hexlify(data[offset:offset+8]).decode()
                            name = RADIO_NAMES.get(idx, f"idx{idx}")
                            radio_in_this_pkt.append((idx, val, val_hex, name))
                            # Check for change
                            if idx in last_radio_values and last_radio_values[idx] != val:
                                print(f"[XP→APP] *** RADIO CHANGE: {name} {last_radio_values[idx]:.0f} → {val:.0f} ***")
                            last_radio_values[idx] = val
                        offset += 8

                    # Log radio packets (but not every position update)
                    if radio_in_this_pkt:
                        if rref_reply_count <= 5 or rref_reply_count % 30 == 0:
                            print(f"[XP→APP] RREF reply #{rref_reply_count} ({total_values} values, "
                                  f"from {addr[0]}:{addr[1]}, {len(data)} bytes)")
                            for idx, val, hexval, name in radio_in_this_pkt:
                                print(f"         idx={idx} {name:>20s} = {val:>10.1f}  raw={hexval}")

            except socket.timeout:
                pass

    except KeyboardInterrupt:
        print(f"\n\nStopped. {rref_reply_count} RREF replies, {dref_write_count} DREF writes captured.")
        print(f"Final radio values: {last_radio_values}")


if __name__ == "__main__":
    main()
