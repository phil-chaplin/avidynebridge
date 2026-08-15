"""
Connect to real X-Plane and subscribe to the same radio datarefs the Avidyne app uses.
Logs the raw values X-Plane returns so we can match the format exactly.
"""
import socket
import struct
import sys
import time

MCAST_GRP = "239.255.1.1"
MCAST_PORT = 49707

RADIO_DREFS = [
    (37, "sim/cockpit2/radios/actuators/nav1_standby_frequency_hz"),
    (38, "sim/cockpit2/radios/actuators/nav2_frequency_hz"),
    (39, "sim/cockpit2/radios/actuators/nav1_frequency_hz"),
    (40, "sim/cockpit2/radios/actuators/com1_standby_frequency_hz"),
    (41, "sim/cockpit2/radios/actuators/com1_frequency_hz"),
]


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
    sock.settimeout(10)
    print("Waiting for X-Plane beacon...")
    while True:
        data, addr = sock.recvfrom(1024)
        if data[:4] == b"BECN":
            port = struct.unpack_from("<H", data, 19)[0]
            print(f"Found X-Plane at {addr[0]}:{port}")
            sock.close()
            return addr[0], port


def main():
    bind_ip = sys.argv[1] if len(sys.argv) > 1 else get_local_ip()
    xp_ip, xp_port = find_xplane(bind_ip)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((bind_ip, 49017))
    sock.settimeout(5)

    # Subscribe to all radio datarefs
    for idx, dref in RADIO_DREFS:
        dref_bytes = dref.encode() + b"\x00" * (400 - len(dref))
        pkt = b"RREF\x00" + struct.pack("<ii", 2, idx) + dref_bytes
        sock.sendto(pkt, (xp_ip, xp_port))
        print(f"Subscribed idx={idx}: {dref}")

    print("\nWaiting for replies...\n")

    seen = 0
    while seen < 20:
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            break
        if data[:4] == b"RREF":
            offset = 5
            while offset + 8 <= len(data):
                idx, val = struct.unpack_from("<if", data, offset)
                # Find the dref name
                name = next((d for i, d in RADIO_DREFS if i == idx), f"idx={idx}")
                print(f"  idx={idx:>3}  value={val:>15.4f}  raw_hex={struct.pack('<f', val).hex()}  {name}")
                offset += 8
            seen += 1
            print()

    # Unsubscribe
    for idx, dref in RADIO_DREFS:
        dref_bytes = dref.encode() + b"\x00" * (400 - len(dref))
        pkt = b"RREF\x00" + struct.pack("<ii", 0, idx) + dref_bytes
        sock.sendto(pkt, (xp_ip, xp_port))

    print("Done.")


if __name__ == "__main__":
    main()
