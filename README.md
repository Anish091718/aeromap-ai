# AeroMap AI

Flask app: drone images -> orthomosaic -> tiles -> Leaflet map -> area measurement -> PDF/Excel.

## Run (Windows)
1. `setup.bat` (ek baar) — venv banata hai aur packages install karta hai
2. `run.bat` — server chalata hai
3. Browser: http://127.0.0.1:5000/signup

(VS Code terminal me: `python -m venv venv`, `venv\Scripts\activate.bat`, `pip install -r requirements.txt`, `python app.py`)

## Orthomosaic banane ke 2 tareeke
- **Docker + ODM:** project page -> "Generate with ODM" (Docker Desktop chalu hona chahiye)
- **Ready GeoTIFF (Docker nahi):** WebODM Windows installer / Metashape se `.tif` banao, project page -> "Upload Orthomosaic (.tif)"

## Files
- `app.py` routes, `db.py` SQLite, `pipeline.py` background jobs
- `odm_processor.py` Docker ODM, `generate_tiles.py` XYZ tiles
- `config.py` paths, zoom range, ODM options (`ODM_EXTRA_ARGS`)
