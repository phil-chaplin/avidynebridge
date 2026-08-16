# Avidyne IFD Trainer XP <-> MSFS 2020 Bridge

Connects the [Avidyne IFD Trainer XP](https://apps.apple.com/us/app/ifd-trainer-xp/id1580754515) iPad app to Microsoft Flight Simulator 2020 (and presumably 2024, untested) by impersonating X-Plane on the network.

## Requirements

- Windows PC running MSFS 2020 (or 2024)
- iPad running Avidyne IFD Trainer XP on the same Wi-Fi network
- Python 3.8+ (if running from source)
- UDP port 49000 free (close X-Plane if running)
- Windows Firewall must allow inbound UDP 49000

## Installation

### From source

```
pip install SimConnect
python bridge.py
```

### Standalone exe

Download `AvidyneBridge.exe` from releases — no Python installation needed.

## Usage

```
AvidyneBridge.exe                         # auto-detect network interface
AvidyneBridge.exe 192.168.1.100           # specify network interface
AvidyneBridge.exe --fake                  # test mode (no MSFS needed)
AvidyneBridge.exe --log bridge.log        # write detailed log to file
```

1. Start MSFS and load into a flight
2. Run the bridge — wait for "Connected to MSFS" in the console
3. Open the IFD Trainer XP app on the iPad — it should connect automatically

**Important:** Start MSFS and the bridge before opening the app. The app loads initial values (radios, altitude) at connection time — if it connects before MSFS data is available, it may show incorrect values or TAWS errors. If this happens, close and reopen the app.

The bridge will reconnect automatically if MSFS is restarted, but the app should be reopened afterward.

## What works

- **Map tracking** — IFD map follows the MSFS aircraft in real time
- **Flight instruments** — airspeed, altitude, heading, attitude, vertical speed
- **Radio frequencies** — COM and NAV frequencies sync bidirectionally between the IFD and MSFS
- **HSI source** — switching between GPS and NAV on the IFD syncs to MSFS
- **Heading bug steering** — set a Direct-To on the IFD, engage HDG mode on the MSFS autopilot, and the aircraft follows the IFD's desired track

## Known limitations

- **CDI needle coupling** — MSFS does not expose CDI needle override via SimConnect. The heading bug workaround is used instead.
- **Flight plan sync** — the IFD manages its own internal FMS. Route/waypoint data is not exported, so MSFS cannot mirror the IFD's flight plan.
- **Nav ident** — MSFS doesn't expose individual NAV ident characters via SimConnect.

## Troubleshooting

**App doesn't connect:**
- Check the iPad is on the same network as the PC (not a guest network)
- Check Windows Firewall allows UDP 49000
- Try specifying your LAN IP: `AvidyneBridge.exe 192.168.x.x`
- Make sure X-Plane is not running (it also uses port 49000)

**App connects but shows no data / TAWS errors:**
- Make sure MSFS is running and loaded into a flight (not the menu)
- Check the bridge console — it should say "Connected to MSFS"
- Close and reopen the app — it must connect after MSFS data is flowing

**Detailed diagnostics:**
- Run with `--log bridge.log` and check the log file

## License

AGPL 3.0 — see [LICENSE](LICENSE).
