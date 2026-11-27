# AeroMap AI

Flask app: drone images -> orthomosaic -> map tiles -> Leaflet map -> area measurement -> PDF/Excel reports,
plus a **live drone view** that shows your drone moving on the map in real time.

## Run (Windows)
1. Run `setup.bat` once. It creates a virtual environment and installs the packages.
2. Run `run.bat` to start the server.
3. Open http://127.0.0.1:5000/signup in your browser.

Manual way (VS Code terminal): `python -m venv venv`, `venv\Scripts\activate.bat`, `pip install -r requirements.txt`, `python app.py`

## Two ways to create an orthomosaic
- **Docker + ODM:** project page -> "Generate with ODM". Docker Desktop must be running.
- **Ready GeoTIFF (no Docker):** make a `.tif` with the WebODM Windows installer or Metashape, then use
  "Upload orthomosaic (.tif)" on the project page.

## Live drone view
Open **Live drone** in the top menu. Choose a source and press **Connect**.

| Source | Use it when | How |
|---|---|---|
| Demo simulator | You want to try the feature | Move the map where you want, press Connect. A virtual drone flies a survey pattern. |
| MAVLink | ArduPilot / PX4 drone, SITL, or a ground station | Enter a connection string (below). |
| This device's GPS | Quick test by walking or driving with a phone | Needs HTTPS or localhost for location access. |
| External app (HTTP) | Any other drone, companion computer, or your own app | POST JSON to `/api/drone/push` with your token (shown on the Live page). |

**MAVLink connection strings**
- Mission Planner / QGroundControl MAVLink forwarding, or SITL: `udpin:0.0.0.0:14550`
- TCP: `tcp:127.0.0.1:5760`
- Telemetry radio or USB cable: `COM5,57600` (Linux: `/dev/ttyUSB0,57600`)

The page shows position, height, speed, heading, battery, satellites and flight mode. The orange line is the flight
path, which you can download as GeoJSON. Pick a project on the Live page to see the drone on top of your
orthomosaic. The **3D flight view** page draws the same path in 3D.

**Important notes**
- DJI drones do not speak MAVLink. For DJI, use a Mobile SDK / companion app that sends the HTTP push, or the phone GPS source.
- Telemetry stays in memory and is not saved to the database. Use "Download path" to keep a flight.
- To open the app from another device on your network, set `HOST=0.0.0.0` before `python app.py`.
  Browsers only allow GPS on HTTPS, so for phone GPS use an HTTPS tunnel (for example Cloudflare Tunnel or ngrok)
  and set `COOKIE_SECURE=1`.
- Set your own `SECRET_KEY` environment variable for production. If it is not set, a random key is created in `database/secret.key`.

## Files
- `app.py` routes, `db.py` SQLite, `pipeline.py` background jobs
- `odm_processor.py` Docker ODM, `generate_tiles.py` XYZ tiles
- `drone_live.py` live telemetry (simulator, MAVLink, HTTP push)
- `config.py` paths, zoom range, ODM options (`ODM_EXTRA_ARGS`)
- `templates/` pages, `static/js/live.js` live map, `static/js/view3d.js` 3D view
