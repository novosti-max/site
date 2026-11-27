"""Крео-бот: картинка «экран блокировки с уведомлениями канала» 1080×1350 (Pillow).

lockscreen(notes, channel_name, avatar, clock, date_text) -> bytes (JPEG)
notes — до 5 штук: {"ago": "сейчас", "title": "...", "sub": "...", "red": True/False}, сверху — самое свежее.
"""
import io
import os

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1350
FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", os.path.dirname(os.path.abspath(__file__))]


def _font(name, size):
    for d in FONT_DIRS:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    for alt in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        for d in FONT_DIRS:
            p = os.path.join(d, alt)
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _fit(draw, text, name, size, max_w, min_size=22):
    """Шрифт уменьшаем до min_size, потом обрезаем текст с «…»."""
    while size >= min_size:
        f = _font(name, size)
        if draw.textlength(text, font=f) <= max_w:
            return text, f
        size -= 1
    f = _font(name, min_size)
    while text and draw.textlength(text + "…", font=f) > max_w:
        text = text[:-1]
    return text.rstrip() + "…", f


def _gradient():
    top, mid, bot = (10, 22, 48), (27, 31, 58), (58, 20, 32)
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        k = y / (H - 1)
        if k < 0.55:
            a, b, t = top, mid, k / 0.55
        else:
            a, b, t = mid, bot, (k - 0.55) / 0.45
        d.line([(0, y), (W, y)], fill=tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)))
    return img


_BG = None


def _background():
    global _BG
    if _BG is None:
        img = _gradient().convert("RGBA")
        sil = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(sil)
        pts = [(0, H), (0, 1150), (60, 1110), (120, 1130), (160, 1050), (200, 1070), (240, 1010), (300, 1030),
               (340, 970), (360, 770), (380, 970), (440, 1000), (500, 950), (540, 690), (560, 950), (620, 980),
               (700, 930), (760, 1000), (820, 960), (880, 1020), (960, 990), (1020, 1050), (1080, 1030), (W, H)]
        d.polygon(pts, fill=(0, 0, 0, 128))
        d.pieslice([450, 780, 630, 960], 180, 360, fill=(0, 0, 0, 128))
        _BG = Image.alpha_composite(img, sil)
    return _BG.copy()


def _avatar(av, size):
    if av is None:
        im = Image.new("RGB", (size, size), (200, 30, 45))
    else:
        im = av.convert("RGB")
        s = min(im.size)
        im = im.crop(((im.width - s) // 2, (im.height - s) // 2, (im.width + s) // 2, (im.height + s) // 2))
        im = im.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
    return im, mask


def lockscreen(notes, channel_name, avatar=None, clock="07:15", date_text="пятница, 27 ноября"):
    img = _background()
    d = ImageDraw.Draw(img)
    d.text((W // 2, 150), date_text, font=_font("DejaVuSans-Bold.ttf", 38), fill=(255, 255, 255, 220), anchor="ms")
    d.text((W // 2, 350), clock, font=_font("DejaVuSans-ExtraLight.ttf", 200), fill=(255, 255, 255, 255), anchor="ms")
    av, mask = _avatar(avatar, 76)
    y = 420
    name_up = channel_name.upper()
    for i, n in enumerate(notes[:5]):
        h = 142
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle([60, y, W - 60, y + h], radius=32,
                                                fill=(245, 245, 250, int(255 * (0.94 - i * 0.06))))
        img = Image.alpha_composite(img, layer)
        d = ImageDraw.Draw(img)
        img.paste(av, (88, y + 28), mask)
        ago_f = _font("DejaVuSans-Bold.ttf", 27)
        ago_w = d.textlength(n.get("ago", ""), font=ago_f)
        nm, nf = _fit(d, name_up, "DejaVuSans-Bold.ttf", 26, W - 90 - 184 - ago_w - 24, 18)
        d.text((184, y + 48), nm, font=nf, fill=(85, 85, 85), anchor="ls")
        d.text((W - 90, y + 48), n.get("ago", ""), font=ago_f, fill=(85, 85, 85), anchor="rs")
        t, tf = _fit(d, n.get("title", ""), "DejaVuSans-Bold.ttf", 33, W - 90 - 184, 24)
        d.text((184, y + 92), t, font=tf, fill=(200, 16, 46) if n.get("red") else (17, 17, 17), anchor="ls")
        s, sf = _fit(d, n.get("sub", ""), "DejaVuSans.ttf", 29, W - 90 - 184, 22)
        d.text((184, y + 128), s, font=sf, fill=(34, 34, 34), anchor="ls")
        y += h + 14
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=90)
    return out.getvalue()
