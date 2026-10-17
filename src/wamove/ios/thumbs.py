from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

SIDE = 72


def dimensions(source: str) -> tuple[int, int] | None:
    try:
        with Image.open(source) as picture:
            return ImageOps.exif_transpose(picture).size
    except (OSError, ValueError, UnidentifiedImageError):
        return None


def image(source: str, target: Path) -> tuple[int, int] | None:
    try:
        with Image.open(source) as picture:
            oriented = ImageOps.exif_transpose(picture)
            size = oriented.size
            thumbnail = ImageOps.fit(oriented.convert("RGB"), (SIDE, SIDE), Image.Resampling.LANCZOS)
            thumbnail.save(target, format="JPEG")
            return size
    except (OSError, ValueError, UnidentifiedImageError):
        target.unlink(missing_ok=True)
        return None


def video(source: str, target: Path) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        done = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-y", "-i", source, "-frames:v", "1", "-vf",
                               f"crop=min(iw\\,ih):min(iw\\,ih),scale={SIDE}:{SIDE}", "-f", "image2", "-c:v", "mjpeg",
                               "-q:v", "4", str(target)], capture_output=True, timeout=30)
        if done.returncode == 0 and target.is_file():
            return True
    except (OSError, subprocess.TimeoutExpired):
        pass
    target.unlink(missing_ok=True)
    return False
