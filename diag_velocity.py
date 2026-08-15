"""Check which velocity/acceleration SimVars are available."""
from SimConnect import SimConnect, AircraftRequests

sm = SimConnect()
aq = AircraftRequests(sm, _time=0)

candidates = [
    "VELOCITY_WORLD_X", "VELOCITY_WORLD_Y", "VELOCITY_WORLD_Z",
    "VELOCITY_BODY_X", "VELOCITY_BODY_Y", "VELOCITY_BODY_Z",
    "ACCELERATION_WORLD_X", "ACCELERATION_WORLD_Y", "ACCELERATION_WORLD_Z",
    "ACCELERATION_BODY_X", "ACCELERATION_BODY_Y", "ACCELERATION_BODY_Z",
    "GROUND_VELOCITY",
    "GPS_GROUND_SPEED",
    "GPS_POSITION_LAT", "GPS_POSITION_LON", "GPS_POSITION_ALT",
    "AUTOPILOT_GPS_DRIVING", "GPS_DRIVES_NAV1",
    "HSI_CDI_NEEDLE_VALID:1", "HSI_CDI_NEEDLE:1",
    "GPS_WP_DESIRED_TRACK",
]

for sv in candidates:
    try:
        val = aq.get(sv)
        status = "OK" if val is not None else "NONE"
        print(f"  {sv:<40} = {str(val):>20}  [{status}]")
    except Exception as e:
        print(f"  {sv:<40} = {'ERROR':>20}  [{e}]")

sm.exit()
