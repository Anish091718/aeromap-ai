import shutil
import threading
import traceback

from config import ORTHO_ROOT
from db import set_status
from generate_tiles import generate_tiles
from odm_processor import orthophoto_path, run_odm

_running = set()
_lock = threading.Lock()


def is_running(project_id):
    with _lock:
        return project_id in _running


def start_processing(project_id):
    """Start the full ODM pipeline in a background thread. Returns False if already running."""
    with _lock:
        if project_id in _running:
            return False
        _running.add(project_id)
    threading.Thread(target=_process, args=(project_id,), daemon=True).start()
    return True


def _process(project_id):
    try:
        set_status(project_id, "Processing", 10)
        if not run_odm(project_id):
            set_status(project_id, "Failed", 0)
            return

        set_status(project_id, "Processing", 60)
        out = ORTHO_ROOT / f"project_{project_id}"
        out.mkdir(parents=True, exist_ok=True)
        tif = orthophoto_path(project_id)
        shutil.copy2(tif, out / "orthomosaic.tif")

        generate_tiles(
            tif, out / "tiles",
            progress=lambda f: set_status(project_id, "Processing", 60 + int(39 * f)),
        )
        set_status(project_id, "Completed", 100)
    except Exception:
        traceback.print_exc()
        set_status(project_id, "Failed", 0)
    finally:
        with _lock:
            _running.discard(project_id)


def start_tiles_only(project_id):
    """Build tiles only, from a ready GeoTIFF (static/orthomosaic/project_X/orthomosaic.tif)."""
    with _lock:
        if project_id in _running:
            return False
        _running.add(project_id)
    threading.Thread(target=_tiles_only, args=(project_id,), daemon=True).start()
    return True


def _tiles_only(project_id):
    try:
        set_status(project_id, "Processing", 10)
        out = ORTHO_ROOT / f"project_{project_id}"
        generate_tiles(
            out / "orthomosaic.tif", out / "tiles",
            progress=lambda f: set_status(project_id, "Processing", 10 + int(89 * f)),
        )
        set_status(project_id, "Completed", 100)
    except Exception:
        traceback.print_exc()
        set_status(project_id, "Failed", 0)
    finally:
        with _lock:
            _running.discard(project_id)
