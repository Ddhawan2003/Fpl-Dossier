"""Instagram post card renderer.

Draws the team's Canva template in code, so a weekly post is a click rather than
twenty minutes of manual layout: pick a player, pick the verdict and confidence,
download a finished 1080x1350 PNG.

Deliberately a *pure* module -- no Streamlit, no network, no file discovery
beyond the render folder. It takes a fully-resolved CardSpec of already-computed
values and returns PNG bytes. That keeps it testable from a plain script, and
keeps the project's second rule intact: every number on the card was computed
exactly upstream, here it is only typeset.

Fonts are vendored under assets/fonts/ because Streamlit Community Cloud ships
almost no fonts, and the two that match this template on a Windows box (Impact,
Arial Black) are Microsoft-licensed and cannot be committed. Anton and Barlow
are OFL.
"""

from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont

# Instagram portrait. 4:5 is the tallest aspect the feed will show uncropped.
W, H = 1080, 1350

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
FONT_DIR = os.path.join(ASSETS, "fonts")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENDER_DIR = os.path.join(REPO_ROOT, "data", "renders")

BG = (11, 18, 32)
WHITE = (255, 255, 255)
MUTED = (150, 161, 178)
RULE = (255, 255, 255, 60)

# The verdict drives the accent colour, not the club. In the source template a
# Man Utd player sits on a green glow -- the colour reads BUY, not United.
VERDICTS = {
    "BUY":  (0, 231, 134),
    "SELL": (255, 71, 87),
    "HOLD": (255, 176, 32),
}

LEFT = 88          # text margin
NAME_MAX_W = 700   # the name is allowed to run over the render, as in the template

# The left column flows: each element is placed a fixed gap below the measured
# ink of the one above it, rather than at a hardcoded y. A two-line name is
# ~115px taller than a one-line name, and with fixed anchors that difference
# lands on top of the fixture line.
Y_GW = 62
Y_VERDICT = 236
GAP_VERDICT_NAME = 52
GAP_NAME_VS = 34
GAP_VS_OPPONENT = 16
GAP_OPPONENT_RULE = 40
GAP_RULE_STRIP = 26
GAP_STRIP_STATS = 26
GAP_NUM_LABEL = 16

# The confidence block is the one thing anchored to the bottom edge instead,
# so it sits in the same place on every card regardless of what flowed above.
Y_STARS_CENTRE = H - 100
GAP_CONF_STARS = 52


@dataclass
class CardSpec:
    """Everything the card needs, already resolved by the caller."""
    name: str
    verdict: str = "BUY"
    gw_label: str = "GW 1"
    opponent: str = ""                      # e.g. "Brighton (H)"
    confidence: int = 5                     # 0-5 stars
    stat_label: str = "LAST 6 GWS"          # the strip's vintage caption
    stats: list = field(default_factory=list)   # [(value, label), ...] up to 3
    render_path: str | None = None
    logo_path: str | None = None


# --------------------------------------------------------------------------
# Renders
# --------------------------------------------------------------------------
def safe_name(name: str) -> str:
    """Mirror fpl_renders_all.py's filename rule exactly.

    That script names files from FPL's `first_name second_name` with every
    non-alphanumeric run collapsed to `_`, and it does NOT transliterate -- so
    Ødegaard is on disk as `Martin_degaard` and Groß as `Pascal_Gro`. Any
    lookup here has to reproduce that byte for byte or the join silently misses.
    """
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")


def find_render(first_name: str, second_name: str) -> str | None:
    path = os.path.join(RENDER_DIR, f"{safe_name(f'{first_name} {second_name}'.strip())}.png")
    return path if os.path.exists(path) else None


