"""Draws assets/icon.png (1024px): a page with text lines and a typing cursor."""
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024
OUT = Path(__file__).resolve().parent.parent / "assets" / "icon.png"


def main():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # macOS-style rounded square with a margin, flat accent fill
    m = 100
    d.rounded_rectangle((m, m, S - m, S - m), radius=185, fill=(35, 131, 226, 255))
    # the page
    px0, py0, px1, py1 = 300, 230, 724, 794
    fold = 110
    d.polygon([(px0, py0 + 28), (px0, py1 - 28), (px0 + 28, py1), (px1 - 28, py1), (px1, py1 - 28),
               (px1, py0 + fold), (px1 - fold, py0), (px0 + 28, py0)], fill=(255, 255, 255, 255))
    d.polygon([(px1 - fold, py0), (px1 - fold, py0 + fold - 18), (px1 - fold + 18, py0 + fold), (px1, py0 + fold)],
              fill=(196, 222, 248, 255))
    # text lines
    ink = (55, 53, 47, 255)
    soft = (190, 200, 212, 255)
    y = 380
    for w, c in ((300, ink), (250, soft), (280, soft)):
        d.rounded_rectangle((360, y, 360 + w, y + 34), radius=17, fill=c)
        y += 84
    # an edited word with a cursor
    d.rounded_rectangle((350, y - 14, 560, y + 50), radius=12, fill=(220, 236, 252, 255))
    d.rounded_rectangle((372, y, 520, y + 34), radius=17, fill=(35, 131, 226, 255))
    d.rounded_rectangle((540, y - 30, 552, y + 66), radius=6, fill=(35, 131, 226, 255))
    OUT.parent.mkdir(exist_ok=True)
    img.save(OUT)
    img.resize((256, 256), Image.LANCZOS).save(OUT.with_name("icon-256.png"))


if __name__ == "__main__":
    main()
