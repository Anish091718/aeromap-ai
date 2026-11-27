from PIL import Image
from PIL.ExifTags import GPSTAGS, TAGS


def get_exif_data(image_path):
    """Return the EXIF tags of an image as a dict, or None if there are none."""
    with Image.open(image_path) as image:
        exif = image.getexif()
        if not exif:
            return None

        exif_data = {TAGS.get(tag, tag): value for tag, value in exif.items()}

        # GPS data lives in its own sub-block (IFD 0x8825).
        gps_ifd = exif.get_ifd(0x8825)
        if gps_ifd:
            exif_data["GPSInfo"] = dict(gps_ifd)

    return exif_data


def get_gps_info(image_path):
    """Return the GPS tags of an image with readable names, or None."""
    exif_data = get_exif_data(image_path)
    if not exif_data:
        return None

    gps_info = exif_data.get("GPSInfo")
    if not gps_info:
        return None

    return {GPSTAGS.get(key, key): value for key, value in gps_info.items()}