def headline_name(first_name: str, second_name: str, web_name: str) -> str:
    """A name a reader would say out loud, for the poster headline.

    FPL has no display-name field: `web_name` is built for a table cell
    ("B.Fernandes") and the legal name is built for a registration form
    ("Bruno Borges Fernandes"). Neither is what goes on a graphic, so this
    picks between them -- an abbreviated web_name means the surname is the
    memorable part, and an over-long legal name loses to the short form.

    Only ever a default; the UI leaves it editable, because no rule gets every
    name right.
    """
    first, second, web = first_name.strip(), second_name.strip(), web_name.strip()
    if "." in web:
        return f"{first} {web.split('.')[-1].strip()}".strip()
    full = f"{first} {second}".strip()
    return full if len(full) <= 20 else (web or full)


def headshot_url(code: int) -> str:
    """FPL's own headshot, the fallback when there is no cut-out render.

    Keyed on `code` (a cross-season player identifier), never on `id`. Square
    and backgrounded rather than a transparent cut-out, so it will not bleed off
    the edge the way a render does -- draw_card handles that by insetting it.
    """
    return f"https://resources.premierleague.com/premierleague/photos/players/250x250/p{code}.png"


# --------------------------------------------------------------------------
# Type
# --------------------------------------------------------------------------
def _font(filename: str, size: int):
    path = os.path.join(FONT_DIR, filename)
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        # Never let a missing font take the page down -- a card in the wrong
        # typeface is recoverable, a traceback mid-deadline is not.
        return ImageFont.load_default()


def anton(size):      return _font("Anton-Regular.ttf", size)
def bold(size):       return _font("Barlow-Bold.ttf", size)
def semibold(size):   return _font("Barlow-SemiBold.ttf", size)
def medium(size):     return _font("Barlow-Medium.ttf", size)


# Every measurement and every draw uses anchor="lt" -- x,y is the top-left of
# the *ink*, not of an em box whose padding differs per font. Anton in
# particular carries a lot of leading, and mixing the two conventions is what
# put the stat labels through the middle of the numbers.
ANCHOR = "lt"


def _text_w(draw, text, font, tracking=0):
    if not text:
        return 0
    # getlength is the advance width, which _draw_tracked also steps by. An ink
    # bbox would be wrong here: a space has no ink, so a measured string would
    # come out narrower than the one actually drawn.
    return font.getlength(text) + tracking * (len(text) - 1)


def _text_h(draw, text, font):
    if not text:
        return 0
    return draw.textbbox((0, 0), text, font=font, anchor=ANCHOR)[3]


def _draw_tracked(draw, xy, text, font, fill, tracking=0):
    """Letterspaced text. PIL has no tracking, and the template's small caps
    labels are unmistakably tracked out, so they are drawn glyph by glyph.

    Every glyph is placed on one shared *baseline*, not at a shared ink top.
    Anchoring each glyph's own ink top to the same y is what turned "2025-26"
    into "2025‾26": a hyphen's ink starts mid-x-height, so top-aligning it
    lifted it to cap height. Anything with punctuation, digits or lowercase
    mixed in hits the same bug.
    """
    x, y = xy
    if tracking == 0:
        draw.text((x, y), text, font=font, fill=fill, anchor=ANCHOR)
        return

    # Caller's y means "ink top of the whole string", as everywhere else here.
    # Convert that once to a baseline the individual glyphs can share.
    ascent = font.getmetrics()[0]
    ink_top = draw.textbbox((0, 0), text, font=font, anchor="la")[1]
    baseline = y - ink_top + ascent

    for ch in text:
        draw.text((x, baseline), ch, font=font, fill=fill, anchor="ls")
        x += font.getlength(ch) + tracking


def _fit(draw, text, size_fn, max_w, start, floor=40):
    """Largest size at which `text` fits `max_w`. Long names shrink instead of
    overrunning the card."""
    size = start
    while size > floor:
        f = size_fn(size)
        if _text_w(draw, text, f) <= max_w:
            return f
        size -= 2
    return size_fn(floor)


def _split_name(name: str) -> list[str]:
    """One or two lines, breaking on the last space so the surname lands alone
    -- 'BRUNO / FERNANDES', not 'BRUNO FERN / ANDES'."""
    parts = name.upper().split()
    if len(parts) <= 1:
        return parts
    return [" ".join(parts[:-1]), parts[-1]]


