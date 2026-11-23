import shutil
import subprocess
from pathlib import Path

from config import (
    IMAGE_EXTS, MIN_IMAGES, ODM_EXTRA_ARGS, ODM_IMAGE, ODM_ROOT, UPLOAD_FOLDER,
)


def dataset_dir(project_id) -> Path:
    return ODM_ROOT / f"project_{project_id}"


def orthophoto_path(project_id) -> Path:
    return dataset_dir(project_id) / "project" / "odm_orthophoto" / "odm_orthophoto.tif"


def run_odm(project_id) -> bool:
    """Sirf is project ka dataset reset hota hai, baaki projects safe rehte hain."""
    upload_dir = UPLOAD_FOLDER / f"project_{project_id}"
    if not upload_dir.is_dir():
        print(f"[ODM] Upload folder missing: {upload_dir}")
        return False

    images = [f for f in upload_dir.iterdir() if f.suffix.lower() in IMAGE_EXTS]
    if len(images) < MIN_IMAGES:
        print(f"[ODM] Need at least {MIN_IMAGES} images, found {len(images)}")
        return False

    root = dataset_dir(project_id)
    if root.exists():
        shutil.rmtree(root)
    images_dir = root / "project" / "images"
    images_dir.mkdir(parents=True)
    for img in images:
        shutil.copy2(img, images_dir)

    command = [
        "docker", "run", "--rm",
        "-v", f"{root}:/datasets",
        ODM_IMAGE,
        "--project-path", "/datasets", "project",
        *ODM_EXTRA_ARGS,
    ]

    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace",
        )
        for line in process.stdout:       # live output
            print(line, end="")
        code = process.wait()
    except FileNotFoundError:
        print("[ODM] Docker nahi mila. Docker Desktop install/start karo.")
        return False

    ok = code == 0 and orthophoto_path(project_id).exists()
    print(f"[ODM] finished, exit code={code}, orthophoto={ok}")
    return ok
