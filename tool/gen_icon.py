"""Draws the app icon and writes every size each platform needs.

Usage: blender -b -P tool/gen_icon.py -- <project_root>
Runs in Blender only for its bundled numpy and PNG writer.
"""
import math
import os
import sys

import bpy
import numpy as np

ROOT = sys.argv[sys.argv.index("--") + 1]
MASTER = 1024
SUPER = 2


def smooth(edge0, edge1, x):
    t = np.clip((x - edge0) / (edge1 - edge0), 0, 1)
    return t * t * (3 - 2 * t)


def capsule(x, y, a, b, ra, rb):
    """Signed distance to a segment from a to b whose radius tapers ra to rb."""
    pax, pay = x - a[0], y - a[1]
    bax, bay = b[0] - a[0], b[1] - a[1]
    h = np.clip((pax * bax + pay * bay) / (bax * bax + bay * bay), 0, 1)
    return np.hypot(pax - bax * h, pay - bay * h) - (ra + (rb - ra) * h)


def draw(size):
    n = size * SUPER
    axis = (np.arange(n) + 0.5) / n * 2 - 1
    x, y = np.meshgrid(axis, -axis)
    ember = (0.0, 0.02)
    r = np.hypot(x - ember[0], y - ember[1])

    rgb = np.empty((n, n, 3))
    night = smooth(1.5, 0.0, np.hypot(x, y + 0.1))
    for c, (edge, centre) in enumerate([(0.02, 0.09), (0.024, 0.06), (0.04, 0.06)]):
        rgb[..., c] = edge + (centre - edge) * night
    halo = np.exp(-(r / 0.62) ** 2) * 0.9 + np.exp(-(r / 0.3) ** 2) * 1.1
    core = np.exp(-(r / 0.13) ** 2)
    rgb += halo[..., None] * np.array([1.0, 0.42, 0.1])
    rgb += core[..., None] * np.array([0.9, 0.8, 0.5])

    d = np.full_like(x, 1e9)
    d = np.minimum(d, (np.hypot(x / 0.17, (y + 0.27) / 0.2) - 1) * 0.17)
    d = np.minimum(d, np.hypot(x, y + 0.03) - 0.115)
    spread = [28, 60, 98, 132]
    for side in (-1, 1):
        for i, deg in enumerate(spread):
            a = math.radians(deg)
            hip = (side * 0.08, -0.03 + 0.03 * (1.5 - i))
            reach = 0.36 + 0.03 * (i in (0, 3))
            knee = (hip[0] + side * math.sin(a) * reach,
                    hip[1] + math.cos(a) * reach + 0.2)
            b = math.radians(deg + 28)
            foot = (knee[0] + side * math.sin(b) * 0.3,
                    knee[1] + math.cos(b) * 0.3 - 0.36)
            d = np.minimum(d, capsule(x, y, hip, knee, 0.03, 0.022))
            d = np.minimum(d, capsule(x, y, knee, foot, 0.022, 0.006))

    px = 2.0 / n
    body = smooth(px, -px, d)
    rim = smooth(-0.03, 0.0, d) * body
    shade = np.array([0.035, 0.02, 0.018]) + rim[..., None] * np.array([0.55, 0.2, 0.04])
    rgb = rgb * (1 - body[..., None]) + shade * body[..., None]

    for ex, ey, er in [(-0.04, 0.03, 0.017), (0.04, 0.03, 0.017),
                       (-0.075, -0.005, 0.011), (0.075, -0.005, 0.011)]:
        eye = np.hypot(x - ex, y - ey)
        rgb += (np.exp(-(eye / er) ** 2) * 1.6)[..., None] * np.array([1.0, 0.25, 0.1])

    rgb = np.clip(rgb, 0, 1) ** (1 / 1.15)
    return rgb.reshape(size, SUPER, size, SUPER, 3).mean(axis=(1, 3))


def rounded_mask(size, margin, radius):
    """macOS icons sit inset on a transparent rounded square."""
    axis = (np.arange(size * 4) + 0.5) / (size * 4) * 2 - 1
    x, y = np.meshgrid(axis, axis)
    half = 1 - margin
    q = np.hypot(np.maximum(np.abs(x) - half + radius, 0),
                 np.maximum(np.abs(y) - half + radius, 0)) - radius
    return smooth(1.0 / size, -1.0 / size, q).reshape(size, 4, size, 4).mean(axis=(1, 3))


def write(path, rgb, alpha=None):
    size = rgb.shape[0]
    rgba = np.ones((size, size, 4), dtype=np.float32)
    rgba[..., :3] = rgb
    if alpha is not None:
        rgba[..., 3] = alpha
    image = bpy.data.images.new("icon", size, size, alpha=True)
    image.pixels.foreach_set(rgba[::-1].ravel())
    image.filepath_raw = path
    image.file_format = "PNG"
    scene = bpy.context.scene
    scene.render.image_settings.color_mode = "RGBA" if alpha is not None else "RGB"
    image.save_render(path, scene=scene)
    bpy.data.images.remove(image)


def resize(rgb, size):
    if size == rgb.shape[0]:
        return rgb
    k = rgb.shape[0] // size
    if rgb.shape[0] % size == 0:
        return rgb.reshape(size, k, size, k, 3).mean(axis=(1, 3))
    return draw(size)


bpy.context.scene.view_settings.view_transform = "Standard"
master = draw(MASTER)
j = os.path.join

mac = j(ROOT, "macos/Runner/Assets.xcassets/AppIcon.appiconset")
for size in (16, 32, 64, 128, 256, 512, 1024):
    inset = draw(size) if size < 64 else resize(master, size)
    write(j(mac, f"app_icon_{size}.png"), inset, rounded_mask(size, 0.1, 0.4))

ios = j(ROOT, "ios/Runner/Assets.xcassets/AppIcon.appiconset")
for points, scales in [(20, (1, 2, 3)), (29, (1, 2, 3)), (40, (1, 2, 3)),
                       (60, (2, 3)), (76, (1, 2)), (83.5, (2,)), (1024, (1,))]:
    for scale in scales:
        name = f"Icon-App-{points:g}x{points:g}@{scale}x.png"
        write(j(ios, name), resize(master, round(points * scale)))

for density, size in [("mdpi", 48), ("hdpi", 72), ("xhdpi", 96),
                      ("xxhdpi", 144), ("xxxhdpi", 192)]:
    write(j(ROOT, f"android/app/src/main/res/mipmap-{density}/ic_launcher.png"),
          resize(master, size))

for name, size in [("icons/Icon-192.png", 192), ("icons/Icon-512.png", 512),
                   ("icons/Icon-maskable-192.png", 192),
                   ("icons/Icon-maskable-512.png", 512), ("favicon.png", 32)]:
    write(j(ROOT, "web", name), resize(master, size))
