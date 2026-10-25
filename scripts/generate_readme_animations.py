"""Generate the README's original animated artwork (requires Pillow).

Run: python -m pip install Pillow && python scripts/generate_readme_animations.py
These are documentation assets; Pillow is not a downloader dependency.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
NAVY = "#111e32"
WHITE = "#f7f9fc"
MUTED = "#b4c1d5"
ORANGE = "#ffbd69"
CYAN = "#71ded7"


def font(size: int, *, bold: bool = False):
    candidates = (
        Path("C:/Windows/Fonts") / ("segoeuib.ttf" if bold else "segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu")
        / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Supplemental") / ("Arial Bold.ttf" if bold else "Arial.ttf"),
    )
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def sparkle(draw, x: float, y: float, radius: float, color) -> None:
    draw.polygon(
        [
            (x, y - radius),
            (x + radius * 0.3, y - radius * 0.3),
            (x + radius, y),
            (x + radius * 0.3, y + radius * 0.3),
            (x, y + radius),
            (x - radius * 0.3, y + radius * 0.3),
            (x - radius, y),
            (x - radius * 0.3, y - radius * 0.3),
        ],
        fill=color,
    )


def coffee_frame(phase: float) -> Image.Image:
    image = Image.new("RGBA", (800, 176))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((4, 8, 796, 168), radius=36, fill=NAVY, outline=ORANGE, width=2)
    draw.rounded_rectangle((24, 26, 30, 150), radius=3, fill=ORANGE)
    draw.ellipse((48, 34, 160, 146), fill="#293648")
    bob = math.sin(phase * math.tau) * 2
    draw.rounded_rectangle((128, 82 + bob, 150, 112 + bob), radius=10, outline=ORANGE, width=5)
    draw.rounded_rectangle((77, 77 + bob, 133, 124 + bob), radius=10, fill=ORANGE)
    draw.ellipse((77, 74 + bob, 133, 88 + bob), fill="#ffe0b1")
    draw.ellipse((84, 78 + bob, 126, 84 + bob), fill="#795441")
    draw.text((105, 97 + bob), "</>", font=font(18, bold=True), fill=NAVY, anchor="mm")
    draw.ellipse((70, 129, 143, 135), fill="#647080")
    for index, x in enumerate((89, 105, 121)):
        points = [
            (
                x + math.sin(step / 5 + phase * math.tau + index) * 3,
                69 - step * 1.5,
            )
            for step in range(16)
        ]
        draw.line(points, fill="#d5bfa6", width=2)
    draw.text((190, 39), "Fuel the next download", font=font(36, bold=True), fill=WHITE)
    draw.text((192, 96), "Buy me a coffee  ·  PayPal", font=font(24), fill=ORANGE)
    for index, (x, y) in enumerate(((655, 42), (686, 133), (612, 120))):
        size = 3 + (1 + math.sin(phase * math.tau + index * 2)) * 1.8
        sparkle(draw, x, y, size, ORANGE if index != 1 else CYAN)
    arrow_x = 738 + math.sin(phase * math.tau) * 2
    draw.ellipse((arrow_x - 25, 63, arrow_x + 25, 113), fill=ORANGE)
    draw.line((arrow_x - 10, 88, arrow_x + 10, 88), fill=NAVY, width=3)
    draw.line((arrow_x + 2, 80, arrow_x + 10, 88, arrow_x + 2, 96), fill=NAVY, width=3)
    return image


def flow_frame(phase: float) -> Image.Image:
    image = Image.new("RGBA", (800, 92))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((1, 1, 799, 91), radius=22, fill=NAVY)
    for x, label in ((112, "LIVE WEBSITE"), (400, "LOCAL COPY"), (688, "OFFLINE READY")):
        draw.text((x, 23), label, font=font(19, bold=True), fill=MUTED, anchor="mm")
    draw.line((127, 61, 673, 61), fill="#36465e", width=2)
    # Moving packets travel along the line; labels and icons stay legible in every frame.
    for offset, radius in ((0, 5), (0.035, 3), (0.07, 2)):
        progress = (phase - offset) % 1
        x = 132 + progress * 536
        draw.ellipse((x - radius, 61 - radius, x + radius, 61 + radius), fill=CYAN)
    draw.ellipse((99, 48, 125, 74), outline=CYAN, width=2)
    draw.ellipse((106, 48, 118, 74), outline=CYAN, width=2)
    draw.line((100, 61, 124, 61), fill=CYAN, width=2)
    draw.rounded_rectangle((383, 49, 416, 74), radius=4, fill=NAVY, outline=ORANGE, width=2)
    draw.rounded_rectangle((383, 45, 399, 54), radius=3, fill=ORANGE)
    draw.line((385, 55, 414, 55), fill=ORANGE, width=2)
    draw.ellipse((673, 46, 703, 76), fill="#203f44", outline=CYAN, width=2)
    draw.line((680, 61, 686, 67, 697, 55), fill=CYAN, width=3)
    return image


def save_gif(frames: list[Image.Image], path: Path) -> None:
    # One palette prevents color flicker. Index zero is reserved for transparent corners.
    sample = Image.new("RGB", (frames[0].width, frames[0].height * 4))
    for index, frame in enumerate(frames[:: len(frames) // 4][:4]):
        sample.paste(frame.convert("RGB"), (0, index * frame.height))
    colors = sample.quantize(colors=127).getpalette()[: 127 * 3]
    palette = Image.new("P", (1, 1))
    palette.putpalette(([255, 0, 255, *colors] + [0] * 768)[:768])
    indexed = []
    for frame in frames:
        converted = frame.convert("RGB").quantize(palette=palette, dither=Image.Dither.NONE)
        converted.paste(
            0, mask=frame.getchannel("A").point(lambda value: 255 if value < 128 else 0)
        )
        indexed.append(converted)
    path.parent.mkdir(parents=True, exist_ok=True)
    indexed[0].save(
        path,
        save_all=True,
        append_images=indexed[1:],
        duration=100,
        loop=0,
        transparency=0,
        disposal=2,
        optimize=False,
    )
    print(f"{path.relative_to(ROOT)}: {path.stat().st_size:,} bytes, {len(frames)} frames")


def main() -> None:
    count = 32
    for name, render in (("coffee-button.gif", coffee_frame), ("mirror-flow.gif", flow_frame)):
        save_gif([render(frame / count) for frame in range(count)], ROOT / "docs" / name)


if __name__ == "__main__":
    main()
