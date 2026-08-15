"""
Transparent UDP proxy between the Avidyne app and real X-Plane.

Sits in the middle, forwards all traffic bidirectionally, logs everything.
Use this to discover what the app sends (especially radio DREF writes).

How it works:
  1. Listens for X-Plane's real BECN to find it
  2. Broadcasts our own BECN (claiming to be X-Plane) so the app connects to us
  3. Forwards app→X-Plane and X-Plane→app, logging both directions

Usage:
    Close X-Plane's beacon first (or run this on a different machine).
    Actually: X-Plane can stay running — we just need to grab the app's
    attention by beaconing with a higher frequency or different name.

    python proxy.py                  # auto-detect interface
    python proxy.py 192.168.1.100   # specify interface
"""

import socket
import struct
import sys
import threading
import time
import binascii

MCAST_GRP = "239.255.1.1"
MCAST_PORT = 49707
PROXY_LISTEN_PORT = 49000  # where the app sends to us
PROXY_REPLY_PORT = 49001   # where we send replies from (mimicking X-Plane)

LOG_FILE = "proxy.log"


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
    """Listen for X-Plane's real beacon and return its address + port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", MCAST_PORT))
    mreq = struct.pack("4s4s", socket.inet_aton(MCAST_GRP), socket.inet_aton(bind_ip))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.settimeout(15)
    print("[PROXY] Waiting for X-Plane beacon...")
    while True:
        data, addr = sock.recvfrom(1024)
        if data[:4] == b"BECN" and addr[0] == bind_ip:
            port = struct.unpack_from("<H", data, 19)[0]
            print(f"[PROXY] Found X-Plane at {addr[0]}:{port}")
            sock.close()
            return addr[0], port, data


def parse_packet(data, direction):
    """Parse and log a packet. Returns a human-readable description."""
    pkt_len = len(data)
    header = data[0:4] if pkt_len >= 4 else b""
    lines = []

    if header == b"RREF":
        sep = data[4:5]
        if sep == b"\x00" and pkt_len >= 413:
            # Subscription packet (client → X-Plane)
            offset = 5
            while offset + 408 <= pkt_len:
                freq, index = struct.unpack_from("<ii", data, offset)
                dref_raw = data[offset + 8 : offset + 408]
                dref = dref_raw.split(b"\x00")[0].decode("utf-8", errors="replace")
                if freq == 0:
                    lines.append(f"  UNSUB   idx={index:<4} {dref}")
                else:
                    lines.append(f"  SUB     idx={index:<4} @{freq:>3}Hz {dref}")
                offset += 408
        elif sep == b"," and pkt_len >= 13:
            # Reply packet (X-Plane → client)
            offset = 5
            pairs = []
            while offset + 8 <= pkt_len:
                idx, val = struct.unpack_from("<if", data, offset)
                pairs.append(f"idx={idx}={val:.4f}")
                offset += 8
            lines.append(f"  RREF reply: {len(pairs)} values")
            # Only show first few to avoid spam
            for p in pairs[:5]:
                lines.append(f"    {p}")
            if len(pairs) > 5:
                lines.append(f"    ... +{len(pairs)-5} more")
        else:
            lines.append(f"  RREF (unknown format, sep=0x{sep.hex()}, len={pkt_len})")

    elif header == b"DREF":
        offset = 5
        if offset + 504 <= pkt_len:
            value = struct.unpack_from("<f", data, offset)[0]
            dref_raw = data[offset + 4 : offset + 504]
            dref = dref_raw.split(b"\x00")[0].decode("utf-8", errors="replace").strip()
            lines.append(f"  *** DREF WRITE: {dref} = {value} ***")
        else:
            lines.append(f"  DREF (short packet, {pkt_len} bytes)")

    elif header == b"CMND":
        cmd = data[5:].split(b"\x00")[0].decode("utf-8", errors="replace")
        lines.append(f"  *** COMMAND: {cmd} ***")

    elif header == b"BECN":
        lines.append(f"  BECN ({pkt_len} bytes)")

    else:
        tag = header.decode("utf-8", errors="replace")
        lines.append(f"  {tag} ({pkt_len} bytes) hex={binascii.hexlify(data[:40]).decode()}")

    return lines


def beacon_thread(bind_ip, listen_port):
    """Broadcast our own beacon so the app connects to us instead of X-Plane."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(bind_ip))

    header = b"BECN\x00"
    body = struct.pack("<BBiiIH", 1, 2, 1, 115501, 1, listen_port)
    name = b"AVIDYNE-PROXY\x00"
    raknet = struct.pack("<H", 49010)
    payload = header + body + name + raknet

    print(f"[BECN] Broadcasting proxy beacon via {bind_ip}, port {listen_port}")
    while True:
        sock.sendto(payload, (MCAST_GRP, MCAST_PORT))
        time.sleep(0.5)


