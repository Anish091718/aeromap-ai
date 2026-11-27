import json
import shutil
import sys
from pathlib import Path

import mercantile
import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import ColorInterp, Resampling
from rasterio.transform import from_bounds
from rasterio.warp import reproject, transform_bounds

from config import MAX_ZOOM, MIN_ZOOM, ORTHO_ROOT, TILE_SIZE


def generate_tiles(input_tif, output_dir, min_zoom=MIN_ZOOM, max_zoom=MAX_ZOOM,
                   progress=None):
    """Create XYZ PNG tiles. progress(fraction 0..1) is called after every zoom level."""
    input_tif, output_dir = Path(input_tif), Path(output_dir)
    if not input_tif.exists():
        raise FileNotFoundError(f"GeoTIFF not found: {input_tif}")

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    with rasterio.open(input_tif) as src:
        if src.crs is None:
            raise ValueError("The GeoTIFF has no coordinate system (CRS). Export it with georeferencing.")
        if src.count < 3:
            raise ValueError("The orthophoto needs at least 3 bands (RGB).")
        if src.dtypes[0] != "uint8":
            raise ValueError(f"Only uint8 imagery is supported, found: {src.dtypes[0]}")

        alpha_band = (src.colorinterp.index(ColorInterp.alpha) + 1
                      if ColorInterp.alpha in src.colorinterp else None)
        west, south, east, north = transform_bounds(
            src.crs, "EPSG:4326", *src.bounds, densify_pts=21
        )
        total = max_zoom - min_zoom + 1

        for n, zoom in enumerate(range(min_zoom, max_zoom + 1)):
            written = 0
            for tile in mercantile.tiles(west, south, east, north, zoom):
                b = mercantile.xy_bounds(tile)
                dst_transform = from_bounds(b.left, b.bottom, b.right, b.top,
                                            TILE_SIZE, TILE_SIZE)
                kwargs = dict(
                    src_transform=src.transform, src_crs=src.crs,
                    dst_transform=dst_transform, dst_crs="EPSG:3857",
                    resampling=Resampling.bilinear, dst_nodata=0,
                )

                rgb = np.zeros((3, TILE_SIZE, TILE_SIZE), dtype=np.uint8)
                reproject(source=rasterio.band(src, [1, 2, 3]),
                          destination=rgb, **kwargs)

                if alpha_band:      # use the real alpha band written by ODM
                    alpha = np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.uint8)
                    reproject(source=rasterio.band(src, alpha_band),
                              destination=alpha, **kwargs)
                else:               # fallback: a pixel that is entirely 0 is empty
                    alpha = np.where(rgb.any(axis=0), 255, 0).astype(np.uint8)

                if alpha.max() == 0:    # the whole tile is empty
                    continue

                rgba = np.dstack((rgb[0], rgb[1], rgb[2], alpha))
                path = output_dir / str(zoom) / str(tile.x) / f"{tile.y}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(rgba).save(path)
                written += 1

            print(f"Zoom {zoom}: {written} tiles")
            if progress:
                progress((n + 1) / total)

    (output_dir / "bounds.json").write_text(json.dumps({
        "bounds": [[south, west], [north, east]],
        "min_zoom": min_zoom,
        "max_zoom": max_zoom,
    }))
    return output_dir


if __name__ == "__main__":
    # Usage: python generate_tiles.py <project_id>
    pid = sys.argv[1] if len(sys.argv) > 1 else "1"
    out = ORTHO_ROOT / f"project_{pid}"
    generate_tiles(out / "orthomosaic.tif", out / "tiles")
    print("Done:", (out / "tiles").resolve())
