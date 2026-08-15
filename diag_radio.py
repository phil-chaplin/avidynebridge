"""Quick diagnostic: read radio frequency SimVars from MSFS and print raw values."""
from SimConnect import SimConnect, AircraftRequests

sm = SimConnect()
aq = AircraftRequests(sm, _time=0)

simvars = [
    "COM_ACTIVE_FREQUENCY:1",
    "COM_STANDBY_FREQUENCY:1",
    "NAV_ACTIVE_FREQUENCY:1",
    "NAV_STANDBY_FREQUENCY:1",
    "NAV_ACTIVE_FREQUENCY:2",
]

for sv in simvars:
    val = aq.get(sv)
    print(f"  {sv:40s} = {val!r}")

sm.exit()
