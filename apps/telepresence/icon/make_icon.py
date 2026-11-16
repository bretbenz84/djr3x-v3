"""Draws Rex Call's icon: an original droid face made from Rex's own lights (the visor over his eyes,
the eyes in the telepresence magenta, the bar mouth that moves with speech) and a call signal.

    .venv/bin/python apps/telepresence/icon/make_icon.py

Writes icon/AppIcon.png (1024 px) and Resources/AppIcon.icns, which build.sh puts in the app.
Drawn at 4x and scaled down for smooth edges. Needs Pillow (in the v3 environment) and macOS's
sips and iconutil.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
S = 4                      # Supersampling.
N = 1024 * S


def px(v):
    return int(v * S)


def vertical_gradient(size, top, bottom):
    w, h = size
    column = Image.new('RGB', (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        column.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    return column.resize((w, h))


def mask_rounded(size, box, radius):
    mask = Image.new('L', size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
    return mask


def glow(canvas, center, radius, color, strength=1.0):
    layer = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx, cy = center
    for i in range(12, 0, -1):
        r = radius * (1 + i * 0.12)
        alpha = int(255 * strength * (0.05 + 0.02 * (12 - i)) / 1.3)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color + (alpha,))
    layer = layer.filter(ImageFilter.GaussianBlur(px(18)))
    canvas.alpha_composite(layer)


def draw():
    icon = Image.new('RGBA', (N, N), (0, 0, 0, 0))
    # macOS icon grid: an 824 px rounded square centred in 1024, with a soft shadow.
    tile = (px(100), px(100), px(924), px(924))
    shadow = Image.new('RGBA', (N, N), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((tile[0], tile[1] + px(14), tile[2], tile[3] + px(14)),
                                             radius=px(185), fill=(0, 0, 0, 110))
    icon.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(px(16))))
    background = vertical_gradient((N, N), (30, 36, 64), (12, 14, 28)).convert('RGBA')
    icon.paste(background, (0, 0), mask_rounded((N, N), tile, px(185)))

    # The head: a warm white dome, wider than tall, lit from above.
    head_box = (px(205), px(255), px(819), px(800))
    head = vertical_gradient((N, N), (246, 242, 234), (186, 182, 176)).convert('RGBA')
    icon.paste(head, (0, 0), mask_rounded((N, N), head_box, px(250)))
    d = ImageDraw.Draw(icon)
    # A seam and two side ear pods, in his orange accent.
    d.rounded_rectangle((px(168), px(470), px(222), px(620)), radius=px(26), fill=(232, 112, 36))
    d.rounded_rectangle((px(802), px(470), px(856), px(620)), radius=px(26), fill=(232, 112, 36))
    d.line((px(512), px(262), px(512), px(330)), fill=(160, 156, 150), width=px(6))

    # The face plate: dark glass.
    face_box = (px(268), px(385), px(756), px(725))
    d.rounded_rectangle(face_box, radius=px(120), fill=(16, 18, 30))
    d.rounded_rectangle(face_box, radius=px(120), outline=(90, 92, 104), width=px(8))

    # The visor, raised for the call: a striped brow across the top of the face.
    visor = (px(250), px(330), px(774), px(425))
    d.rounded_rectangle(visor, radius=px(46), fill=(48, 52, 66))
    stripes = Image.new('RGBA', (N, N), (0, 0, 0, 0))
    sd = ImageDraw.Draw(stripes)
    for i in range(-2, 14):
        x = px(250 + i * 44)
        sd.polygon([(x, visor[3]), (x + px(22), visor[3]), (x + px(22) + px(95), visor[1]), (x + px(95), visor[1])],
                   fill=(232, 112, 36, 255))
    icon.paste(stripes, (0, 0), Image.composite(stripes, Image.new('RGBA', (N, N)),
                                              mask_rounded((N, N), visor, px(46))).split()[3])
    d.rounded_rectangle(visor, radius=px(46), outline=(28, 30, 40), width=px(6))

    # Two eyes in the telepresence magenta, glowing.
    magenta = (255, 0, 255)
    for cx in (px(405), px(619)):
        cy = px(512)
        glow(icon, (cx, cy), px(62), magenta, 0.9)
        d = ImageDraw.Draw(icon)
        d.ellipse((cx - px(56), cy - px(56), cx + px(56), cy + px(56)), fill=(250, 60, 250))
        d.ellipse((cx - px(30), cy - px(30), cx + px(30), cy + px(30)), fill=(255, 205, 255))
        d.ellipse((cx - px(56), cy - px(56), cx + px(56), cy + px(56)), outline=(120, 0, 120), width=px(5))

    # The mouth: a row of light bars, as his LED mouth moves when someone speaks through him.
    heights = (30, 52, 78, 58, 90, 64, 42, 70, 48, 28)
    left, width, gap, base = px(328), px(26), px(12), px(668)
    for i, h in enumerate(heights):
        x = left + i * (width + gap)
        d.rounded_rectangle((x, base - px(h / 2), x + width, base + px(h / 2)), radius=px(9), fill=(0, 180, 255))
    glow_layer = Image.new('RGBA', (N, N), (0, 0, 0, 0))
    ImageDraw.Draw(glow_layer).rounded_rectangle((left - px(10), base - px(50), left + 10 * (width + gap), base + px(50)),
                                                 radius=px(30), fill=(0, 160, 255, 70))
    icon.alpha_composite(glow_layer.filter(ImageFilter.GaussianBlur(px(22))))

    # A call: signal arcs at the top right.
    d = ImageDraw.Draw(icon)
    cx, cy = px(768), px(236)   # Kept inside the tile's rounded corner.
    d.ellipse((cx - px(17), cy - px(17), cx + px(17), cy + px(17)), fill=(255, 255, 255))
    for r in (48, 84):
        d.arc((cx - px(r), cy - px(r), cx + px(r), cy + px(r)), start=205, end=335, fill=(255, 255, 255), width=px(14))
    return icon.resize((1024, 1024), Image.LANCZOS)


def main():
    picture = draw()
    png = HERE / 'AppIcon.png'
    picture.save(png)
    with tempfile.TemporaryDirectory() as folder:
        iconset = Path(folder) / 'AppIcon.iconset'
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                name = f'icon_{size}x{size}' + ('@2x' if scale == 2 else '') + '.png'
                picture.resize((size * scale, size * scale), Image.LANCZOS).save(iconset / name)
        out = HERE.parent / 'Resources' / 'AppIcon.icns'
        subprocess.run(['iconutil', '-c', 'icns', str(iconset), '-o', str(out)], check=True)
    print(f'wrote {png} and {out}')


if __name__ == '__main__':
    main()
