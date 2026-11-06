"""Generate GhostLock icon artwork (requires Pillow).

    py tools/make_icons.py

Design: "ghost fading into data" - a solid violet-indigo left half that dissolves into
blue-to-cyan pixel squares on the right. Every file is backgroundless (transparent PNG/ICO):
just the ghost, no tile, no circle.

Outputs into ../assets:
    ghostlock.ico              multi-size Windows icon
    ghostlock.png              256 px ghost
    ghostlock_<n>.png          ghost at the sizes the app uses at runtime (no runtime resampling)
    ghostlock_mark_<n>.png     same artwork (kept for the app's icon loader)
    ghost_only_<n>.png         1024/512/256 px exports for the website, README, etc.
    ghost_light_<n>.png        deeper-colour variant for light backgrounds
"""
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

ASSETS = Path(__file__).resolve().parent.parent / "assets"

# Ghost geometry in abstract units (matches the proportions of the design PDF).
W, H = 300, 390
R = W / 2
SPLIT = 128  # solid half | pixel half
BOTTOM_R = 34
EYE_L = (96, 133)
EYE_R = (202, 133)
EYE_RX, EYE_RY = 24, 33
EYE_COLOR = (22, 27, 58, 255)
HI = 1500  # height in px of the master render

TILE_SIZES = (24, 32, 40, 48, 56, 64, 72, 80, 96, 112, 128, 160, 192, 256)
MARK_SIZES = (48, 64, 80, 96, 128, 160)
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
EXPORT_SIZES = (1024, 512, 256)

PALETTES = {
    # default: reads well on dark and mid-tone backgrounds (hero palette from the design)
    "default": dict(left=(150, 132, 255), right=(100, 122, 255), blue=(61, 134, 247), cyan=(22, 181, 204)),
    # deeper tones for white / light backgrounds (light palette from the design)
    "light": dict(left=(108, 76, 255), right=(88, 99, 255), blue=(45, 120, 240), cyan=(14, 160, 185)),
}

FINE = dict(cell=21, pitch=25, inside=(1, 1, 0.95, 0.8, 0.6, 0.45, 0.3, 0.2, 0.12), tail=0.07, outside=0.30)
COARSE = dict(cell=38, pitch=46, inside=(1, 1, 0.8, 0.45, 0.2), tail=0.0, outside=0.0)


def in_shape(x, y):
    if y < 0 or y > H:
        return False
    if y < R:
        return (x - R) ** 2 + (y - R) ** 2 <= R * R
    if y <= H - BOTTOM_R:
        return 0 <= x <= W
    dy = y - (H - BOTTOM_R)
    if x < BOTTOM_R:
        return (x - BOTTOM_R) ** 2 + dy**2 <= BOTTOM_R**2
    if x > W - BOTTOM_R:
        return (x - (W - BOTTOM_R)) ** 2 + dy**2 <= BOTTOM_R**2
    return 0 <= x <= W


