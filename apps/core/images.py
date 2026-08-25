"""Generated placeholder imagery for demo/seed data.

Real product photography is not something a seed script can invent, so it
draws deterministic gradient tiles with the product name instead. That keeps
every storefront surface (grids, galleries, banners, category tiles) looking
like a real store rather than a wall of broken-image icons.

Production catalogs upload real images through the admin; nothing here runs
outside ``seed_data``.
"""
import hashlib
import io

from django.core.files.base import ContentFile
from PIL import Image, ImageDraw, ImageFont

# Palette pairs used for the gradients, chosen to stay readable behind white text.
GRADIENTS = [
    ((49, 46, 129), (67, 56, 202)),
    ((15, 23, 42), (51, 65, 85)),
    ((124, 45, 18), (249, 115, 22)),
    ((20, 83, 45), (22, 163, 74)),
    ((88, 28, 135), (168, 85, 247)),
    ((7, 89, 133), (14, 165, 233)),
    ((136, 19, 55), (225, 29, 72)),
    ((113, 63, 18), (245, 158, 11)),
]


def _seed_for(text):
    """Stable per-name index so the same product always gets the same look."""
    digest = hashlib.md5(text.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _load_font(size):
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _wrap(draw, text, font, max_width):
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines[:4]


def generate_image(text, width=800, height=800, subtitle="", kind="product"):
    """Return a ``ContentFile`` holding a JPEG placeholder for ``text``."""
    seed = _seed_for(text + kind)
    start, end = GRADIENTS[seed % len(GRADIENTS)]

    image = Image.new("RGB", (width, height), start)
    draw = ImageDraw.Draw(image)

    # Diagonal gradient.
    steps = max(width, height)
    for offset in range(steps):
        ratio = offset / steps
        colour = (
            int(start[0] + (end[0] - start[0]) * ratio),
            int(start[1] + (end[1] - start[1]) * ratio),
            int(start[2] + (end[2] - start[2]) * ratio),
        )
        draw.line([(offset, 0), (0, offset)], fill=colour, width=2)
        draw.line([(width, offset), (offset, height)], fill=colour, width=2)

    # Soft geometric accents so tiles are visually distinguishable.
    accent = (255, 255, 255)
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    for index in range(3):
        radius = int(width * (0.22 + index * 0.16))
        cx = int(width * (0.2 + ((seed >> (index * 3)) % 7) / 10))
        cy = int(height * (0.25 + ((seed >> (index * 5)) % 6) / 10))
        odraw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            outline=accent + (34,),
            width=max(2, width // 200),
        )
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(image)

    # Title.
    font_size = max(int(width / 13), 18)
    font = _load_font(font_size)
    lines = _wrap(draw, text, font, width * 0.78)
    line_height = font_size * 1.22
    total_height = line_height * len(lines)
    y = (height - total_height) / 2 - (font_size * 0.4 if subtitle else 0)

    for line in lines:
        text_width = draw.textlength(line, font=font)
        x = (width - text_width) / 2
        draw.text((x + 2, y + 2), line, font=font, fill=(0, 0, 0))  # shadow
        draw.text((x, y), line, font=font, fill=(255, 255, 255))
        y += line_height

    if subtitle:
        small = _load_font(max(int(font_size * 0.45), 12))
        subtitle_width = draw.textlength(subtitle, font=small)
        draw.text(
            ((width - subtitle_width) / 2, y + font_size * 0.25),
            subtitle,
            font=small,
            fill=(255, 255, 255),
        )

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=82, optimize=True)
    slug = hashlib.md5(f"{text}{kind}{width}x{height}".encode()).hexdigest()[:12]
    return ContentFile(buffer.getvalue(), name=f"{kind}-{slug}.jpg")


def gallery_for(name, count=3, subtitle=""):
    """A small set of visually distinct images for one product gallery."""
    angles = ["", "side view", "detail", "in use"]
    return [
        generate_image(
            name,
            subtitle=(subtitle if index == 0 else angles[index % len(angles)]),
            kind=f"product{index}",
        )
        for index in range(count)
    ]


def banner_image(title, subtitle=""):
    return generate_image(title, width=1600, height=560, subtitle=subtitle, kind="banner")


def category_image(name):
    return generate_image(name, width=400, height=400, kind="category")


def brand_logo(name):
    return generate_image(name, width=320, height=320, kind="brand")
