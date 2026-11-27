import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

DATABASE = BASE_DIR / "database" / "users.db"
UPLOAD_FOLDER = BASE_DIR / "static" / "uploads"
ODM_ROOT = BASE_DIR / "odm_datasets"              # one separate folder per project
ORTHO_ROOT = BASE_DIR / "static" / "orthomosaic"  # tiles + GeoTIFF are stored here


def _load_secret_key():
    """Use SECRET_KEY from the environment, otherwise create and reuse a random key file."""
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key
    key_file = BASE_DIR / "database" / "secret.key"
    key_file.parent.mkdir(parents=True, exist_ok=True)
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_hex(32)
    key_file.write_text(key)
    return key


SECRET_KEY = _load_secret_key()

# OpenDroneMap
ODM_IMAGE = "opendronemap/odm:latest"
ODM_EXTRA_ARGS = []   # e.g. ["--fast-orthophoto", "--orthophoto-resolution", "5"]
MIN_IMAGES = 2
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

# Tiles
TILE_SIZE = 256
MIN_ZOOM = 14
MAX_ZOOM = 20   # zoom 21 makes the tile count more than 2x larger; raise it only if you need it

# Live drone tracking
DRONE_TRACK_MAX_POINTS = 5000   # flight-path points kept in memory per user
DRONE_STALE_SECONDS = 5         # no new position for this long -> "No signal"
