"""Read every SimVar the bridge uses and show raw values."""
from SimConnect import SimConnect, AircraftRequests
import math

sm = SimConnect()
aq = AircraftRequests(sm, _time=0)

simvars = [
    # Position
    "PLANE_LATITUDE",
    "PLANE_LONGITUDE",
    "PLANE_ALTITUDE",
    # Velocity
    "VELOCITY_BODY_X",
    "VELOCITY_BODY_Y",
    "VELOCITY_BODY_Z",
    # Attitude
    "PLANE_PITCH_DEGREES",
    "PLANE_BANK_DEGREES",
    "PLANE_HEADING_DEGREES_MAGNETIC",
    "INCIDENCE_BETA",
    # Rates
    "TURN_INDICATOR_RATE",
    "ROTATION_VELOCITY_BODY_X",
    "ROTATION_VELOCITY_BODY_Y",
    "ROTATION_VELOCITY_BODY_Z",
    # Acceleration
    "ACCELERATION_BODY_X",
    "ACCELERATION_BODY_Y",
    "ACCELERATION_BODY_Z",
    # Air data
    "AIRSPEED_TRUE",
    "AIRSPEED_INDICATED",
    "INDICATED_ALTITUDE",
    "INCIDENCE_ALPHA",
    "VERTICAL_SPEED",
    "KOHLSMAN_SETTING_HG",
    # Weather
    "AMBIENT_TEMPERATURE",
    "TOTAL_AIR_TEMPERATURE",
    # NAV
    "NAV_TOFROM:1",
    "NAV_CDI:1",
    "NAV_GSI:1",
    "NAV_OBS:1",
    # Autopilot
    "AUTOPILOT_GPS_DRIVING",
    "AUTOPILOT_APPROACH_HOLD",
    "HSI_CDI_NEEDLE_VALID:1",
    # Radios
    "COM_ACTIVE_FREQUENCY:1",
    "COM_STANDBY_FREQUENCY:1",
    "NAV_ACTIVE_FREQUENCY:1",
    "NAV_STANDBY_FREQUENCY:1",
    "NAV_ACTIVE_FREQUENCY:2",
]

print(f"{'SimVar':<45} {'Raw Value':>15}  Notes")
print("-" * 80)

for sv in simvars:
    try:
        val = aq.get(sv)
        notes = ""
        if val is None:
            notes = "*** NONE ***"
        elif "PITCH" in sv or "BANK" in sv or "HEADING" in sv or "ALPHA" in sv or "BETA" in sv:
            if val is not None:
                notes = f"(as deg: {math.degrees(val):.2f})" if abs(val) < 10 else ""
        elif "FREQUENCY" in sv:
            if val is not None:
                notes = f"(×100: {int(round(val * 100))})"
        elif "ALTITUDE" in sv and val is not None:
            notes = f"(as m: {val * 0.3048:.1f})" if abs(val) > 10 else ""
        print(f"  {sv:<43} {str(val):>15}  {notes}")
    except Exception as e:
        print(f"  {sv:<43} {'ERROR':>15}  {e}")

sm.exit()
