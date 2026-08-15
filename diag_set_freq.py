"""Test setting COM1 frequency in MSFS via different methods."""
from SimConnect import SimConnect, AircraftRequests, AircraftEvents

sm = SimConnect()
aq = AircraftRequests(sm, _time=0)
ae = AircraftEvents(sm)

# Read current
cur = aq.get("COM_ACTIVE_FREQUENCY:1")
print(f"Current COM1: {cur} MHz")

target_mhz = 125.9
target_xp = 12590  # X-Plane format

# Method 1: BCD Hz
def to_bcd_hz(freq_mhz):
    freq_hz = int(round(freq_mhz * 1_000_000))
    bcd = 0
    shift = 0
    while freq_hz > 0:
        bcd |= (freq_hz % 10) << shift
        freq_hz //= 10
        shift += 4
    return bcd

# Method 2: BCD without full Hz (some MSFS events want freq in 10kHz BCD)
def to_bcd_10khz(freq_mhz):
    # 125.90 → 12590 (in 10 kHz units) → BCD
    val = int(round(freq_mhz * 100))
    bcd = 0
    shift = 0
    while val > 0:
        bcd |= (val % 10) << shift
        val //= 10
        shift += 4
    return bcd

bcd_hz = to_bcd_hz(target_mhz)
bcd_10k = to_bcd_10khz(target_mhz)

print(f"\nTarget: {target_mhz} MHz")
print(f"  BCD Hz:   0x{bcd_hz:08X} ({bcd_hz})")
print(f"  BCD 10kHz: 0x{bcd_10k:08X} ({bcd_10k})")

# Try Method 1
print(f"\nTrying COM_RADIO_SET with BCD Hz (0x{bcd_hz:08X})...")
try:
    event = ae.find("COM_RADIO_SET")
    event(bcd_hz)
    import time; time.sleep(0.5)
    after = aq.get("COM_ACTIVE_FREQUENCY:1")
    print(f"  Result: {after} MHz {'✓' if abs(after - target_mhz) < 0.01 else '✗'}")
except Exception as e:
    print(f"  Error: {e}")

# Read again
cur = aq.get("COM_ACTIVE_FREQUENCY:1")
print(f"\nCurrent COM1: {cur} MHz")

# Try Method 2
print(f"\nTrying COM_RADIO_SET with BCD 10kHz (0x{bcd_10k:08X})...")
try:
    event = ae.find("COM_RADIO_SET")
    event(bcd_10k)
    import time; time.sleep(0.5)
    after = aq.get("COM_ACTIVE_FREQUENCY:1")
    print(f"  Result: {after} MHz {'✓' if abs(after - target_mhz) < 0.01 else '✗'}")
except Exception as e:
    print(f"  Error: {e}")

# Try COM_RADIO_SET_HZ (plain Hz, no BCD - newer MSFS)
print(f"\nTrying COM_RADIO_SET_HZ with plain Hz ({int(target_mhz * 1_000_000)})...")
try:
    event = ae.find("COM_RADIO_SET_HZ")
    event(int(target_mhz * 1_000_000))
    import time; time.sleep(0.5)
    after = aq.get("COM_ACTIVE_FREQUENCY:1")
    print(f"  Result: {after} MHz {'✓' if abs(after - target_mhz) < 0.01 else '✗'}")
except Exception as e:
    print(f"  Error: {e}")

# Try COM_STBY version too
print(f"\nTrying COM_STBY_RADIO_SET_HZ with plain Hz ({int(118.5 * 1_000_000)})...")
try:
    event = ae.find("COM_STBY_RADIO_SET_HZ")
    event(int(118.5 * 1_000_000))
    import time; time.sleep(0.5)
    after = aq.get("COM_STANDBY_FREQUENCY:1")
    print(f"  Result: {after} MHz {'✓' if abs(after - 118.5) < 0.01 else '✗'}")
except Exception as e:
    print(f"  Error: {e}")

print("\nDone.")
sm.exit()
