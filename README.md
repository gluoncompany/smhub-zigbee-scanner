# Zigbee Scanner for SMHUB

A small app for **SMLIGHT SMHUB OS** that brings the "Zigbee channel energy" tool of the SLZB-06 coordinators to the SMHUB, and goes a bit further:

- **Finds nearby Zigbee networks** (active beacon scan): channel, PAN ID, Extended PAN ID, RSSI, LQI, permit-join.
- **Measures energy on channels 11–26** (min / average / peak over several passes).
- **Ranks the channels** from best to worst, taking into account noise, other Zigbee networks on the channel and whether the channel is one of the "safe" ones (11 / 15 / 20 / 25).
- Shows up in the hub panel like any other app (sidebar + iframe), with a **Spanish / English** UI and light/dark theme.
- **Stops Zigbee2MQTT during the scan and restarts it automatically** (only if it was running).

## Requirements

- SMHUB with **SMHUB OS 1.0.x** (tested on a **SMHUB Nano MG24**, SMHUB OS 1.0.2).
- A Silicon Labs radio running **EmberZNet (EZSP)** firmware, as used by Zigbee2MQTT with `adapter: ember`. TI (CC2652) and Thread/RCP firmware are not supported.
- No extra dependencies: Python 3 standard library only (the SMHUB CPU is RISC-V and `pip` packages with native code are not available).

## How it works

| File | Purpose |
|---|---|
| `app/scanner.py` | Minimal ASH + EZSP implementation: reset, version handshake, `startScan` (active and energy scans), result parsing and channel ranking |
| `app/server.py` | HTTP server and API (`/api/status`, `POST /api/scan`, `/api/result`). Reads the serial port and baud rate from the Zigbee2MQTT configuration and controls the `zigbee2mqtt` OpenRC service |
| `app/index.html` | Web UI (ES/EN) |
| `control/` | opkg metadata: `control`, `openrc`, `schema.json` (`iframe_port: 8099`), `postinst`, `prerm`, `postrm` (using `/usr/lib/smhub/service-helpers.sh`) |
| `build.py` | Builds the `.ipk` and an opkg feed index (`Packages`, `Packages.gz`) with the Python standard library |
| `install-root.sh` | Installs the package through a local opkg feed and restarts `smhub-services` so the app is registered in the panel |

`smhub-services` registers apps from the opkg feed indexes at startup, so the package is installed from a local feed (`/opt/localfeed`) rather than as a loose `.ipk`.

Ranking score (lower is better):

```
score = average + 0.25 × (peak − average) + 8 × networks_on_channel + 4 (if not 11/15/20/25) + 2 (if channel 26)
```

## Install on a SMHUB

From the hub web console (or SSH), with this repository copied to `~/smhub-zigbee-scanner`:

```bash
cd ~/smhub-zigbee-scanner
python3 build.py
sudo sh install-root.sh
```

Reload the panel: **zigbee-scanner** appears under Apps and in the sidebar. It can also be opened at `http://<hub-ip>:8099`.

## Uninstall

```bash
sudo opkg remove zigbee-scanner
sudo sed -i '\#file:///opt/localfeed#d' /etc/opkg/smlight.conf
sudo rc-service smhub-services restart
```

## License

MIT, see [LICENSE](LICENSE).

## Notes

- This is not an official SMLIGHT app. The app format was worked out by inspecting SMHUB OS 1.0.2 and may change in future releases.
- The scan needs exclusive access to the radio, so Zigbee2MQTT is offline for 1–2 minutes while it runs.
