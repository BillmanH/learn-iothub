"""
download_images.py — download the Kaggle surveillance dataset into images/.

Usage (from modules/onvifsim/):
    uv run python download_images.py

Requires a Kaggle API token (~/.kaggle/kaggle.json or KAGGLE_USERNAME /
KAGGLE_KEY environment variables). See:
  https://github.com/Kaggle/kaggle-api#api-credentials
"""
import shutil
from pathlib import Path

import kagglehub

DATASET = "simuletic/surveillance-vlm-weapon-and-knife-detection-dataset"
DEST    = Path(__file__).parent / "images"
EXTS    = {".jpg", ".jpeg", ".png", ".bmp"}


def main():
    print(f"Downloading dataset: {DATASET}")
    src = Path(kagglehub.dataset_download(DATASET))
    print(f"Downloaded to: {src}")

    DEST.mkdir(exist_ok=True)

    copied = 0
    for img in src.rglob("*"):
        if img.is_file() and img.suffix.lower() in EXTS:
            target = DEST / img.name
            # Avoid overwriting if names collide across subdirs
            if target.exists():
                target = DEST / f"{img.parent.name}_{img.name}"
            shutil.copy2(img, target)
            copied += 1

    print(f"Copied {copied} images to {DEST}")


if __name__ == "__main__":
    main()
