"""Generates the forest-floor and bark texture sets and the night-sky HDR.

Everything comes from this script: the forest floor is rendered top-down in
Cycles from scattered procedural needles, twigs, cones, leaves and bark chips;
the bark is a procedural height field; the sky is computed per pixel.
Fixed seeds make every run reproduce the same files.

Usage:
  blender -b -P tool/gen_surfaces.py -- <project_root> [--only floor,bark,sky]
                                        [--previews <dir>]

Writes assets/textures/forest_floor_{Diffuse,nor_gl,Rough}.jpg (2048; normal 1024),
assets/textures/bark_{Diffuse,nor_gl,Rough}.jpg (1024) and
assets/env/night_sky.hdr (1024x512). With --previews it also writes 3x3
tilings and lit Cycles renders of the surfaces into <dir>.
"""
import math
import os
import sys
import tempfile

import bpy
import numpy as np
import OpenImageIO as oiio

# Azimuth 0 is the centre column of the equirectangular image, increasing to
# the right. 36 degrees matches where the moon sat in moonlit_night.hdr.
MOON_AZIMUTH_DEG = 36.0
MOON_ELEVATION_DEG = 38.0
MOON_DIAMETER_DEG = 1.5
SEED = 20261109

FLOOR_SIZE = 2048
FLOOR_TILE_M = 6.5
BARK_SIZE = 1024
SKY_W, SKY_H = 1024, 512

# Mean sRGB albedo: an earthy floor brown, and the bark this replaces.
FLOOR_TARGET_SRGB = np.array([0.33, 0.26, 0.19])
BARK_TARGET_SRGB = np.array([0.402, 0.326, 0.253])


