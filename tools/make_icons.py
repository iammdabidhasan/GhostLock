"""Generate GhostLock icon artwork (requires Pillow).

    py tools/make_icons.py

Design: a smooth, backgroundless ghost. A round dome head, straight sides, a three-scallop hem, two oval eyes
with a small shine, and a teal-to-blue gradient that stays clear on white and on dark backgrounds.
No tile, no circle: just the ghost. It is drawn from a few shapes with no randomness, so the output is
identical every run.

Outputs into ../assets:
    ghostlock.ico              multi-size Windows icon
    ghostlock.png              256 px ghost
    ghostlock_<n>.png          ghost at the sizes the app uses at runtime (no runtime resampling)
    ghostlock_mark_<n>.png     same artwork (kept for the app's icon loader)
    ghost_only_<n>.png         1024/512/256 px exports for the website, README, etc.
    ghost_light_<n>.png        slightly deeper colours, for very bright or printed backgrounds
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ASSETS = Path(__file__).resolve().parent.parent / "assets"

W, H = 300, 380  # abstract units
HI = 1400  # height in px of the master render
U = HI / H
CW, CH = int(W * U), int(H * U)

SCALLOPS = 3
EYES_X = (100, 200)
EYE_Y = 150
EYE_RX, EYE_RY = 20, 30
SMALL_EYE_SCALE = 1.2  # small icons get slightly larger eyes so they stay readable
INK = (12, 28, 66, 255)
SHINE = (255, 255, 255, 235)

PALETTES = {
    "default": ((20, 184, 166), (37, 99, 235)),  # teal -> blue
    "light": ((13, 148, 136), (29, 78, 216)),  # a little deeper
}

TILE_SIZES = (24, 32, 40, 48, 56, 64, 72, 80, 96, 112, 128, 160, 192, 256)
MARK_SIZES = (48, 64, 80, 96, 128, 160)
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
EXPORT_SIZES = (1024, 512, 256)


def S(value):
    return value * U


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def silhouette():
    """Dome head, straight sides, three scalloped bumps along the hem."""
    mask = Image.new("L", (CW, CH), 0)
    draw = ImageDraw.Draw(mask)
    radius = W / 2
    draw.ellipse([0, 0, S(W), S(W)], fill=255)  # head
    bump = W / (2 * SCALLOPS)
    draw.rectangle([0, S(radius), S(W), S(H - bump)], fill=255)  # body
    for i in range(SCALLOPS):  # hem
        cx = bump + i * 2 * bump
        draw.ellipse([S(cx - bump), S(H - 2 * bump), S(cx + bump), S(H)], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(S(5)))  # softens the notches between the bumps
    return mask.point(lambda v: 255 if v > 128 else 0).filter(ImageFilter.GaussianBlur(1.2))


def gradient(c1, c2):
    g = Image.new("RGBA", (CW, CH))
    px = g.load()
    for y in range(CH):
        for x in range(CW):
            t = min(1.0, max(0.0, x / CW * 0.45 + y / CH * 0.55))
            px[x, y] = lerp(c1, c2, t) + (255,)
    return g


def render(palette="default", eye_scale=1.0):
    """Master render of the ghost (RGBA, transparent background), tightly cropped."""
    out = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    out.paste(gradient(*PALETTES[palette]), mask=silhouette())
    draw = ImageDraw.Draw(out)
    rx, ry = EYE_RX * eye_scale, EYE_RY * eye_scale
    for ex in EYES_X:
        draw.ellipse([S(ex - rx), S(EYE_Y - ry), S(ex + rx), S(EYE_Y + ry)], fill=INK)
        draw.ellipse([S(ex - 9 * eye_scale), S(EYE_Y - 20 * eye_scale), S(ex + 1 * eye_scale), S(EYE_Y - 8 * eye_scale)], fill=SHINE)
    return out.crop(out.getbbox())


def square(img, size, margin=0.04):
    """Centre the ghost in a transparent square canvas and resample to `size`."""
    side = int(max(img.size) * (1 + 2 * margin))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.alpha_composite(img, ((side - img.width) // 2, (side - img.height) // 2))
    return canvas.resize((size, size), Image.LANCZOS)


def main():
    ASSETS.mkdir(exist_ok=True)
    for old in list(ASSETS.glob("ghostlock*.png")) + list(ASSETS.glob("ghost_*.png")):
        old.unlink()

    normal = render("default", 1.0)
    small = render("default", SMALL_EYE_SCALE)
    light = render("light", 1.0)

    def pick(n):
        return small if n < 64 else normal

    for n in TILE_SIZES:
        square(pick(n), n).save(ASSETS / f"ghostlock_{n}.png")
    for n in MARK_SIZES:
        square(pick(n), n).save(ASSETS / f"ghostlock_mark_{n}.png")
    square(normal, 256).save(ASSETS / "ghostlock.png")
    for n in EXPORT_SIZES:
        square(normal, n).save(ASSETS / f"ghost_only_{n}.png")
        square(light, n).save(ASSETS / f"ghost_light_{n}.png")

    frames = [square(pick(n), n) for n in ICO_SIZES]
    frames[-1].save(
        ASSETS / "ghostlock.ico", format="ICO", sizes=[(n, n) for n in ICO_SIZES], append_images=frames[:-1]
    )
    print("Icons written to", ASSETS)


if __name__ == "__main__":
    main()
