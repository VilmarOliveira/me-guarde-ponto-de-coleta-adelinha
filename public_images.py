"""Bounded derivative cache; uploaded originals remain untouched."""

from functools import lru_cache
from io import BytesIO
from PIL import Image, ImageOps


@lru_cache(maxsize=16)
def banner_version(data, width):
    with Image.open(BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source)
        original = image.size
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
        image.thumbnail(
            (width, max(1, round(image.height * width / image.width))),
            Image.Resampling.LANCZOS,
        )
        output = BytesIO()
        image.save(output, "WEBP", quality=86, method=4)
        result = output.getvalue()
        if image.size == original and len(result) >= len(data):
            return data, Image.MIME[source.format]
        return result, "image/webp"
