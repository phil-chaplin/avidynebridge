"""Check which CDI/GPS SimVars are readable and writable in MSFS."""
from SimConnect import SimConnect, AircraftRequests, AircraftEvents

sm = SimConnect()
aq = AircraftRequests(sm, _time=0)
ae = AircraftEvents(sm)

# Read current CDI/GPS state
simvars = [
    "HSI_CDI_NEEDLE",
    "HSI_GSI_NEEDLE",
    "HSI_BEARING",
    "HSI_CDI_NEEDLE_VALID:1",
    "HSI_GSI_NEEDLE_VALID:1",
    "HSI_TF_FLAGS:1",
    "NAV_CDI:1",
    "NAV_GSI:1",
    "NAV_OBS:1",
    "NAV_TOFROM:1",
    "GPS_CDI_NEEDLE",
    "GPS_GSI_NEEDLE",
    "GPS_CDI_SCALING",
    "GPS_WP_DESIRED_TRACK",
    "GPS_WP_CROSS_TRK",
    "GPS_DRIVES_NAV1",
    "GPS_IS_ACTIVE_FLIGHT_PLAN",
    "GPS_IS_ACTIVE_WAY_POINT",
    "GPS_COURSE_TO_STEER",
    "AUTOPILOT_HEADING_LOCK_DIR",
]

print("CDI/GPS SimVar values:")
print("-" * 70)
for sv in simvars:
    try:
        val = aq.get(sv)
        print(f"  {sv:<40s} = {val}")
    except Exception as e:
        print(f"  {sv:<40s} ERROR: {e}")

# Check which events exist for CDI/GPS
print("\nChecking events:")
events = [
    "VOR1_SET", "VOR1_OBI_INC", "VOR1_OBI_DEC",
    "GPS_OBS_SET", "GPS_OBS_INC", "GPS_OBS_DEC",
    "AP_NAV1_HOLD", "AP_GPS_HOLD",
    "TOGGLE_GPS_DRIVES_NAV1",
]
for ev_name in events:
    try:
        ev = ae.find(ev_name)
        print(f"  {ev_name:<40s} {'EXISTS' if ev else 'NOT FOUND'}")
    except:
        print(f"  {ev_name:<40s} NOT FOUND")

# Try writing GPS_DRIVES_NAV1
print("\nTrying to set GPS_DRIVES_NAV1 = 1...")
try:
    ev = ae.find("TOGGLE_GPS_DRIVES_NAV1")
    if ev:
        gps_nav = aq.get("GPS_DRIVES_NAV1")
        print(f"  Current: {gps_nav}")
        if gps_nav == 0:
            ev()
            import time; time.sleep(0.3)
            print(f"  After toggle: {aq.get('GPS_DRIVES_NAV1')}")
except Exception as e:
    print(f"  Error: {e}")

sm.exit()
print("\nDone.")