def proxy_app_to_xplane(listen_sock, xp_ip, xp_port, app_addr_holder, reply_port):
    """Receive from app, forward to X-Plane, log everything."""
    fwd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    fwd_sock.bind(("0.0.0.0", 49017))
    fwd_sock.settimeout(0.1)

    reply_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    reply_sock.bind(("0.0.0.0", reply_port))

    sub_count = 0
    rref_reply_count = 0

    while True:
        # Check for data from app
        try:
            listen_sock.settimeout(0.05)
            data, addr = listen_sock.recvfrom(4096)

            # Remember the app's address
            app_addr_holder["addr"] = addr

            lines = parse_packet(data, "APP→XP")
            # Only log non-RREF-sub packets verbosely, or first batch of subs
            header = data[0:4]
            if header == b"DREF" or header == b"CMND":
                print(f"\n{'='*60}")
                print(f"[APP→XP] from {addr[0]}:{addr[1]} ({len(data)} bytes)")
                for l in lines:
                    print(l)
                print(f"{'='*60}")
            elif header == b"RREF" and data[4:5] == b"\x00":
                sub_count += 1
                if sub_count <= 50:
                    for l in lines:
                        print(f"[APP→XP] {l.strip()}")

            # Forward to real X-Plane
            fwd_sock.sendto(data, (xp_ip, xp_port))

        except socket.timeout:
            pass

        # Check for replies from X-Plane
        try:
            data, addr = fwd_sock.recvfrom(4096)
            app_addr = app_addr_holder.get("addr")
            if app_addr:
                # Forward reply to app (from our reply port)
                reply_sock.sendto(data, app_addr)
                rref_reply_count += 1
                if rref_reply_count <= 3:
                    lines = parse_packet(data, "XP→APP")
                    for l in lines:
                        print(f"[XP→APP] {l.strip()}")
                elif rref_reply_count == 4:
                    print("[XP→APP] (suppressing further RREF reply logs...)")
        except socket.timeout:
            pass


def main():
    sys.stdout = Tee(LOG_FILE)

    bind_ip = sys.argv[1] if len(sys.argv) > 1 else get_local_ip()

    print("=" * 60)
    print("  Avidyne ↔ X-Plane Transparent Proxy")
    print(f"  Logging to: {LOG_FILE}")
    print("=" * 60)
    print(f"  Interface: {bind_ip}")
    print()

    # Step 1: Find real X-Plane
    xp_ip, xp_port, real_becn = find_xplane(bind_ip)

    print(f"[PROXY] X-Plane is at {xp_ip}:{xp_port}")
    print(f"[PROXY] We'll listen on :{PROXY_LISTEN_PORT}, forward to X-Plane on :{xp_port}")
    print()
    print("  Now connect the iPad app. Watch for DREF/CMND packets.")
    print("  Try changing frequencies on the IFD.")
    print("  Ctrl+C to stop.")
    print()

    # We need X-Plane to NOT be listening on 49000 since we need that port.
    # This is tricky... X-Plane binds 49000. We can't both bind it.
    # SOLUTION: Run this AFTER closing X-Plane, then reopen X-Plane on a different port.
    # OR: We listen on a different port and adjust the beacon.
    # Actually, X-Plane IS on 49000. We need a different approach.

    # Plan B: Don't bind 49000. Instead, beacon with a different port (49050),
    # and listen there. X-Plane keeps 49000.
    proxy_port = 49050
    proxy_reply_port = 49051
    print(f"[PROXY] NOTE: X-Plane owns :{xp_port}, so proxy listens on :{proxy_port}")
    print(f"[PROXY] Adjusting beacon to advertise port {proxy_port}")
    print()

    listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listen_sock.bind(("0.0.0.0", proxy_port))

    app_addr_holder = {}

    t_beacon = threading.Thread(target=beacon_thread, args=(bind_ip, proxy_port), daemon=True)
    t_proxy = threading.Thread(target=proxy_app_to_xplane,
                               args=(listen_sock, xp_ip, xp_port, app_addr_holder, proxy_reply_port),
                               daemon=True)

    t_beacon.start()
    t_proxy.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
