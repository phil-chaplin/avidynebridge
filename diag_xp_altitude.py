"""Check X-Plane's elevation dataref value and units."""
import socket, struct, sys, time

MCAST_GRP = "239.255.1.1"
MCAST_PORT = 49707

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
    print("Waiting for X-Plane...")
    while True:
        data, addr = sock.recvfrom(1024)
        if data[:4] == b"BECN" and addr[0] == bind_ip:
            port = struct.unpack_from("<H", data, 19)[0]
            sock.close()
            return addr[0], port

bind_ip = sys.argv[1] if len(sys.argv) > 1 else get_local_ip()
xp_ip, xp_port = find_xplane(bind_ip)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((bind_ip, 49018))
sock.settimeout(5)

# Subscribe to the same datarefs the app uses for position
drefs = [
    (0, "sim/flightmodel/position/latitude"),
    (2, "sim/flightmodel/position/elevation"),
    (26, "sim/cockpit2/gauges/indicators/altitude_ft_pilot"),
    (28, "sim/flightmodel/position/vh_ind"),
]

for idx, dref in drefs:
    dref_bytes = dref.encode() + b"\x00" * (400 - len(dref))
    pkt = b"RREF\x00" + struct.pack("<ii", 2, idx) + dref_bytes
    sock.sendto(pkt, (xp_ip, xp_port))

print(f"\nReading from X-Plane at {xp_ip}:{xp_port}...\n")

for _ in range(10):
    try:
        data, addr = sock.recvfrom(4096)
    except socket.timeout:
        break
    if data[:4] == b"RREF":
        offset = 5
        vals = {}
        while offset + 8 <= len(data):
            idx, val = struct.unpack_from("<if", data, offset)
            vals[idx] = val
            offset += 8
        lat = vals.get(0, 0)
        elev = vals.get(2, 0)
        alt_ft = vals.get(26, 0)
        vh = vals.get(28, 0)
        print(f"  latitude={lat:.6f}  elevation={elev:.2f}  altitude_ft={alt_ft:.2f}  vh_ind={vh:.2f}")
        print(f"    elevation in ft: {elev / 0.3048:.2f}")
    time.sleep(0.5)

# Unsubscribe
for idx, dref in drefs:
    dref_bytes = dref.encode() + b"\x00" * (400 - len(dref))
    pkt = b"RREF\x00" + struct.pack("<ii", 0, idx) + dref_bytes
    sock.sendto(pkt, (xp_ip, xp_port))
print("\nDone.")
