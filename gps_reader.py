from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS


def get_exif_data(image_path):
    image = Image.open(image_path)
    exif = image._getexif()

    if not exif:
        return None

    exif_data = {}

    for tag, value in exif.items():
        decoded = TAGS.get(tag, tag)
        exif_data[decoded] = value

    return exif_data


def get_gps_info(image_path):
    exif_data = get_exif_data(image_path)

    if not exif_data:
        return None

    gps_info = exif_data.get("GPSInfo")

    if not gps_info:
        return None

    gps_data = {}

    for key in gps_info:
        name = GPSTAGS.get(key, key)
        gps_data[name] = gps_info[key]

    return gps_data