# --------------------------------------------------------------------------
# Decoration
# --------------------------------------------------------------------------
def _glow(color, cx, cy, radius, max_alpha=110):
    """Soft radial wash behind the player.

    Built small and scaled up rather than computed per full-size pixel: a 160px
    square is 25k operations instead of 1.4M, and bilinear upscaling of a smooth
    gradient is indistinguishable from the real thing.
    """
    n = 160
    grad = Image.new("L", (n, n), 0)
    px = grad.load()
    for y in range(n):
        dy = (y + 0.5) / n - 0.5
        for x in range(n):
            dx = (x + 0.5) / n - 0.5
            d = ((dx * dx + dy * dy) ** 0.5) / 0.5
            px[x, y] = int(max_alpha * max(0.0, 1.0 - d) ** 2)

    d = radius * 2
    mask = grad.resize((d, d), Image.BILINEAR)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    tile = Image.new("RGBA", (d, d), color + (255,))
    tile.putalpha(mask)
    layer.alpha_composite(tile, (int(cx - radius), int(cy - radius)))
    return layer


def _scrim():
    """Left-to-right dark gradient so the headline stays legible where it runs
    over the player. The template gets this free from the photo being dark on
    that side; a random render will not be."""
    n = 128
    grad = Image.new("L", (n, 1), 0)
    px = grad.load()
    for x in range(n):
        t = x / (n - 1)
        px[x, 0] = int(215 * max(0.0, 1.0 - t) ** 1.35)
    mask = grad.resize((W, H), Image.BILINEAR)
    layer = Image.new("RGBA", (W, H), BG + (255,))
    layer.putalpha(mask)
    return layer


def _star(draw, cx, cy, r, fill):
    import math
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rad = r if i % 2 == 0 else r * 0.44
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    draw.polygon(pts, fill=fill)


