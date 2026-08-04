"""Generate a simple app.ico / tray.png for packaging (Pillow)."""

from __future__ import annotations

from pathlib import Path


def main() -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("Pillow required: pip install Pillow")
        return

    root = Path(__file__).resolve().parents[1] / "assets" / "icons"
    root.mkdir(parents=True, exist_ok=True)

    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((16, 16, size - 16, size - 16), fill=(55, 105, 200, 255), outline=(180, 210, 255, 255), width=6)
    draw.ellipse((70, 90, 110, 130), fill=(230, 240, 255, 255))
    draw.ellipse((146, 90, 186, 130), fill=(230, 240, 255, 255))
    draw.arc((80, 130, 176, 190), 20, 160, fill=(230, 240, 255, 255), width=8)

    png = root / "tray.png"
    img.resize((64, 64), Image.Resampling.LANCZOS).save(png)
    img.save(root / "app.png")

    # ICO with multiple sizes
    ico_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(root / "app.ico", sizes=ico_sizes)
    print(f"Wrote icons to {root}")


if __name__ == "__main__":
    main()