def dist_outside(x, y):
    if y < R:
        return ((x - R) ** 2 + (y - R) ** 2) ** 0.5 - R
    return x - W


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def render(style, palette, seed=11):
    """Master render of the ghost (RGBA, transparent background), tightly cropped."""
    u = HI / H
    pad_x = 110  # room for the dissolving pixels on the right
    cw, ch = int((W + pad_x) * u), int(H * u)
    pal = PALETTES[palette]

    def S(v):
        return v * u

    # --- silhouette mask
    sil = Image.new("L", (cw, ch), 0)
    d = ImageDraw.Draw(sil)
    d.ellipse([0, 0, S(W), S(W)], fill=255)
    d.rectangle([0, S(R), S(W), S(H - BOTTOM_R - 4)], fill=255)
    d.rounded_rectangle([0, S(H - 2 * BOTTOM_R - 6), S(W), S(H)], radius=S(BOTTOM_R), fill=255)

    # --- solid half: horizontal gradient, vertical cut at SPLIT, small notch at the hem
    solid_mask = Image.new("L", (cw, ch), 0)
    ImageDraw.Draw(solid_mask).rectangle([0, 0, S(SPLIT), ch], fill=255)
    solid_mask = ImageChops.multiply(solid_mask, sil)
    ImageDraw.Draw(solid_mask).ellipse([S(90 - 22), S(H - 27), S(90 + 22), S(H + 27)], fill=0)
    grad = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for x in range(int(S(SPLIT)) + 2):
        gd.line([(x, 0), (x, ch)], fill=lerp(pal["left"], pal["right"], x / max(1, S(SPLIT))) + (255,))
    out = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    out.paste(grad, mask=solid_mask)

    # --- pixel half
    rng = random.Random(seed)
    cell, pitch = style["cell"], style["pitch"]
    clipped = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    free = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    cd, fd = ImageDraw.Draw(clipped), ImageDraw.Draw(free)
    x0 = SPLIT + 7
    cols = int((W + 85 - x0) / pitch) + 1
    rows = int(H / pitch) + 2
    y_off = (H - (rows - 1) * pitch) / 2 - 2
    for k in range(cols):
        for j in range(rows):
            cx = x0 + cell / 2 + k * pitch
            cy = y_off + j * pitch
            if (cx - EYE_R[0]) ** 2 / (EYE_RX + 8) ** 2 + (cy - EYE_R[1]) ** 2 / (EYE_RY + 8) ** 2 <= 1:
                continue  # clear the pixels around the right eye
            t = min(1.0, (cx - SPLIT) / (W + 60 - SPLIT))
            r, g, b = lerp(pal["blue"], pal["cyan"], t)
            jit = rng.randint(-7, 7)
            color = (max(0, min(255, r + jit)), max(0, min(255, g + jit)), max(0, min(255, b + jit)), 255)
            roll = rng.random()
            if in_shape(cx, cy):
                p = style["inside"][k] if k < len(style["inside"]) else style["tail"]
                if roll < p:
                    cd.rectangle([S(cx - cell / 2), S(cy - cell / 2), S(cx + cell / 2), S(cy + cell / 2)], fill=color)
            elif style["outside"] > 0 and cx > R:
                dist = dist_outside(cx, cy)
                if 0 < dist < 80 and cy > 12 and cy < H - 12:
                    if roll < max(0.0, style["outside"] - 0.0042 * dist):
                        half = cell / 2 * max(0.35, 1 - dist / 100)
                        fd.rectangle([S(cx - half), S(cy - half), S(cx + half), S(cy + half)], fill=color)
    clipped.putalpha(ImageChops.multiply(clipped.getchannel("A"), sil))
    out.alpha_composite(clipped)
    out.alpha_composite(free)

    # --- eyes
    ed = ImageDraw.Draw(out)
    for ex, ey in (EYE_L, EYE_R):
        ed.ellipse([S(ex - EYE_RX), S(ey - EYE_RY), S(ex + EYE_RX), S(ey + EYE_RY)], fill=EYE_COLOR)
    return out.crop(out.getbbox())


def square(img, size, margin=0.04):
    """Centre the ghost in a transparent square canvas and resample to `size`."""
    side = int(max(img.size) * (1 + 2 * margin))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.alpha_composite(img, ((side - img.width) // 2, (side - img.height) // 2))
    return canvas.resize((size, size), Image.LANCZOS)


def main():
    ASSETS.mkdir(exist_ok=True)
    for old in ASSETS.glob("ghostlock*.png"):
        old.unlink()
    for old in ASSETS.glob("ghost_*.png"):
        old.unlink()

    fine = render(FINE, "default")
    coarse = render(COARSE, "default")
    light = render(FINE, "light")

    def pick(n):
        return coarse if n < 64 else fine

    for n in TILE_SIZES:
        square(pick(n), n).save(ASSETS / f"ghostlock_{n}.png")
    for n in MARK_SIZES:
        square(pick(n), n).save(ASSETS / f"ghostlock_mark_{n}.png")
    square(fine, 256).save(ASSETS / "ghostlock.png")
    for n in EXPORT_SIZES:
        square(fine, n).save(ASSETS / f"ghost_only_{n}.png")
        square(light, n).save(ASSETS / f"ghost_light_{n}.png")

    frames = [square(pick(n), n) for n in ICO_SIZES]
    frames[-1].save(
        ASSETS / "ghostlock.ico", format="ICO", sizes=[(n, n) for n in ICO_SIZES], append_images=frames[:-1]
    )
    print("Icons written to", ASSETS)


if __name__ == "__main__":
    main()
