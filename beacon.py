"""
Stage 1+2: Fake X-Plane beacon + listener.

Broadcasts a BECN multicast packet so the Avidyne IFD Trainer XP app
discovers us, then logs everything the app sends to :49000.

Usage:
    python beacon.py                  # auto-detect LAN interface
    python beacon.py 192.168.1.100    # force a specific interface IP
"""

import socket
import struct
import sys
import threading
import time
import binascii


MCAST_GRP = "239.255.1.1"
MCAST_PORT = 49707
LISTEN_PORT = 49000


def get_local_ip():
    """Best-effort guess at the LAN-facing IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def list_interfaces():
    """Print available interfaces to help the user pick."""
    import ipaddress
    print("\nAvailable interfaces:")
    for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        ip = info[4][0]
        if not ip.startswith("127."):
            print(f"  {ip}")
    print()


def build_becn_payload(listen_port):
    """
    Build an X-Plane 11 BECN packet.

    Layout (after the 5-byte 'BECN\x00' prologue):
        uint8   beacon_major_version    (1)
        uint8   beacon_minor_version    (2)
        int32   application_host_id     (1 = X-Plane)
        int32   version_number          (115501 = 11.55r1)
        uint32  role                    (1 = master)
        uint16  port                    (the UDP port we're listening on)
        char[]  host name, null-terminated
    """
    header = b"BECN\x00"
    body = struct.pack("<BBiiIH",
        1,        # beacon_major_version
        2,        # beacon_minor_version
        1,        # application_host_id: 1 = X-Plane
        115501,   # version_number: 11.55r1
        1,        # role: 1 = master
        listen_port,
    )
    name = b"AVIDYNE-BRIDGE\x00"
    return header + body + name


def beacon_thread(bind_ip):
    """Broadcast BECN multicast once per second."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)

    # Force multicast out the correct interface
    sock.setsockopt(
        socket.IPPROTO_IP,
        socket.IP_MULTICAST_IF,
        socket.inet_aton(bind_ip),
    )

    payload = build_becn_payload(LISTEN_PORT)

    print(f"[BECN] Beaconing on {MCAST_GRP}:{MCAST_PORT} via {bind_ip}")
    print(f"[BECN] Claiming to listen on :{LISTEN_PORT}")
    print(f"[BECN] Packet ({len(payload)} bytes): {binascii.hexlify(payload).decode()}")
    print()

    while True:
        sock.sendto(payload, (MCAST_GRP, MCAST_PORT))
        time.sleep(1)


def listener_thread():
    """Bind :49000 and log everything the app sends."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", LISTEN_PORT))
    print(f"[LISTEN] Waiting for packets on :{LISTEN_PORT}")
    print()

    while True:
        data, addr = sock.recvfrom(4096)
        src = f"{addr[0]}:{addr[1]}"
        header = data[0:4]

        if header == b"RREF":
            # RREF: 5-byte header, then repeating (freq:int, index:int, dataref:400s)
            # But subscription packets have: freq(4) + index(4) + dataref(400) = 408 per entry
            offset = 5
            while offset + 408 <= len(data):
                freq, index = struct.unpack_from("<ii", data, offset)
                dref_raw = data[offset + 8 : offset + 408]
                dref = dref_raw.split(b"\x00")[0].decode("utf-8", errors="replace")
                if freq == 0:
                    print(f"[{src}] UNSUBSCRIBE idx={index:<4}  {dref}")
                else:
                    print(f"[{src}] SUBSCRIBE   idx={index:<4} @ {freq:>3} Hz  {dref}")
                offset += 408

        elif header == b"DREF":
            # DREF: 5-byte header, then float(4) + dataref(500)
            offset = 5
            if offset + 504 <= len(data):
                value = struct.unpack_from("<f", data, offset)[0]
                dref_raw = data[offset + 4 : offset + 504]
                dref = dref_raw.split(b"\x00")[0].decode("utf-8", errors="replace").strip()
                print(f"[{src}] WRITE       {dref} = {value}")

        elif header == b"CMND":
            # CMND: 5-byte header + null-terminated command string
            cmd = data[5:].split(b"\x00")[0].decode("utf-8", errors="replace")
            print(f"[{src}] COMMAND     {cmd}")

        else:
            tag = header.decode("utf-8", errors="replace")
            print(f"[{src}] UNKNOWN     tag={tag}  hex={binascii.hexlify(data[:40]).decode()}")


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__.strip())
        list_interfaces()
        return

    if len(sys.argv) > 1:
        bind_ip = sys.argv[1]
    else:
        bind_ip = get_local_ip()

    print("=" * 60)
    print("  Avidyne IFD ↔ MSFS Bridge — Stage 1+2")
    print("  Beacon + Listener")
    print("=" * 60)
    print()
    print(f"  Binding to: {bind_ip}")
    print(f"  Beacon:     {MCAST_GRP}:{MCAST_PORT}")
    print(f"  Listener:   0.0.0.0:{LISTEN_PORT}")
    print()
    print("  Ctrl+C to stop")
    print()

    t_beacon = threading.Thread(target=beacon_thread, args=(bind_ip,), daemon=True)
    t_listen = threading.Thread(target=listener_thread, daemon=True)

    t_beacon.start()
    t_listen.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
