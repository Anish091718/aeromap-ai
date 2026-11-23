import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-change-me")

DATABASE = BASE_DIR / "database" / "users.db"
UPLOAD_FOLDER = BASE_DIR / "static" / "uploads"
ODM_ROOT = BASE_DIR / "odm_datasets"            # har project ka alag folder
ORTHO_ROOT = BASE_DIR / "static" / "orthomosaic"  # tiles + tif yaha jayenge

# OpenDroneMap
ODM_IMAGE = "opendronemap/odm:latest"
ODM_EXTRA_ARGS = []   # e.g. ["--fast-orthophoto", "--orthophoto-resolution", "5"]
MIN_IMAGES = 2
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

# Tiles
TILE_SIZE = 256
MIN_ZOOM = 14
MAX_ZOOM = 20   # 21 pe tiles ki sankhya 2x se zyada ho jati hai; zarurat ho to badha lo
