"""Генератор карточек-изображений для постов канала (Pillow).

Фирменный стиль JoyDao: тёмный космический градиент, золото и фиолет,
крупное число — читается в ленте даже в миниатюре.
"""
from __future__ import annotations

import random
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from . import suisai

W, H = 1280, 720
TOP = (36, 29, 69)        # #241d45
BOTTOM = (14, 11, 30)     # #0e0b1e
GOLD = (212, 175, 85)
VIOLET = (164, 140, 245)
TEXTC = (242, 239, 252)
MUTED = (176, 166, 214)

_FONT_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]
_FONT_REG = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    for path in (_FONT_BOLD if bold else _FONT_REG):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _base(seed: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    """Градиентный фон со звёздами и золотым кольцом справа."""
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):  # вертикальный градиент
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)))
    rnd = random.Random(seed)  # звёзды стабильны для одной темы
    for _ in range(40):
        x, y = rnd.randint(0, W), rnd.randint(0, H)
        r = rnd.choice((1, 1, 2))
        d.ellipse((x - r, y - r, x + r, y + r), fill=(207, 196, 244))
    # декоративное кольцо
    cx, cy, r = W - 250, H // 2, 240
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=GOLD, width=5)
    d.ellipse((cx - r + 26, cy - r + 26, cx + r - 26, cy + r - 26), outline=(88, 70, 160), width=2)
    return img, d


def _brand(d: ImageDraw.ImageDraw) -> None:
    d.text((70, H - 78), "JoyDao  ·  t.me/joydao", font=_font(30, False), fill=MUTED)


def _date_grid(d: ImageDraw.ImageDraw, date_str: str) -> None:
    """Квадрат 3×3 в кольце: цифры даты по энергиям (как в матрице), пустые — «·»."""
    from datetime import datetime
    try:
        m = suisai.matrix(datetime.strptime(date_str, "%d.%m.%Y").date())
    except ValueError:
        return
    cs, cx, cy = 108, W - 250, H // 2
    f = _font(40)
    for i, e in enumerate(suisai.MATRIX_LAYOUT):
        x = cx - 1.5 * cs + (i % 3) * cs
        y = cy - 1.5 * cs + (i // 3) * cs
        d.rounded_rectangle((x + 6, y + 6, x + cs - 6, y + cs - 6), 16,
                            outline=(88, 70, 160), width=2, fill=(52, 42, 98) if m[e] else None)
        s = str(e) * min(m[e], 3) if m[e] else "·"
        tw = d.textlength(s, font=f)
        d.text((x + cs / 2 - tw / 2, y + cs / 2 - 26), s, font=f, fill=GOLD if m[e] >= 2 else (TEXTC if m[e] else MUTED))


def render(topic: dict) -> bytes:
    """PNG-карточка под тему поста."""
    kind = topic.get("kind", "day")
    img, d = _base(str(sorted(topic.items())))

    if kind == "day":
        n = topic["num"]
        label, big = "ЭНЕРГИЯ ДНЯ", str(n)
        sub = topic.get("date", "")
        desc = suisai.CHS_SHORT[n].split(":")[0]
    elif kind == "chs":
        n = topic["num"]
        label, big = "ЧИСЛО СОЗНАНИЯ", str(n)
        sub = ""
        desc = suisai.CHS_SHORT[n].split(":")[0]
    else:
        a, b = topic["a"], topic["b"]
        label, big = "СОВМЕСТИМОСТЬ", f"{a} + {b}"
        sub = ""
        desc = "две энергии в одной паре"

    d.text((70, 96), label, font=_font(44), fill=VIOLET)
    d.text((64, 170), big, font=_font(300), fill=GOLD)
    y = 520
    if sub:
        d.text((70, y), sub, font=_font(38, False), fill=MUTED)
        y += 56
    d.text((70, y), desc, font=_font(40), fill=TEXTC)
    if kind == "day" and sub:
        _date_grid(d, sub)  # баланс энергий даты в кольце
    else:  # число крупно и в кольце справа — для миниатюры
        ring_font = _font(150)
        tw = d.textlength(big, font=ring_font)
        d.text((W - 250 - tw / 2, H / 2 - 105), big, font=ring_font, fill=VIOLET)
    _brand(d)

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_portrait(name: str, chs: int, mission: int, year: int) -> bytes:
    """Квадратная шеринг-карточка личного портрета — для пересылки друзьям."""
    S = 1080
    img = Image.new("RGB", (S, S))
    d = ImageDraw.Draw(img)
    for y in range(S):
        t = y / S
        d.line([(0, y), (S, y)], fill=tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)))
    rnd = random.Random(f"{name}{chs}{mission}")
    for _ in range(46):
        x, y = rnd.randint(0, S), rnd.randint(0, S)
        r = rnd.choice((1, 2, 2))
        d.ellipse((x - r, y - r, x + r, y + r), fill=(207, 196, 244))

    # заголовок и имя
    label_f = _font(46)
    tw = d.textlength("КОД ДУШИ", font=label_f)
    d.text(((S - tw) / 2, 78), "КОД ДУШИ", font=label_f, fill=VIOLET)
    name = (name or "")[:20]
    name_f = _font(66)
    tw = d.textlength(name, font=name_f)
    d.text(((S - tw) / 2, 148), name, font=name_f, fill=TEXTC)

    # большое число сознания в золотом кольце
    cx, cy, r = S // 2, 470, 210
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=GOLD, width=7)
    d.ellipse((cx - r + 22, cy - r + 22, cx + r - 22, cy + r - 22), outline=(88, 70, 160), width=2)
    big_f = _font(240)
    tw = d.textlength(str(chs), font=big_f)
    d.text((cx - tw / 2, cy - 158), str(chs), font=big_f, fill=GOLD)

    # описание числа
    desc = suisai.CHS_SHORT[chs].split(":")[0]
    desc_f = _font(52)
    tw = d.textlength(desc, font=desc_f)
    d.text(((S - tw) / 2, 716), desc, font=desc_f, fill=TEXTC)

    # миссия и личный год
    duo = f"Миссия {mission}   ·   Личный год {year}"
    duo_f = _font(40, False)
    tw = d.textlength(duo, font=duo_f)
    d.text(((S - tw) / 2, 800), duo, font=duo_f, fill=MUTED)

    # разделитель и CTA
    d.line([(S / 2 - 140, 886), (S / 2 + 140, 886)], fill=(88, 70, 160), width=2)
    cta = "Узнай свой код души бесплатно"
    cta_f = _font(38, False)
    tw = d.textlength(cta, font=cta_f)
    d.text(((S - tw) / 2, 916), cta, font=cta_f, fill=MUTED)
    from . import config
    handle = f"@{config.BOT_USERNAME}"
    h_f = _font(46)
    tw = d.textlength(handle, font=h_f)
    d.text(((S - tw) / 2, 976), handle, font=h_f, fill=GOLD)

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
