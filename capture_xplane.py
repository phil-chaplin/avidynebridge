"""
Reference capture: connect to real X-Plane and log the raw protocol exchange.

1. Joins the BECN multicast group and dumps the beacon bytes.
2. Subscribes to sim/flightmodel/position/latitude (idx=0, 10 Hz)
   — same as the Avidyne app does.
3. Logs every raw reply from X-Plane.

Run this while X-Plane is open. No iPad needed.
Ctrl+C to stop.
"""

import io
import socket
import struct
import sys
import threading
import time
import binascii


MCAST_GRP = "239.255.1.1"
MCAST_PORT = 49707

# Tee: write to both console and log file
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


def listen_beacon(bind_ip):
    """Listen for X-Plane BECN multicast and return (xp_ip, xp_port, raw_packet)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", MCAST_PORT))

    # Join multicast group on the correct interface
    mreq = struct.pack("4s4s",
        socket.inet_aton(MCAST_GRP),
        socket.inet_aton(bind_ip),
    )
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    print(f"[BECN] Listening for X-Plane beacon on {MCAST_GRP}:{MCAST_PORT} (interface {bind_ip})")
    print(f"[BECN] Start X-Plane if it's not already running...\n")

    while True:
        data, addr = sock.recvfrom(1024)
        if data[:4] == b"BECN":
            print(f"[BECN] Received from {addr[0]}:{addr[1]}")
            print(f"[BECN] Raw ({len(data)} bytes):")
            # Hex dump in rows of 16
            for i in range(0, len(data), 16):
                hex_part = " ".join(f"{b:02x}" for b in data[i:i+16])
                ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in data[i:i+16])
                print(f"       {i:04x}  {hex_part:<48s}  {ascii_part}")

            # Parse the known fields
            if len(data) >= 21:  # 5 header + 16 body minimum
                major, minor = struct.unpack_from("<BB", data, 5)
                host_id, version, role, port = struct.unpack_from("<iiIH", data, 7)
                name_raw = data[21:]
                name = name_raw.split(b"\x00")[0].decode("utf-8", errors="replace")
                print(f"\n[BECN] Parsed:")
                print(f"       major={major} minor={minor}")
                print(f"       host_id={host_id} version={version} role={role}")
                print(f"       port={port} name={name!r}")
                print(f"       total_size={len(data)}")
            print()
            return addr[0], port if len(data) >= 21 else 49000, data
        else:
            print(f"[????] Non-BECN multicast from {addr[0]}: {binascii.hexlify(data[:20]).decode()}")


def subscribe_and_capture(xp_ip, xp_port, bind_ip):
    """Subscribe to latitude on real X-Plane and log all replies."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Bind to a specific port so we can see what X-Plane sends back
    sock.bind((bind_ip, 49017))  # arbitrary local port
    sock.settimeout(5.0)

    # Build RREF subscription — same as what the Avidyne app sends
    dref = b"sim/flightmodel/position/latitude"
    dref_padded = dref + b"\x00" * (400 - len(dref))
    pkt = b"RREF\x00" + struct.pack("<ii", 10, 0) + dref_padded  # freq=10, idx=0

    print(f"[SUB]  Subscribing to latitude on {xp_ip}:{xp_port}")
    print(f"[SUB]  Our packet ({len(pkt)} bytes):")
    for i in range(0, min(len(pkt), 48), 16):
        hex_part = " ".join(f"{b:02x}" for b in pkt[i:i+16])
        print(f"       {i:04x}  {hex_part}")
    print()

    sock.sendto(pkt, (xp_ip, xp_port))

    print(f"[RECV] Waiting for replies from X-Plane...\n")

    count = 0
    while count < 30:  # capture ~30 replies then stop
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            print("[RECV] Timeout — no reply received. Is X-Plane running and unpaused?")
            break

        count += 1
        print(f"[RECV] #{count} from {addr[0]}:{addr[1]} ({len(data)} bytes):")
        for i in range(0, len(data), 16):
            hex_part = " ".join(f"{b:02x}" for b in data[i:i+16])
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in data[i:i+16])
            print(f"       {i:04x}  {hex_part:<48s}  {ascii_part}")

        # Try to parse as RREF reply
        if data[:4] == b"RREF" and len(data) >= 13:
            offset = 5
            while offset + 8 <= len(data):
                idx, val = struct.unpack_from("<if", data, offset)
                print(f"       → idx={idx} value={val}")
                offset += 8
        print()

        if count >= 5:
            # After 5 replies, also show our bridge's reply for comparison
            if count == 5:
                print("=" * 60)
                print("[CMP]  For comparison, here's what our bridge would send:")
                our_reply = b"RREF\x00" + struct.pack("<if", 0, -33.8468)
                for i in range(0, len(our_reply), 16):
                    hex_part = " ".join(f"{b:02x}" for b in our_reply[i:i+16])
                    print(f"       {i:04x}  {hex_part}")
                print("=" * 60)
                print()

    # Unsubscribe
    unsub = b"RREF\x00" + struct.pack("<ii", 0, 0) + dref_padded
    sock.sendto(unsub, (xp_ip, xp_port))
    print("[SUB]  Unsubscribed. Done.")


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def main():
    bind_ip = sys.argv[1] if len(sys.argv) > 1 else get_local_ip()
    logfile = "capture.log"
    sys.stdout = Tee(logfile)
    print("=" * 60)
    print("  X-Plane Reference Capture")
    print(f"  Logging to: {logfile}")
    print("=" * 60)
    print(f"  Interface: {bind_ip}")
    print()

    xp_ip, xp_port, becn_raw = listen_beacon(bind_ip)
    subscribe_and_capture(xp_ip, xp_port, bind_ip)


if __name__ == "__main__":
    main()