def srgb_to_linear(c):
    c = np.asarray(c, dtype=np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    c = np.clip(c, 0, None)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def fft_noise(n, seed, fmin, fmax, slope=1.0, aniso=(1.0, 1.0)):
    """Periodic band-limited noise, zero mean, unit std. Frequencies are in
    cycles per tile; aniso scales the (x, y) frequency axes before filtering."""
    rng = np.random.default_rng(seed)
    spec = np.fft.rfft2(rng.standard_normal((n, n)))
    fy = np.fft.fftfreq(n)[:, None] * n * aniso[1]
    fx = np.fft.rfftfreq(n)[None, :] * n * aniso[0]
    f = np.hypot(fx, fy)
    band = 1 / (1 + np.exp(np.clip(-(f - fmin) / (0.15 * fmin + 0.5), -60, 60)))
    band *= 1 / (1 + np.exp(np.clip((f - fmax) / (0.15 * fmax + 0.5), -60, 60)))
    band *= np.where(f > 0, np.maximum(f, 1e-6) ** -slope, 0)
    out = np.fft.irfft2(spec * band, s=(n, n))
    out -= out.mean()
    return out / (out.std() + 1e-12)


def gaussian_wrap(a, sigma):
    n0, n1 = a.shape[:2]
    fy = np.fft.fftfreq(n0)[:, None]
    fx = np.fft.rfftfreq(n1)[None, :]
    g = np.exp(-2 * (np.pi * sigma) ** 2 * (fx ** 2 + fy ** 2))
    if a.ndim == 3:
        return np.stack([np.fft.irfft2(np.fft.rfft2(a[..., i]) * g, s=(n0, n1))
                         for i in range(a.shape[2])], axis=-1)
    return np.fft.irfft2(np.fft.rfft2(a) * g, s=(n0, n1))


def sample_wrap(a, x, y, period):
    """Bilinear lookup in a periodic grid a[iy, ix] at world coordinates."""
    n = a.shape[0]
    u = np.asarray(x) / period * n
    v = np.asarray(y) / period * n
    i0 = np.floor(u).astype(np.int64)
    j0 = np.floor(v).astype(np.int64)
    fu, fv = u - i0, v - j0
    i0 %= n
    j0 %= n
    i1, j1 = (i0 + 1) % n, (j0 + 1) % n
    return ((a[j0, i0] * (1 - fu) + a[j0, i1] * fu) * (1 - fv)
            + (a[j1, i0] * (1 - fu) + a[j1, i1] * fu) * fv)


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def write_jpeg(path, img, quality=90):
    img = np.atleast_3d(np.asarray(img))
    h, w, c = img.shape
    data = (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)
    spec = oiio.ImageSpec(w, h, c, oiio.UINT8)
    spec.attribute("Compression", f"jpeg:{quality}")
    # Chroma subsampling smears normal-map vectors, so keep full chroma.
    spec.attribute("jpeg:subsampling", "4:4:4")
    out = oiio.ImageOutput.create(path)
    out.open(path, spec)
    out.write_image(np.ascontiguousarray(data))
    out.close()


def write_float(path, img):
    img = np.ascontiguousarray(np.atleast_3d(img).astype(np.float32))
    h, w, c = img.shape
    out = oiio.ImageOutput.create(path)
    out.open(path, oiio.ImageSpec(w, h, c, oiio.FLOAT))
    out.write_image(img)
    out.close()


def read_image(path):
    buf = oiio.ImageBuf(path)
    return buf.spec().channelnames, buf.get_pixels(oiio.FLOAT)


def match_mean_srgb(lin, target_srgb, iterations=6):
    """Scales linear albedo per channel until its sRGB mean hits the target."""
    for _ in range(iterations):
        mean = linear_to_srgb(lin).reshape(-1, 3).mean(0)
        lin = lin * (srgb_to_linear(target_srgb) / srgb_to_linear(mean))
    return np.clip(lin, 0, 1)


def flatten_low_freq(lin, sigma, strength):
    """Divides out a wrapped low-pass copy so the tile repeat does not read."""
    low = gaussian_wrap(lin, sigma)
    return lin * (lin.reshape(-1, lin.shape[-1]).mean(0) / np.maximum(low, 1e-4)) ** strength


def normal_from_height(h, scale):
    """OpenGL tangent-space normals; h is indexed [row, col] with row 0 at the top."""
    dx = (np.roll(h, -1, 1) - np.roll(h, 1, 1)) * 0.5 * scale
    dy_down = (np.roll(h, -1, 0) - np.roll(h, 1, 0)) * 0.5 * scale
    n = np.stack([-dx, dy_down, np.ones_like(h)], axis=-1)
    return n / np.linalg.norm(n, axis=-1, keepdims=True)


def reset_blender():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def setup_cycles(scene, samples, res_x, res_y, device="GPU"):
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.compute_device_type = "METAL"
    prefs.get_devices()
    for d in prefs.devices:
        d.use = d.type == "METAL"
    scene.render.engine = "CYCLES"
    scene.cycles.device = device
    scene.cycles.samples = samples
    scene.cycles.use_denoising = False
    scene.cycles.use_adaptive_sampling = False
    scene.render.resolution_x = res_x
    scene.render.resolution_y = res_y
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "Standard"


def set_output(scene, path, kind):
    s = scene.render.image_settings
    if kind == "exr_multilayer":
        if hasattr(s, "media_type"):
            s.media_type = "MULTI_LAYER_IMAGE"
        s.file_format = "OPEN_EXR_MULTILAYER"
        s.color_depth = "32"
    else:
        if hasattr(s, "media_type"):
            s.media_type = "IMAGE"
        s.file_format = "PNG"
        s.color_depth = "8"
    scene.render.filepath = path


def make_mesh(scene, name, verts, loops, sizes, col, rough, mat, smooth):
    me = bpy.data.meshes.new(name)
    me.vertices.add(len(verts))
    me.vertices.foreach_set("co", verts.astype(np.float32).ravel())
    me.loops.add(len(loops))
    me.loops.foreach_set("vertex_index", loops.astype(np.int32))
    me.polygons.add(len(sizes))
    starts = np.concatenate([[0], np.cumsum(sizes)[:-1]]).astype(np.int32)
    me.polygons.foreach_set("loop_start", starts)
    me.update(calc_edges=True)
    ca = me.color_attributes.new("col", "FLOAT_COLOR", "POINT")
    ca.data.foreach_set("color", np.c_[col, np.ones(len(col))].astype(np.float32).ravel())
    ra = me.attributes.new("rough", "FLOAT", "POINT")
    ra.data.foreach_set("value", rough.astype(np.float32))
    if smooth:
        me.shade_smooth()
    ob = bpy.data.objects.new(name, me)
    scene.collection.objects.link(ob)
    me.materials.append(mat)
    return ob


class Geo:
    """Accumulates vertex/face arrays for one mesh."""

    def __init__(self):
        self.v, self.loops, self.sizes, self.col, self.rough = [], [], [], [], []
        self.count = 0

    def add(self, verts, faces, col, rough):
        """verts (N, K, 3) per instance; faces is one (F, m) array of template
        indices into K, or a list of such arrays with different m."""
        n, k = verts.shape[:2]
        offset = (np.arange(n) * k + self.count)[:, None]
        for f in faces if isinstance(faces, list) else [faces]:
            self.loops.append((f.ravel()[None, :] + offset).ravel())
            self.sizes.append(np.full(n * len(f), f.shape[1]))
        self.v.append(verts.reshape(-1, 3))
        self.col.append(np.broadcast_to(col, (n, k, 3)).reshape(-1, 3))
        self.rough.append(np.broadcast_to(rough, (n, k)).ravel())
        self.count += n * k

    def build(self, scene, name, mat, smooth):
        return make_mesh(scene, name, np.concatenate(self.v), np.concatenate(self.loops),
                         np.concatenate(self.sizes), np.concatenate(self.col),
                         np.concatenate(self.rough), mat, smooth)


def wrap_instances(params, reach, period):
    """Copies every instance whose footprint crosses a tile edge to the
    opposite side, so the rendered tile wraps seamlessly."""
    out = {k: [] for k in params}
    x, y = params["x"], params["y"]
    for dx in (-period, 0.0, period):
        for dy in (-period, 0.0, period):
            m = ((x + dx > -reach) & (x + dx < period + reach)
                 & (y + dy > -reach) & (y + dy < period + reach))
            for k, v in params.items():
                vv = v[m]
                if k == "x":
                    vv = vv + dx
                elif k == "y":
                    vv = vv + dy
                out[k].append(vv)
    return {k: np.concatenate(v) for k, v in out.items()}


def scatter(rng, count, density, period):
    """Rejection-samples points with probability given by a periodic density grid."""
    pts = []
    have = 0
    while have < count:
        c = rng.uniform(0, period, (count * 3, 2))
        keep = rng.uniform(0, 1, len(c)) < sample_wrap(density, c[:, 0], c[:, 1], period)
        pts.append(c[keep])
        have += keep.sum()
    pts = np.concatenate(pts)[:count]
    return pts[:, 0], pts[:, 1]


def jitter_colors(rng, n, palette, weights, spread):
    pick = rng.choice(len(palette), n, p=np.asarray(weights) / np.sum(weights))
    base = np.asarray(palette)[pick]
    return base * rng.lognormal(0, spread, (n, 1)) * rng.lognormal(0, spread * 0.4, (n, 3))


def debris_material():
    mat = bpy.data.materials.new("debris")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    col = nt.nodes.new("ShaderNodeAttribute")
    col.attribute_name = "col"
    rough = nt.nodes.new("ShaderNodeAttribute")
    rough.attribute_name = "rough"
    bsdf = nt.nodes.new("ShaderNodeBsdfDiffuse")
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    aov = nt.nodes.new("ShaderNodeOutputAOV")
    aov.aov_name = "rough"
    nt.links.new(col.outputs["Color"], bsdf.inputs["Color"])
    nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
    nt.links.new(rough.outputs["Fac"], aov.inputs["Value"])
    return mat


def build_floor_scene(scene):
    T = FLOOR_TILE_M
    n = 2048
    mat = debris_material()
    rng = np.random.default_rng(SEED + 100)

    # Patch masks only use 15-65 cycles per tile (10-43 cm), so nothing larger
    # than about half a metre survives to show the tile repeat.
    def patches(seed, lo, hi):
        f = fft_noise(n, seed, 15, 65, 0.5) + 0.3 * fft_noise(n, seed + 50, 65, 250, 0.2)
        return smoothstep(lo, hi, f)

    moss = patches(SEED + 4, 1.2, 1.75)
    bare = patches(SEED + 12, 1.0, 1.5) * (1 - moss)
    moss_fine = fft_noise(n, SEED + 6, 300, 1024, 0.0)
    height = (0.0012 * fft_noise(n, SEED + 1, 15, 60, 0.6)
              + 0.0008 * fft_noise(n, SEED + 2, 60, 400, 0.3)
              + 0.0004 * fft_noise(n, SEED + 3, 300, 1024, 0.0))
    height += moss * (0.008 + 0.003 * moss_fine) - bare * 0.003
    needle_density = np.clip(0.75 + 0.25 * fft_noise(n, SEED + 7, 15, 60, 0.5), 0.3, 1) \
        * (1 - 0.92 * bare) * (1 - 0.6 * moss)
    litter_density = np.clip(1 - 0.5 * moss - 0.3 * bare, 0, 1)

    def ground(x, y):
        return sample_wrap(height, x, y, T)

    margin = 64
    k = np.arange(-margin, n + margin + 1)
    gx, gy = np.meshgrid(k * T / n, k * T / n)
    m = len(k)
    soil_mix = smoothstep(-0.6, 1.4, fft_noise(n, SEED + 9, 40, 300, 0.4))
    grit = fft_noise(n, SEED + 10, 250, 1024, 0.0)
    humus = np.array([0.030, 0.022, 0.016])
    mineral = np.array([0.075, 0.058, 0.042])
    moss_a = np.array([0.040, 0.046, 0.020])
    moss_b = np.array([0.085, 0.088, 0.040])
    sm = sample_wrap(soil_mix, gx, gy, T)[..., None]
    gr = sample_wrap(grit, gx, gy, T)[..., None]
    mo = sample_wrap(moss, gx, gy, T)[..., None]
    mf = sample_wrap(moss_fine, gx, gy, T)[..., None]
    ba = sample_wrap(bare, gx, gy, T)[..., None]
    soil_col = (humus * (1 - sm) + mineral * sm) * np.clip(1 + 0.45 * gr, 0.4, 2) * (1 - 0.3 * ba)
    moss_col = moss_a + (moss_b - moss_a) * np.clip(0.5 + 0.5 * mf, 0, 1)
    soil_col = soil_col * (1 - mo) + moss_col * mo
    verts = np.stack([gx, gy, ground(gx, gy)], -1).reshape(-1, 3)
    ii, jj = np.meshgrid(np.arange(m - 1), np.arange(m - 1))
    a = (jj * m + ii).ravel()
    quads = np.stack([a, a + 1, a + m + 1, a + m], -1)
    soil_rough = (0.96 - 0.04 * mo[..., 0]).ravel()
    make_mesh(scene, "soil", verts, quads.ravel(), np.full(len(quads), 4),
              soil_col.reshape(-1, 3), soil_rough, mat, True)

    needle_geo = Geo()

    def add_needles(count, length, width, palette, weights, pair, rough_v):
        bx, by = scatter(rng, count, needle_density, T)
        theta = rng.uniform(0, 2 * np.pi, count)
        L = rng.uniform(*length, count)
        if pair:
            spread = rng.uniform(0.04, 0.3, count) * rng.choice([-1, 1], count)
            bx, by = np.r_[bx, bx], np.r_[by, by]
            theta = np.r_[theta, theta + spread]
            L = np.r_[L, L * rng.uniform(0.92, 1.05, count)]
        cnt = len(bx)
        p = dict(x=bx, y=by, theta=theta, L=L, bend=rng.normal(0, 0.06, cnt),
                 off=rng.uniform(0.0005, 0.007, cnt), col=jitter_colors(rng, cnt, palette, weights, 0.2))
        p = wrap_instances(p, length[1] + 0.01, T)
        cnt = len(p["x"])
        t = np.linspace(0, 1, 5)
        along = p["L"][:, None] * t
        lateral = p["bend"][:, None] * p["L"][:, None] * 4 * t * (1 - t)
        c, s = np.cos(p["theta"])[:, None], np.sin(p["theta"])[:, None]
        cx = p["x"][:, None] + c * along - s * lateral
        cy = p["y"][:, None] + s * along + c * lateral
        w = width * (1 - 0.8 * t ** 4)
        side = np.array([-0.5, 0.0, 0.5])
        vx = cx[:, :, None] - s[:, :, None] * w[None, :, None] * side
        vy = cy[:, :, None] + c[:, :, None] * w[None, :, None] * side
        vz = (ground(vx, vy) + p["off"][:, None, None]
              + np.array([0.0, 0.35, 0.0])[None, None, :] * w[None, :, None])
        verts = np.stack([vx, vy, vz], -1).reshape(cnt, 15, 3)
        faces = np.array([[sx * 3 + cc, (sx + 1) * 3 + cc, (sx + 1) * 3 + cc + 1, sx * 3 + cc + 1]
                          for sx in range(4) for cc in range(2)])
        shade = np.repeat((1 - 0.25 * t ** 3), 3)[None, :, None]
        rough = np.full((cnt, 15), rough_v) + rng.normal(0, 0.04, (cnt, 1))
        needle_geo.add(verts, faces, p["col"][:, None, :] * shade, rough)

    needle_pal = [[0.20, 0.12, 0.065], [0.15, 0.095, 0.058], [0.095, 0.067, 0.045],
                  [0.058, 0.044, 0.032], [0.22, 0.19, 0.15]]
    add_needles(120000, (0.05, 0.095), 0.0019, needle_pal, [2, 4, 4, 3, 1.5], True, 0.8)
    add_needles(200000, (0.012, 0.028), 0.0015, needle_pal, [1, 3, 4, 4, 1], False, 0.82)
    needle_geo.build(scene, "needles", mat, False)

    tube_geo = Geo()
    ring_k = 8

    def add_tubes(p, segs):
        cnt = len(p["x"])
        t = np.linspace(0, 1, segs + 1)
        ang = p["theta"][:, None] + p["curve"][:, None] * (t - 0.5) + np.cumsum(
            p["wiggle"][:, None] * np.sin(t * 7 + p["phase"][:, None]) * 0.08, axis=1)
        step = p["L"][:, None] / segs
        cx = p["x"][:, None] + np.concatenate(
            [np.zeros((cnt, 1)), np.cumsum(np.cos(ang) * step, 1)[:, :-1]], 1)
        cy = p["y"][:, None] + np.concatenate(
            [np.zeros((cnt, 1)), np.cumsum(np.sin(ang) * step, 1)[:, :-1]], 1)
        r = p["r"][:, None] * (1 - p["taper"][:, None] * t)
        # Sticks rest on their high points rather than following every dip.
        gz = ground(cx, cy)
        cz = np.maximum(gz, gz.max(axis=1, keepdims=True) - 0.004) + r * 0.75 + p["off"][:, None]
        sx, sy = -np.sin(ang), np.cos(ang)
        phi = np.linspace(0, 2 * np.pi, ring_k, endpoint=False)
        vx = cx[..., None] + r[..., None] * np.cos(phi) * sx[..., None]
        vy = cy[..., None] + r[..., None] * np.cos(phi) * sy[..., None]
        vz = cz[..., None] + r[..., None] * np.sin(phi)
        verts = np.stack([vx, vy, vz], -1).reshape(cnt, (segs + 1) * ring_k, 3)
        faces = np.array([[s_ * ring_k + j, s_ * ring_k + (j + 1) % ring_k,
                           (s_ + 1) * ring_k + (j + 1) % ring_k, (s_ + 1) * ring_k + j]
                          for s_ in range(segs) for j in range(ring_k)])
        # Pale weathered wood with patches of darker bark left on it.
        bark_left = smoothstep(0.2, 0.7, np.sin(t[None, :] * p["L"][:, None] * p["freq"][:, None]
                                                + p["phase"][:, None]) * 0.5 + 0.5) * p["barky"][:, None]
        col = p["col"][:, None, :] * (1 - bark_left[..., None]) + p["dark"][:, None, :] * bark_left[..., None]
        col = np.repeat(col, ring_k, axis=1)
        rough = np.repeat(0.78 - 0.06 * (1 - bark_left), ring_k, axis=1)
        tube_geo.add(verts, faces, col, rough)

    def sticks(count, length, radius):
        x, y = scatter(rng, count, litter_density, T)
        p = dict(x=x, y=y, theta=rng.uniform(0, 2 * np.pi, count),
                 L=np.exp(rng.uniform(np.log(length[0]), np.log(length[1]), count)),
                 r=np.exp(rng.uniform(np.log(radius[0]), np.log(radius[1]), count)),
                 curve=rng.normal(0, 0.3, count), wiggle=rng.normal(0, 1, count),
                 phase=rng.uniform(0, 6.3, count), taper=rng.uniform(0.2, 0.5, count),
                 off=rng.uniform(0, 0.004, count), freq=rng.uniform(15, 50, count),
                 barky=rng.uniform(0, 1, count) ** 1.5,
                 col=jitter_colors(rng, count, [[0.36, 0.31, 0.25], [0.27, 0.22, 0.16],
                                                [0.17, 0.12, 0.08], [0.09, 0.065, 0.045]],
                                   [3, 3, 2, 2], 0.12),
                 dark=jitter_colors(rng, count, [[0.06, 0.045, 0.035], [0.10, 0.07, 0.05]], [1, 1], 0.1))
        # Side shoots start part-way along a parent and share its colours.
        kids = rng.uniform(0, 1, count) < 0.5
        at = rng.uniform(0.25, 0.75, count)[kids]
        kid = {key: v[kids].copy() for key, v in p.items()}
        kid["x"] = x[kids] + np.cos(p["theta"][kids]) * p["L"][kids] * at
        kid["y"] = y[kids] + np.sin(p["theta"][kids]) * p["L"][kids] * at
        kid["theta"] += rng.choice([-1, 1], kids.sum()) * rng.uniform(0.4, 0.9, kids.sum())
        kid["L"] *= rng.uniform(0.2, 0.45, kids.sum())
        kid["r"] *= 0.5
        p = {key: np.concatenate([p[key], kid[key]]) for key in p}
        add_tubes(wrap_instances(p, length[1] + 0.02, T), 10)

    sticks(240, (0.15, 0.6), (0.004, 0.0125))
    sticks(1100, (0.05, 0.2), (0.0015, 0.004))
    tube_geo.build(scene, "sticks", mat, True)

    debris_geo = Geo()
    cone_n = 70
    x, y = scatter(rng, cone_n, litter_density, T)
    cones = wrap_instances(dict(x=x, y=y, theta=rng.uniform(0, 2 * np.pi, cone_n),
                                L=rng.uniform(0.05, 0.09, cone_n), r=rng.uniform(0.016, 0.024, cone_n),
                                shade=rng.uniform(0.7, 1.3, cone_n)), 0.1, T)
    cn = len(cones["x"])
    ms = 90
    j = np.arange(ms)
    tt = 0.04 + 0.92 * j / ms
    phi = j * 2.39996323
    ax = np.stack([np.cos(cones["theta"]), np.sin(cones["theta"]), np.zeros(cn)], -1)
    up = np.array([0.0, 0.0, 1.0])
    sd = np.cross(up, ax)
    prof = np.sin(np.pi * tt) ** 0.6
    center = np.stack([cones["x"], cones["y"], ground(cones["x"], cones["y"]) + cones["r"] * 0.85], -1)
    radial = np.cos(phi)[None, :, None] * sd[:, None, :] + np.sin(phi)[None, :, None] * up
    along = ((tt - 0.5)[None, :, None] * cones["L"][:, None, None]) * ax[:, None, :]
    rc = cones["r"][:, None, None] * prof[None, :, None]
    base_p = center[:, None, :] + along + radial * rc * 0.45
    tip_p = center[:, None, :] + along + radial * rc - ax[:, None, :] * cones["L"][:, None, None] * 0.06
    tang = np.cross(ax[:, None, :], radial)
    ws = (cones["r"][:, None] * (0.25 + 0.35 * prof))[..., None]
    sv = np.stack([base_p - tang * ws * 0.5, base_p + tang * ws * 0.5,
                   tip_p + tang * ws * 0.45, tip_p - tang * ws * 0.45], axis=2).reshape(cn * ms, 4, 3)
    scol = (np.array([0.15, 0.09, 0.05]) * cones["shade"][:, None]).repeat(ms, 0)[:, None, :]
    scol = scol * np.array([0.8, 0.8, 1.15, 1.15])[None, :, None]
    debris_geo.add(sv, np.array([[0, 1, 2, 3]]), scol, np.full((cn * ms, 4), 0.75))

    # Leaves gather in loose drifts of a few around shared centres.
    leaf_n = 1700
    x, y = scatter(rng, leaf_n // 4, litter_density, T)
    x = np.repeat(x, 4) + rng.normal(0, 0.06, leaf_n)
    y = np.repeat(y, 4) + rng.normal(0, 0.06, leaf_n)
    leaves = wrap_instances(dict(
        x=x, y=y, theta=rng.uniform(0, 2 * np.pi, leaf_n),
        L=rng.uniform(0.04, 0.09, leaf_n), w=rng.uniform(0.3, 0.85, leaf_n),
        blotch=rng.uniform(0.6, 1.25, (leaf_n, 63)), tip=rng.uniform(0.0, 0.6, leaf_n),
        curl=rng.uniform(-0.1, 0.9, leaf_n), bend=rng.normal(0, 0.3, leaf_n),
        tilt=rng.normal(0, 0.15, leaf_n), off=rng.uniform(0.001, 0.01, leaf_n),
        col=jitter_colors(rng, leaf_n, [[0.42, 0.33, 0.19], [0.32, 0.20, 0.08], [0.20, 0.11, 0.05],
                                        [0.12, 0.075, 0.045], [0.07, 0.05, 0.035]],
                          [1.5, 2, 3, 3, 2], 0.15)), 0.06, T)
    ln = len(leaves["x"])
    nu, nv = 9, 7
    u = np.linspace(0, 1, nu)
    v = np.linspace(-1, 1, nv)
    half_w = (np.sin(np.pi * u) ** 0.75)[:, None] * 0.5
    lu = (u[:, None] - 0.5) * np.ones((1, nv))
    L = leaves["L"][:, None, None]
    W = L * leaves["w"][:, None, None]
    px, py = lu * L, half_w * v[None, :] * W
    pz = (leaves["curl"][:, None, None] * (v[None, None, :] ** 2) * half_w * W * 1.2
          + leaves["bend"][:, None, None] * (lu ** 2) * L
          + leaves["tilt"][:, None, None] * py)
    c, s = np.cos(leaves["theta"])[:, None, None], np.sin(leaves["theta"])[:, None, None]
    wx = leaves["x"][:, None, None] + c * px - s * py
    wy = leaves["y"][:, None, None] + s * px + c * py
    wz = (ground(leaves["x"], leaves["y"])[:, None, None] + leaves["off"][:, None, None]
          + pz - pz.min(axis=(1, 2), keepdims=True))
    lverts = np.stack([wx, wy, wz], -1).reshape(ln, nu * nv, 3)
    lfaces = np.array([[a_ * nv + b_, (a_ + 1) * nv + b_, (a_ + 1) * nv + b_ + 1, a_ * nv + b_ + 1]
                       for a_ in range(nu - 1) for b_ in range(nv - 1)])
    vein = 1 - 0.2 * np.exp(-(v / 0.2) ** 2)
    edge = 1 - 0.25 * np.abs(v) ** 3
    lc = leaves["col"][:, None, None, :] * (vein * edge)[None, None, :, None] * np.ones((1, nu, 1, 1))
    # Decay darkens leaves in blotches and from the tip inward.
    decay = leaves["blotch"].reshape(ln, nu, nv) * (1 - leaves["tip"][:, None, None] * u[None, :, None] ** 2)
    lc = lc * decay[..., None]
    lrough = 0.58 + 0.08 * rng.uniform(0, 1, (ln, 1)) * np.ones((1, nu * nv))
    debris_geo.add(lverts, lfaces, lc.reshape(ln, nu * nv, 3), lrough)

    stone_n = 130
    x, y = scatter(rng, stone_n, litter_density, T)
    nl, ns_ = 5, 10
    lat = np.linspace(-np.pi / 2, np.pi / 2, nl + 2)[1:-1]
    lon = np.linspace(0, 2 * np.pi, ns_, endpoint=False)
    dirs = np.concatenate([[[0, 0, -1]],
                           np.stack([np.cos(lat)[:, None] * np.cos(lon), np.cos(lat)[:, None] * np.sin(lon),
                                     np.sin(lat)[:, None] * np.ones_like(lon)], -1).reshape(-1, 3),
                           [[0, 0, 1]]])
    nvs = len(dirs)
    stones = wrap_instances(dict(
        x=x, y=y, theta=rng.uniform(0, 2 * np.pi, stone_n),
        R=np.exp(rng.uniform(np.log(0.01), np.log(0.03), stone_n)),
        asp=rng.uniform(1.0, 1.6, stone_n), flat=rng.uniform(0.45, 0.7, stone_n),
        bump=rng.uniform(0.85, 1.15, (stone_n, nvs)),
        col=jitter_colors(rng, stone_n, [[0.17, 0.16, 0.14], [0.12, 0.11, 0.10], [0.22, 0.20, 0.17]],
                          [3, 2, 1], 0.12)), 0.08, T)
    sn = len(stones["x"])
    d = dirs[None] * stones["bump"][..., None]
    lx_ = d[..., 0] * stones["R"][:, None] * stones["asp"][:, None]
    ly_ = d[..., 1] * stones["R"][:, None]
    lz_ = d[..., 2] * stones["R"][:, None] * stones["flat"][:, None]
    c, s = np.cos(stones["theta"])[:, None], np.sin(stones["theta"])[:, None]
    sz = ground(stones["x"], stones["y"])[:, None] + stones["R"][:, None] * stones["flat"][:, None] * 0.35
    sverts = np.stack([stones["x"][:, None] + c * lx_ - s * ly_, stones["y"][:, None] + s * lx_ + c * ly_,
                       sz + lz_], -1)
    ring = lambda i, jj_: 1 + i * ns_ + jj_ % ns_
    squads = np.array([[ring(i, jj_), ring(i, jj_ + 1), ring(i + 1, jj_ + 1), ring(i + 1, jj_)]
                       for i in range(nl - 1) for jj_ in range(ns_)])
    stris = np.array([[0, ring(0, jj_ + 1), ring(0, jj_)] for jj_ in range(ns_)]
                     + [[nvs - 1, ring(nl - 1, jj_), ring(nl - 1, jj_ + 1)] for jj_ in range(ns_)])
    scol_v = stones["col"][:, None, :] * (0.9 + 0.2 * (stones["bump"] - 0.85) / 0.3)[..., None]
    stone_geo = Geo()
    stone_geo.add(sverts, [squads, stris], scol_v, np.full((sn, nvs), 0.62))
    stone_geo.build(scene, "stones", mat, True)

    chip_n = 750
    x, y = scatter(rng, chip_n, litter_density, T)
    kk = 9
    chips = wrap_instances(dict(
        x=x, y=y, theta=rng.uniform(0, 2 * np.pi, chip_n),
        R=np.exp(rng.uniform(np.log(0.013), np.log(0.04), chip_n)),
        asp=rng.uniform(1.0, 2.2, chip_n), th=rng.uniform(0.003, 0.009, chip_n),
        off=rng.uniform(0.0, 0.005, chip_n), radii=rng.uniform(0.55, 1.0, (chip_n, kk)),
        col=jitter_colors(rng, chip_n, [[0.20, 0.10, 0.055], [0.14, 0.08, 0.05], [0.24, 0.19, 0.14],
                                        [0.08, 0.055, 0.04]], [3, 3, 2, 2], 0.12)), 0.09, T)
    hn = len(chips["x"])
    angs = np.linspace(0, 2 * np.pi, kk, endpoint=False)
    rad = chips["radii"] * chips["R"][:, None]
    lx_ = np.cos(angs) * rad * chips["asp"][:, None]
    ly_ = np.sin(angs) * rad
    c, s = np.cos(chips["theta"])[:, None], np.sin(chips["theta"])[:, None]
    rx = chips["x"][:, None] + c * lx_ - s * ly_
    ry = chips["y"][:, None] + s * lx_ + c * ly_
    gz = ground(chips["x"], chips["y"])[:, None] + chips["off"][:, None]
    centre = np.stack([chips["x"], chips["y"], gz[:, 0] + chips["th"] * 1.4], -1)[:, None, :]
    top = np.stack([rx, ry, np.broadcast_to(gz + chips["th"][:, None], rx.shape)], -1)
    bot = np.stack([rx, ry, np.broadcast_to(gz - 0.002, rx.shape)], -1)
    hverts = np.concatenate([centre, top, bot], axis=1)
    tris = np.array([[0, 1 + i, 1 + (i + 1) % kk] for i in range(kk)])
    sides = np.array([[1 + kk + i, 1 + kk + (i + 1) % kk, 1 + (i + 1) % kk, 1 + i] for i in range(kk)])
    # Broken edges show the paler, redder inner bark.
    hcol = np.concatenate([chips["col"][:, None, :], np.repeat(chips["col"][:, None, :] * 0.85, kk, 1),
                           np.repeat(chips["col"][:, None, :] * np.array([1.6, 1.3, 1.1]), kk, 1)], 1)
    chip_geo = Geo()
    chip_geo.add(hverts, [tris, sides], hcol, np.full((hn, 1 + 2 * kk), 0.82))
    chip_geo.build(scene, "chips", mat, False)
    debris_geo.build(scene, "debris", mat, False)


def render_floor(tmp, previews):
    reset_blender()
    scene = bpy.data.scenes.new("floor")
    T = FLOOR_TILE_M
    world = bpy.data.worlds.new("w")
    scene.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.0
    world.light_settings.distance = 0.04
    build_floor_scene(scene)

    cam_data = bpy.data.cameras.new("cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = T
    cam_data.clip_start = 0.1
    cam_data.clip_end = 100
    cam = bpy.data.objects.new("cam", cam_data)
    cam.location = (T / 2, T / 2, 10)
    scene.collection.objects.link(cam)
    scene.camera = cam

    # The GPU path differs run to run in a few pixels; the CPU is bit-exact.
    setup_cycles(scene, 48, FLOOR_SIZE, FLOOR_SIZE, "CPU")
    scene.cycles.seed = SEED % 1000
    scene.cycles.filter_width = 1.0
    scene.cycles.max_bounces = 0
    vl = scene.view_layers[0]
    vl.use_pass_diffuse_color = True
    vl.use_pass_normal = True
    vl.use_pass_ambient_occlusion = True
    aov = vl.aovs.add()
    aov.name = "rough"
    aov.type = "VALUE"
    exr = os.path.join(tmp, "floor_passes.exr")
    set_output(scene, exr, "exr_multilayer")
    bpy.ops.render.render(write_still=True, scene=scene.name)

    if previews:
        # Raking light over the real geometry, to compare with the textured plane.
        sun = bpy.data.lights.new("sun", "SUN")
        sun.energy = 4.0
        sun.angle = math.radians(1)
        so = bpy.data.objects.new("sun", sun)
        so.rotation_euler = (0, math.radians(-72), 0)
        scene.collection.objects.link(so)
        world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.3, 0.35, 0.5, 1)
        world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.15
        cam_data.ortho_scale = 1.2
        cam.location = (1.6, 1.6, 10)
        scene.cycles.max_bounces = 4
        scene.cycles.samples = 64
        scene.render.resolution_x = scene.render.resolution_y = 768
        vl.use_pass_diffuse_color = vl.use_pass_normal = vl.use_pass_ambient_occlusion = False
        set_output(scene, os.path.join(previews, "floor_geometry_raking.png"), "png")
        bpy.ops.render.render(write_still=True, scene=scene.name)
    return exr


def read_passes(path):
    """Returns {pass name: (h, w, c) array} from a Blender multilayer EXR,
    which may store the passes as separate parts."""
    inp = oiio.ImageInput.open(path)
    passes = {}
    k = 0
    while inp.seek_subimage(k, 0):
        pix = inp.read_image(oiio.FLOAT)
        for i, name in enumerate(inp.spec().channelnames):
            layer = name.rsplit(".", 1)[0].split(".")[-1]
            passes.setdefault(layer, []).append(pix[..., i])
        k += 1
    inp.close()
    return {k_: np.stack(v, -1) for k_, v in passes.items()}


def finish_floor(exr, root):
    passes = read_passes(exr)
    diff = passes["Diffuse Color" if "Diffuse Color" in passes else "DiffCol"][..., :3]
    nrm = passes["Normal"][..., :3]
    ao = passes["Ambient Occlusion" if "Ambient Occlusion" in passes else "AO"][..., 0]
    rough = passes["rough"][..., 0]
    albedo = diff * (0.6 + 0.4 * ao)[..., None]
    albedo = flatten_low_freq(albedo, FLOOR_SIZE / 20, 0.9)
    albedo = match_mean_srgb(albedo, FLOOR_TARGET_SRGB)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=-1, keepdims=True), 1e-6)
    rough = flatten_low_freq(rough[..., None], FLOOR_SIZE / 20, 0.8)[..., 0]
    tex = os.path.join(root, "assets", "textures")
    write_jpeg(os.path.join(tex, "forest_floor_Diffuse.jpg"), linear_to_srgb(albedo))
    # The build keeps normal maps out of the small encoding, so at full size
    # this one file outweighs every other asset.
    half = nrm.reshape(FLOOR_SIZE // 2, 2, FLOOR_SIZE // 2, 2, 3).mean(axis=(1, 3))
    half /= np.linalg.norm(half, axis=-1, keepdims=True)
    write_jpeg(os.path.join(tex, "forest_floor_nor_gl.jpg"), half * 0.5 + 0.5)
    write_jpeg(os.path.join(tex, "forest_floor_Rough.jpg"), np.clip(rough, 0.3, 1))


def periodic_worley(n, cells_x, cells_y, seed, warp_x, warp_y, stretch):
    """F1/F2 cellular distances in pixels on a periodic n x n grid. Cells are
    cells_x by cells_y; distances are measured with y divided by stretch."""
    rng = np.random.default_rng(seed)
    jit = rng.uniform(0.1, 0.9, (cells_y, cells_x, 2))
    cw, ch = n / cells_x, n / cells_y
    rows, cols = np.mgrid[0:n, 0:n].astype(np.float64)
    px = cols + warp_x
    py = rows + warp_y
    ci = np.floor(px / cw).astype(int)
    cj = np.floor(py / ch).astype(int)
    f1 = np.full((n, n), 1e9)
    f2 = np.full((n, n), 1e9)
    ids = np.zeros((n, n), dtype=np.int64)
    for dj in (-1, 0, 1):
        for di in (-1, 0, 1):
            i = ci + di
            jj = cj + dj
            fx = (i + jit[jj % cells_y, i % cells_x, 0]) * cw
            fy = (jj + jit[jj % cells_y, i % cells_x, 1]) * ch
            d = np.hypot(fx - px, (fy - py) / stretch)
            cid = (jj % cells_y) * cells_x + (i % cells_x)
            closer = d < f1
            f2 = np.where(closer, f1, np.minimum(f2, d))
            ids = np.where(closer, cid, ids)
            f1 = np.where(closer, d, f1)
    return f1, f2, ids


def make_bark(root):
    n = BARK_SIZE
    rng = np.random.default_rng(SEED + 300)
    wx = 14 * fft_noise(n, SEED + 301, 1.5, 6, 1.0, aniso=(0.4, 2.5))
    wy = 20 * fft_noise(n, SEED + 303, 1.5, 6, 1.0)
    # Furrows follow the zero crossings of noise stretched along V, so they
    # wander, merge and split like real bark; cell edges add the cross breaks.
    ridge = fft_noise(n, SEED + 313, 7, 16, 1.0, aniso=(1.0, 6.0))
    ridge += 0.25 * fft_noise(n, SEED + 314, 16, 40, 0.5, aniso=(1.0, 4.0))
    cells_x, cells_y = 12, 4
    f1, f2, ids = periodic_worley(n, cells_x, cells_y, SEED + 304, wx, wy, 3.0)
    breaks = 1 - smoothstep(0, 9, f2 - f1)
    breaks *= smoothstep(-0.3, 0.6, fft_noise(n, SEED + 305, 6, 24, 0.5))
    plate = smoothstep(0.08, 0.75, np.abs(ridge)) * (1 - 0.85 * breaks)
    pr = rng.uniform(0, 1, cells_x * cells_y)[ids]
    # A finer cell layer splits each plate into overlapping flakes.
    s1, s2, sid = periodic_worley(n, 40, 30, SEED + 306, wx * 0.5, wy * 0.5, 1.6)
    flake_edge = smoothstep(0, 3, s2 - s1)
    fr = rng.uniform(0, 1, 40 * 30)[sid]
    crumb = fft_noise(n, SEED + 307, 60, 400, 0.5)
    fibre = fft_noise(n, SEED + 308, 30, 300, 0.3, aniso=(1.0, 5.0))
    h = plate ** 0.6 * (0.8 + 0.2 * pr)
    h += plate * (0.10 * fr * flake_edge - 0.05 * (1 - flake_edge) + 0.025 * crumb)
    h += (1 - plate) * 0.06 * fibre + 0.03 * fibre
    h += 0.03 * fft_noise(n, SEED + 310, 3, 12, 0.5)

    furrow = np.array([0.012, 0.008, 0.006])
    wall = np.array([0.060, 0.032, 0.021])
    top = np.array([0.10, 0.086, 0.072])
    lichen = np.array([0.17, 0.18, 0.14])
    top_m = smoothstep(0.45, 0.85, plate)[..., None]
    col = furrow + (wall - furrow) * smoothstep(0.02, 0.4, plate)[..., None]
    col = col * (1 - top_m) + top * (0.8 + 0.4 * pr[..., None]) * top_m
    # Flakes that have fallen off expose the redder inner layers.
    peeled = (smoothstep(0.3, 0.15, fr) * plate)[..., None] * 0.6
    col = col * (1 - peeled) + wall * 1.3 * peeled
    col *= (1 + 0.1 * crumb + 0.1 * fibre + 0.25 * (fr - 0.5) * plate)[..., None]
    lm = (smoothstep(2.3, 2.9, fft_noise(n, SEED + 312, 40, 200, 0.2)) * top_m[..., 0])[..., None]
    col = col * (1 - lm) + lichen * lm
    cavity = np.clip(gaussian_wrap(h, 6) - h, 0, None)
    col *= np.clip(1 - 2.0 * cavity, 0.5, 1)[..., None]
    col = flatten_low_freq(col, n / 10, 0.6)
    col = match_mean_srgb(col, BARK_TARGET_SRGB)

    nrm = normal_from_height(h, 22.0)
    rough = 0.82 + 0.1 * (1 - plate) - 0.08 * peeled[..., 0] + 0.05 * lm[..., 0] + 0.03 * crumb
    tex = os.path.join(root, "assets", "textures")
    write_jpeg(os.path.join(tex, "bark_Diffuse.jpg"), linear_to_srgb(col))
    write_jpeg(os.path.join(tex, "bark_nor_gl.jpg"), nrm * 0.5 + 0.5)
    write_jpeg(os.path.join(tex, "bark_Rough.jpg"), np.clip(rough, 0.3, 1))


def make_sky(root):
    w, h = SKY_W, SKY_H
    rng = np.random.default_rng(SEED + 400)
    ss = 4
    xs = (np.arange(w) + 0.5) / w
    az = (xs - 0.5) * 2 * np.pi
    elev = (0.5 - (np.arange(h) + 0.5) / h) * np.pi
    E, A = np.meshgrid(elev, az, indexing="ij")
    dirs = np.stack([np.cos(E) * np.sin(A), np.sin(E), np.cos(E) * np.cos(A)], -1)

    me, ma = math.radians(MOON_ELEVATION_DEG), math.radians(MOON_AZIMUTH_DEG)
    moon_dir = np.array([math.cos(me) * math.sin(ma), math.sin(me), math.cos(me) * math.cos(ma)])
    cosd = np.clip(dirs @ moon_dir, -1, 1)
    dang = np.arccos(cosd)

    e_up = np.clip(E, 0, None)
    zenith = np.array([0.15, 0.225, 0.36])
    horizon = np.array([0.50, 0.60, 0.85])
    blend = np.exp(-e_up / 0.42)[..., None]
    sky = zenith * (1 - blend) + horizon * blend
    glow = 0.9 * np.exp(-dang / 0.035) + 0.45 * np.exp(-dang / 0.22) + 0.15 * np.exp(-dang / 0.8)
    sky = sky + glow[..., None] * np.array([0.85, 0.92, 1.0]) * 0.6

    # A faint Milky Way band along a tilted great circle.
    pole = np.array([0.45, 0.55, -0.70])
    pole /= np.linalg.norm(pole)
    band_d = np.abs(dirs @ pole)
    mw_noise = 0.5 + 0.5 * np.tanh(fft_noise(1024, SEED + 402, 8, 60, 0.6)[:h, :w] * 0.8)
    sky = sky + (np.exp(-(band_d / 0.1) ** 2) * mw_noise * 0.05)[..., None] * np.array([0.9, 0.95, 1.0])

    pix_omega = np.cos(elev)[:, None] * (np.pi / h) * (2 * np.pi / w)
    stars = np.zeros((h, w, 3))
    ns = 7000
    sz = rng.uniform(-0.05, 1, ns)
    sa = rng.uniform(-np.pi, np.pi, ns)
    flux = np.minimum(1.5e-6 * rng.pareto(1.8, ns) + 5e-7, 4e-4)
    temp = rng.uniform(0, 1, ns)
    scol = np.stack([0.85 + 0.3 * temp, 0.92 + 0.06 * temp, 1.15 - 0.35 * temp], -1)
    fy = (0.5 - np.arcsin(sz) / np.pi) * h - 0.5
    fx = (sa / (2 * np.pi) + 0.5) * w - 0.5
    y0, x0 = np.floor(fy).astype(int), np.floor(fx).astype(int)
    for oy in (0, 1):
        for ox in (0, 1):
            wgt = (1 - np.abs(fy - (y0 + oy))) * (1 - np.abs(fx - (x0 + ox)))
            yy = np.clip(y0 + oy, 0, h - 1)
            xx = (x0 + ox) % w
            np.add.at(stars, (yy, xx), (flux * wgt)[:, None] * scol)
    stars /= pix_omega[..., None]
    sky = sky + stars * smoothstep(0.0, 0.3, dang / 0.2)[..., None]

    moon_r = math.radians(MOON_DIAMETER_DEG / 2)
    # The game lights the scene with its own directional moon, so the disc
    # only needs to show in reflections; at full strength it floods the
    # image-based lighting and the night turns to day.
    moon_flux = 0.25
    moon_lum = moon_flux / (np.pi * moon_r ** 2)
    near = dang < moon_r * 3
    rows, cols = np.nonzero(near)
    sub = 8
    cover = np.zeros(len(rows))
    shade = np.zeros(len(rows))
    tex_n = fft_noise(64, SEED + 403, 2, 10, 1.0)
    for sy_ in range(sub):
        for sx_ in range(sub):
            ee = (0.5 - (rows + (sy_ + 0.5) / sub) / h) * np.pi
            aa = ((cols + (sx_ + 0.5) / sub) / w - 0.5) * 2 * np.pi
            d = np.stack([np.cos(ee) * np.sin(aa), np.sin(ee), np.cos(ee) * np.cos(aa)], -1)
            ang = np.arccos(np.clip(d @ moon_dir, -1, 1))
            inside = ang < moon_r
            rr = np.clip(ang / moon_r, 0, 1)
            limb = 0.6 + 0.4 * np.sqrt(1 - rr ** 2)
            right = np.cross(np.array([0, 1, 0]), moon_dir)
            right /= np.linalg.norm(right)
            upv = np.cross(moon_dir, right)
            u = (d @ right) / moon_r
            v = (d @ upv) / moon_r
            maria = 1 - 0.28 * smoothstep(0.2, 1.2, sample_wrap(tex_n, (u + 1) * 0.5, (v + 1) * 0.5, 1.0))
            cover += inside / sub ** 2
            shade += inside * limb * maria / sub ** 2
    norm = (shade.sum() / max(cover.sum(), 1e-9))
    moon_px = shade / norm * moon_lum
    sky[rows, cols] = sky[rows, cols] * (1 - cover[:, None]) + moon_px[:, None] * np.array([1.0, 0.94, 0.88])

    # Tree line: conifer spires with drooping branch whorls, at three
    # distances, rasterised 4x4 supersampled near the horizon.
    top_deg = 30.0
    rows_s = int(math.ceil((90 - top_deg) / 180 * h * ss))
    rows_e = int(math.ceil(92 / 180 * h * ss))
    el_s = 90 - (np.arange(rows_s, rows_e) + 0.5) / (h * ss) * 180
    az_s = ((np.arange(w * ss) + 0.5) / (w * ss) - 0.5) * 360
    occ_s = np.zeros((len(el_s), w * ss), dtype=bool)
    ridge = 11 + 1.5 * fft_noise(4096, SEED + 404, 4, 60, 0.6)[0, :w * ss]
    occ_s |= el_s[:, None] < ridge[None, :]
    deg_per_col = 360 / (w * ss)
    for layer, (count, hmin, hmax) in enumerate([(220, 10, 15), (150, 13, 20), (70, 17, 25)]):
        lr = np.random.default_rng(SEED + 410 + layer)
        for _ in range(count):
            taz, th = lr.uniform(-180, 180), lr.uniform(hmin, hmax)
            tw = lr.uniform(0.15, 0.24)
            whorl = lr.uniform(0.8, 1.6) * th / 18
            phase = lr.uniform(0, 1)
            span = int(th * tw * 1.3 / deg_per_col) + 2
            c0 = int((taz + 180) / deg_per_col)
            cols_k = np.arange(c0 - span, c0 + span + 1) % (w * ss)
            dx = np.abs((az_s[cols_k] - taz + 180) % 360 - 180)
            d = np.clip(th - el_s, 0, None)[:, None]
            tier = ((d / whorl + phase) % 1) ** 0.6
            jag = lr.uniform(0.85, 1.15, (len(el_s), 1))
            half = d * tw * (0.35 + 0.8 * tier) * jag + 0.04
            occ_s[:, cols_k] |= (el_s[:, None] < th) & (dx[None, :] < half)
    occ = np.zeros((h * ss, w * ss))
    occ[rows_s:rows_e] = occ_s
    occ[rows_e:] = 1
    occ = occ.reshape(h, ss, w, ss).mean(axis=(1, 3))
    facing = 0.5 - 0.5 * np.cos(A - ma)
    tree_l = (0.008 + 0.03 * facing ** 1.5)[..., None] * np.array([0.75, 0.95, 1.15])
    tree_l = tree_l * (1 + 0.2 * fft_noise(1024, SEED + 405, 20, 120, 0.5)[:h, :w, None])
    sky = sky * (1 - occ[..., None]) + tree_l * occ[..., None]

    below = E < 0
    depth = np.clip(-np.degrees(E) / 90, 0, 1)
    edge = 1 - smoothstep(0.08, 0.3, depth)
    floor_lum = np.array([0.30, 0.24, 0.165]) * 0.75
    gnd = floor_lum * (1 - edge[..., None]) + tree_l * edge[..., None]
    gnd = gnd * (1 + 0.12 * fft_noise(1024, SEED + 406, 10, 80, 0.6)[:h, :w, None])
    sky = np.where(below[..., None], gnd, sky)
    sky = np.clip(sky, 0, None).astype(np.float32)

    out = os.path.join(root, "assets", "env", "night_sky.hdr")
    write_float(out, sky)
    return out


def hdr_stats(path):
    _, a = read_image(path)
    a = a[..., :3]
    h, w = a.shape[:2]
    L = a @ np.array([0.2126, 0.7152, 0.0722])
    elev = (0.5 - (np.arange(h) + 0.5) / h) * np.pi
    om = np.cos(elev)[:, None] * np.ones((1, w))
    up = np.clip(np.sin(elev), 0, None)[:, None]
    dom = om * (np.pi / h) * (2 * np.pi / w)
    return dict(mean=float(L.mean()), solid_angle_mean=float((L * om).sum() / om.sum()),
                max=float(L.max()), median=float(np.median(L)),
                upper_no_moon=float(L[: h // 2][L[: h // 2] < 5].mean()),
                lower=float(L[h // 2:].mean()),
                irradiance_up=float((L * dom * up).sum()),
                mean_rgb=[round(float(x), 4) for x in a.reshape(-1, 3).mean(0)])


def tile_preview(src, out, tiles=3, size=1536):
    _, a = read_image(src)
    if a.shape[2] == 1:
        a = np.repeat(a, 3, axis=2)
    big = np.tile(a[..., :3], (tiles, tiles, 1))
    step = big.shape[0] / size
    idx = (np.arange(size) * step + step / 2).astype(int)
    # Box-average to keep the downscaled view honest about high-frequency content.
    k = max(int(step), 1)
    acc = np.zeros((size, size, 3))
    for oy in range(k):
        for ox in range(k):
            acc += big[np.clip(idx - k // 2 + oy, 0, big.shape[0] - 1)][:, np.clip(idx - k // 2 + ox, 0, big.shape[1] - 1)]
    write_jpeg(out, acc / k ** 2, 92)


def aim(ob, target):
    from mathutils import Vector
    direction = Vector(target) - ob.location
    ob.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def lit_preview(root, kind, previews):
    reset_blender()
    scene = bpy.data.scenes.new(kind)
    tex = os.path.join(root, "assets", "textures")
    prefix = os.path.join(tex, "forest_floor" if kind != "bark" else "bark")
    mat = bpy.data.materials.new(kind)
    mat.use_nodes = True
    nt = mat.node_tree
    nodes = nt.nodes
    bsdf = nodes["Principled BSDF"]
    uv = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")
    nt.links.new(uv.outputs["UV"], mapping.inputs["Vector"])
    if kind == "bark":
        mapping.inputs["Scale"].default_value = (2, 6, 1)

    def img(name, colorspace):
        node = nodes.new("ShaderNodeTexImage")
        node.image = bpy.data.images.load(prefix + name)
        node.image.colorspace_settings.name = colorspace
        nt.links.new(mapping.outputs["Vector"], node.inputs["Vector"])
        return node

    nt.links.new(img("_Diffuse.jpg", "sRGB").outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(img("_Rough.jpg", "Non-Color").outputs["Color"], bsdf.inputs["Roughness"])
    nm = nodes.new("ShaderNodeNormalMap")
    nm.inputs["Strength"].default_value = 1.4 if kind != "bark" else 1.0
    nt.links.new(img("_nor_gl.jpg", "Non-Color").outputs["Color"], nm.inputs["Color"])
    nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])

    world = bpy.data.worlds.new("w")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    cam_data = bpy.data.cameras.new("cam")
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam

    def add_mesh(name, verts, faces, uvs):
        me = bpy.data.meshes.new(name)
        me.from_pydata(verts, [], faces)
        uvl = me.uv_layers.new(name="UVMap")
        uvl.data.foreach_set("uv", np.asarray(uvs, dtype=np.float32).ravel())
        me.shade_smooth()
        me.materials.append(mat)
        ob = bpy.data.objects.new(name, me)
        scene.collection.objects.link(ob)
        return ob

    if kind == "floor_top":
        # Same raking sun and framing as floor_geometry_raking.png.
        T = FLOOR_TILE_M
        add_mesh("plane", [(0, 0, 0), (T, 0, 0), (T, T, 0), (0, T, 0)], [(0, 1, 2, 3)],
                 [(0, 0), (1, 0), (1, 1), (0, 1)])
        sun = bpy.data.lights.new("sun", "SUN")
        sun.energy = 4.0
        sun.angle = math.radians(1)
        so = bpy.data.objects.new("sun", sun)
        so.rotation_euler = (0, math.radians(-72), 0)
        scene.collection.objects.link(so)
        bg.inputs["Color"].default_value = (0.3, 0.35, 0.5, 1)
        bg.inputs["Strength"].default_value = 0.15
        bsdf.inputs["Specular IOR Level"].default_value = 0.0
        cam_data.type = "ORTHO"
        cam_data.ortho_scale = 1.2
        cam.location = (1.6, 1.6, 10)
        setup_cycles(scene, 64, 768, 768)
    elif kind == "floor":
        # Mimics the game: 13 m camera at 55 degrees down, 40 degree vertical
        # FOV, a low warm light near the player and dim blue moonlight.
        T = FLOOR_TILE_M
        s = 3.5 * T
        add_mesh("plane", [(-s, -s, 0), (s, -s, 0), (s, s, 0), (-s, s, 0)], [(0, 1, 2, 3)],
                 [(-3.5, -3.5), (3.5, -3.5), (3.5, 3.5), (-3.5, 3.5)])
        spot = bpy.data.lights.new("spot", "SPOT")
        spot.energy = 1400
        spot.color = (1.0, 0.6, 0.28)
        spot.spot_size = math.radians(80)
        spot.spot_blend = 0.8
        spot.shadow_soft_size = 0.05
        sp = bpy.data.objects.new("spot", spot)
        sp.location = (0, -1.0, 1.2)
        aim(sp, (0.3, 2.5, 0))
        scene.collection.objects.link(sp)
        moon = bpy.data.lights.new("moon", "SUN")
        moon.energy = 3.0
        moon.color = (0.56, 0.7, 1.0)
        mo = bpy.data.objects.new("moon", moon)
        mo.location = (5.2, 5.8, 6.2)
        aim(mo, (0, 0, 0))
        scene.collection.objects.link(mo)
        bg.inputs["Color"].default_value = (0.2, 0.28, 0.5, 1)
        bg.inputs["Strength"].default_value = 0.12
        cam_data.sensor_fit = "VERTICAL"
        cam_data.angle = math.radians(40)
        pitch = math.radians(55)
        cam.location = (0, -13 * math.cos(pitch), 13 * math.sin(pitch))
        aim(cam, (0, 0, 0))
        setup_cycles(scene, 128, 1280, 720)
        scene.cycles.use_denoising = True
        scene.view_settings.view_transform = "AgX"
        scene.view_settings.exposure = 1.0
    else:
        k, segs, r, hgt = 48, 1, 0.3, 4.0
        verts, faces, uvs = [], [], []
        for j in range(2):
            for i in range(k + 1):
                a = 2 * math.pi * i / k
                verts.append((r * math.cos(a), r * math.sin(a), j * hgt))
        for i in range(k):
            faces.append((i, i + 1, k + 1 + i + 1, k + 1 + i))
        loop_uv = []
        for i in range(k):
            loop_uv += [(i / k, 0), ((i + 1) / k, 0), ((i + 1) / k, 1), (i / k, 1)]
        add_mesh("trunk", verts, faces, loop_uv)
        spot = bpy.data.lights.new("spot", "SPOT")
        spot.energy = 1200
        spot.color = (1.0, 0.62, 0.3)
        spot.spot_size = math.radians(60)
        spot.shadow_soft_size = 0.05
        sp = bpy.data.objects.new("spot", spot)
        sp.location = (-2.5, -2.2, 1.2)
        aim(sp, (0, 0, 1.8))
        scene.collection.objects.link(sp)
        bg.inputs["Color"].default_value = (0.25, 0.35, 0.6, 1)
        bg.inputs["Strength"].default_value = 0.2
        cam_data.lens = 50
        cam.location = (0, -3.2, 1.6)
        cam.rotation_euler = (math.radians(90), 0, 0)
        setup_cycles(scene, 96, 800, 1000)
        scene.cycles.use_denoising = True
    set_output(scene, os.path.join(previews, f"lit_{kind}.png"), "png")
    bpy.ops.render.render(write_still=True, scene=scene.name)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not argv:
        raise SystemExit(__doc__)
    root = os.path.abspath(argv[0])
    only = {"floor", "bark", "sky"}
    previews = None
    if "--only" in argv:
        only = set(argv[argv.index("--only") + 1].split(","))
    if "--previews" in argv:
        previews = os.path.abspath(argv[argv.index("--previews") + 1])
        os.makedirs(previews, exist_ok=True)
    tex = os.path.join(root, "assets", "textures")

    if "floor" in only:
        with tempfile.TemporaryDirectory() as tmp:
            exr = render_floor(previews or tmp, previews)
            finish_floor(exr, root)
    if "bark" in only:
        make_bark(root)
    if "sky" in only:
        out = make_sky(root)
        print("SKY new", hdr_stats(out))
        old = os.path.join(root, "assets", "env", "moonlit_night.hdr")
        if os.path.exists(old):
            print("SKY old", hdr_stats(old))
    if previews:
        for name in [n for n in ("forest_floor", "bark") if ("floor" if n == "forest_floor" else n) in only]:
            for m in ("Diffuse", "nor_gl", "Rough"):
                tile_preview(os.path.join(tex, f"{name}_{m}.jpg"),
                             os.path.join(previews, f"tile3x3_{name}_{m}.jpg"))
        if "floor" in only:
            lit_preview(root, "floor", previews)
            lit_preview(root, "floor_top", previews)
        if "bark" in only:
            lit_preview(root, "bark", previews)
        if "sky" in only:
            _, a = read_image(os.path.join(root, "assets", "env", "night_sky.hdr"))
            write_jpeg(os.path.join(previews, "night_sky.jpg"), linear_to_srgb(a[..., :3] * 1.6))
    for name in ("forest_floor", "bark"):
        p = os.path.join(tex, f"{name}_Diffuse.jpg")
        if os.path.exists(p) and (("floor" if name == "forest_floor" else name) in only):
            _, a = read_image(p)
            print("MEAN", name, a.reshape(-1, a.shape[2]).mean(0))


main()