def _place_player(canvas, img):
    """Big, bottom-anchored, bleeding off the right edge as in the template.

    A cut-out render is trimmed to its own alpha bounding box first, because
    FootyRenders PNGs carry wildly different amounts of transparent padding --
    without this, two players at the same nominal scale sit at visibly
    different sizes.

    Scale is then bounded on *both* axes. Height alone is not enough: these
    renders range from 647x2375 to 2582x3846 after trimming, so a
    height-only fit blows the wide ones across the entire card and leaves the
    narrow ones as slivers.
    """
    img = img.convert("RGBA")
    box = img.getbbox()
    if box:
        img = img.crop(box)

    scale = min(H * 1.04 / img.height, W * 0.74 / img.width)
    img = img.resize((max(1, int(img.width * scale)),
                      max(1, int(img.height * scale))), Image.LANCZOS)

    # Centre of mass in the right third, feet on the bottom edge. The overflow
    # past the right edge is what makes it read as a poster rather than a slide.
    #
    # The max() is the guard for wide renders: centring alone pulls a broad
    # figure back across the headline, so the figure's left edge is never
    # allowed past the text column no matter how wide it is. Narrow renders are
    # unaffected and stay centred.
    x = max(int(W * 0.44), int(W * 0.72) - img.width // 2)
    y = H - img.height
    canvas.alpha_composite(img, (x, y))


# --------------------------------------------------------------------------
# The card
# --------------------------------------------------------------------------
def draw_card(spec: CardSpec) -> bytes:
    accent = VERDICTS.get(spec.verdict.upper(), VERDICTS["BUY"])

    canvas = Image.new("RGBA", (W, H), BG + (255,))
    canvas.alpha_composite(_glow(accent, W * 0.74, H * 0.40, int(W * 0.62)))

    if spec.render_path and os.path.exists(spec.render_path):
        try:
            _place_player(canvas, Image.open(spec.render_path))
        except Exception:  # noqa: BLE001 -- a corrupt PNG must not lose the card
            pass

    canvas.alpha_composite(_scrim())
    draw = ImageDraw.Draw(canvas)

    # ---- gameweek ----
    _draw_tracked(draw, (LEFT, Y_GW), spec.gw_label.upper(), bold(36), WHITE, tracking=2)

    # ---- optional badge, top right ----
    if spec.logo_path and os.path.exists(spec.logo_path):
        try:
            logo = Image.open(spec.logo_path).convert("RGBA")
            side = 108
            logo.thumbnail((side - 16, side - 16), Image.LANCZOS)
            plate = Image.new("RGBA", (side, side), (0, 0, 0, 0))
            ImageDraw.Draw(plate).rounded_rectangle(
                [0, 0, side - 1, side - 1], radius=14, fill=WHITE + (255,))
            plate.alpha_composite(logo, ((side - logo.width) // 2, (side - logo.height) // 2))
            canvas.alpha_composite(plate, (W - side - 64, 44))
        except Exception:  # noqa: BLE001
            pass

    # ---- verdict ----
    verdict_text = spec.verdict.upper()
    f_verdict = anton(92)
    draw.text((LEFT, Y_VERDICT), verdict_text, font=f_verdict, fill=accent, anchor=ANCHOR)
    y = Y_VERDICT + _text_h(draw, verdict_text, f_verdict) + GAP_VERDICT_NAME

    # ---- name ----
    lines = _split_name(spec.name)[:2]
    f_name = _fit(draw, max(lines, key=len) if lines else "", anton, NAME_MAX_W, 122, floor=54)
    lh = int(f_name.size * 0.94)
    for i, line in enumerate(lines):
        draw.text((LEFT, y + i * lh), line, font=f_name, fill=WHITE, anchor=ANCHOR)
    if lines:
        y += (len(lines) - 1) * lh + _text_h(draw, lines[-1], f_name)

    # ---- fixture ----
    if spec.opponent:
        y += GAP_NAME_VS
        f_vs, f_opp = semibold(32), medium(48)
        _draw_tracked(draw, (LEFT, y), "VS", f_vs, accent, tracking=3)
        y += _text_h(draw, "VS", f_vs) + GAP_VS_OPPONENT
        draw.text((LEFT, y), spec.opponent, font=f_opp, fill=WHITE, anchor=ANCHOR)
        y += _text_h(draw, spec.opponent, f_opp)

    # ---- rule ----
    y += GAP_OPPONENT_RULE
    draw.rectangle([LEFT, y, LEFT + 290, y + 3], fill=RULE)
    y += 3

    # ---- stat strip ----
    if spec.stats:
        y += GAP_RULE_STRIP
        f_strip = semibold(27)
        _draw_tracked(draw, (LEFT, y), spec.stat_label.upper(), f_strip, MUTED, tracking=3)
        y += _text_h(draw, spec.stat_label.upper(), f_strip) + GAP_STRIP_STATS

        # Columns are spaced off measured width, not a fixed pitch: "48" is
        # wider than "5" and a tracked "ASSISTS" is wider than either, so a
        # constant pitch either overlaps or leaves a gap depending on the week.
        f_num, f_label = anton(80), semibold(25)
        x = LEFT
        num_h = max(_text_h(draw, str(v), f_num) for v, _ in spec.stats[:3])
        for value, label in spec.stats[:3]:
            text, cap = str(value), str(label).upper()
            draw.text((x, y), text, font=f_num, fill=WHITE, anchor=ANCHOR)
            _draw_tracked(draw, (x, y + num_h + GAP_NUM_LABEL), cap, f_label, MUTED, tracking=2)
            x += max(_text_w(draw, text, f_num),
                     _text_w(draw, cap, f_label, tracking=2)) + 52

    # ---- confidence ----
    r, pitch = 27, 85
    f_conf = bold(31)
    conf_y = Y_STARS_CENTRE - r - GAP_CONF_STARS - _text_h(draw, "CONFIDENCE", f_conf)
    _draw_tracked(draw, (LEFT, conf_y), "CONFIDENCE", f_conf, WHITE, tracking=3)
    for i in range(5):
        _star(draw, LEFT + r + i * pitch, Y_STARS_CENTRE, r,
              WHITE if i < spec.confidence else (255, 255, 255, 55))

    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()
