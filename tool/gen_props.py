"""Generate the environment prop models (rocks, boulder, stumps, logs, branches, ferns) from code.

Usage: blender -b -P tool/gen_props.py -- <project_root> [prop ...]

Props: rocks_a rocks_b boulder stump_a stump_b log_a log_b branches fern (default: all).
Each prop is built as a seeded high-poly mesh, reduced to a budgeted low-poly, UV-unwrapped,
baked in Cycles (base colour, OpenGL tangent-space normal, roughness) and written to
<project_root>/assets/models/<prop>.glb. Ferns are alpha-masked cards whose frond texture
is drawn procedurally instead of baked. Everything is deterministic.
"""
import math
import os
import sys
import tempfile

import bmesh
import bpy
import numpy as np

TAU = 2 * math.pi


class Noise:
    GRAD = np.array([[1, 1, 0], [-1, 1, 0], [1, -1, 0], [-1, -1, 0], [1, 0, 1], [-1, 0, 1],
                     [1, 0, -1], [-1, 0, -1], [0, 1, 1], [0, -1, 1], [0, 1, -1], [0, -1, -1]], float)

    def __init__(self, seed):
        rng = np.random.default_rng(seed)
        self.perm = np.concatenate([rng.permutation(256)] * 2)
        self.jit = rng.random((256, 3))
        self.off = rng.uniform(-50, 50, 3)

    def _hash(self, x, y, z):
        p = self.perm
        return p[p[p[x & 255] + (y & 255)] + (z & 255)]

    def perlin(self, pts):
        pts = np.asarray(pts, float) + self.off
        i = np.floor(pts).astype(np.int64)
        f = pts - i
        u = f * f * f * (f * (f * 6 - 15) + 10)
        res = 0.0
        for dx in (0, 1):
            for dy in (0, 1):
                for dz in (0, 1):
                    g = self.GRAD[self._hash(i[..., 0] + dx, i[..., 1] + dy, i[..., 2] + dz) % 12]
                    d = f - np.array([dx, dy, dz])
                    w = ((u[..., 0] if dx else 1 - u[..., 0]) * (u[..., 1] if dy else 1 - u[..., 1])
                         * (u[..., 2] if dz else 1 - u[..., 2]))
                    res = res + w * np.sum(g * d, axis=-1)
        return res

    def fbm(self, pts, octaves=4, gain=0.5, lac=2.03):
        pts = np.asarray(pts, float)
        total, amp, norm = 0.0, 1.0, 0.0
        for o in range(octaves):
            total = total + amp * self.perlin(pts * lac ** o + o * 17.3)
            norm += amp
            amp *= gain
        return total / norm * 1.6

    def ridged(self, pts, octaves=3):
        pts = np.asarray(pts, float)
        total, amp, norm = 0.0, 1.0, 0.0
        for o in range(octaves):
            total = total + amp * (1 - np.abs(self.perlin(pts * 2.1 ** o + o * 7.1)) * 1.6)
            norm += amp
            amp *= 0.5
        return total / norm

    def worley(self, pts):
        pts = np.asarray(pts, float) + self.off
        c = np.floor(pts).astype(np.int64)
        f1 = np.full(pts.shape[:-1], 9.0)
        f2 = np.full(pts.shape[:-1], 9.0)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    cc = c + np.array([dx, dy, dz])
                    fp = cc + self.jit[self._hash(cc[..., 0], cc[..., 1], cc[..., 2])]
                    d = np.linalg.norm(fp - pts, axis=-1)
                    f2 = np.where(d < f1, f1, np.minimum(f2, d))
                    f1 = np.minimum(f1, d)
        return f1, f2


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def smin(a, b, k):
    h = np.clip(0.5 + 0.5 * (b - a) / k, 0, 1)
    return b + (a - b) * h - k * h * (1 - h)


def strip_tris(ia, ib):
    """Triangles between two equal-length closed rings of vertex indices."""
    ia1, ib1 = np.roll(ia, -1), np.roll(ib, -1)
    t = np.concatenate([np.stack([ia, ia1, ib1], 1), np.stack([ia, ib1, ib], 1)])
    keep = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
    return t[keep]


def grid_tris(idx, wrap=True):
    rows = []
    for r in range(idx.shape[0] - 1):
        a, b = idx[r], idx[r + 1]
        if wrap:
            rows.append(strip_tris(a, b))
        else:
            rows.append(np.concatenate([np.stack([a[:-1], a[1:], b[1:]], 1),
                                        np.stack([a[:-1], b[1:], b[:-1]], 1)]))
    return np.concatenate(rows)


def vertex_normals(V, F):
    fn = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    vn = np.zeros_like(V)
    for k in range(3):
        np.add.at(vn, F[:, k], fn)
    return vn / np.maximum(np.linalg.norm(vn, axis=1, keepdims=True), 1e-12)


def make_object(name, V, F, attrs=None, uv=None, recalc=True):
    """uv is per vertex (N, 2) or per face corner (len(F), 3, 2)."""
    V = np.asarray(V, np.float32)
    F = np.asarray(F, np.int32)
    ok = (F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 0] != F[:, 2])
    if uv is not None:
        uv = np.asarray(uv, np.float32)
        uv = uv[ok] if uv.ndim == 3 else uv[F[ok]]
    F = F[ok]
    me = bpy.data.meshes.new(name)
    me.vertices.add(len(V))
    me.vertices.foreach_set("co", V.ravel())
    me.loops.add(F.size)
    me.loops.foreach_set("vertex_index", F.ravel())
    me.polygons.add(len(F))
    me.polygons.foreach_set("loop_start", np.arange(0, F.size, 3, dtype=np.int32))
    me.update(calc_edges=True)
    me.validate()
    for key, arr in (attrs or {}).items():
        arr = np.asarray(arr, np.float32)
        if arr.ndim == 1:
            me.attributes.new(key, "FLOAT", "POINT").data.foreach_set("value", arr)
        else:
            me.attributes.new(key, "FLOAT_VECTOR", "POINT").data.foreach_set("vector", arr.ravel())
    if uv is not None:
        me.uv_layers.new(name="UVMap").data.foreach_set("uv", uv.ravel())
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    if recalc:
        bm = bmesh.new()
        bm.from_mesh(me)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(me)
        bm.free()
    me.shade_smooth()
    return obj


def tri_count(obj):
    return sum(len(p.vertices) - 2 for p in obj.data.polygons)


def _sock(coll, key):
    if isinstance(key, int):
        return coll[key]
    for s in coll:
        if s.identifier == key:
            return s
    for s in coll:
        if s.name == key and getattr(s, "enabled", True):
            return s
    return coll[key]


class SB:
    def __init__(self, name):
        self.mat = bpy.data.materials.new(name)
        self.nt = self.mat.node_tree
        self.nt.nodes.clear()
        self.out = None

    def node(self, kind, ins=None, **props):
        nd = self.nt.nodes.new(kind)
        for k, v in props.items():
            setattr(nd, k, v)
        for k, v in (ins or {}).items():
            sock = _sock(nd.inputs, k)
            if isinstance(v, bpy.types.NodeSocket):
                self.nt.links.new(v, sock)
            else:
                sock.default_value = v
        return nd

    def coord(self, offset=True):
        p = self.node("ShaderNodeTexCoord").outputs["Object"]
        if offset:
            rnd = self.node("ShaderNodeObjectInfo").outputs["Random"]
            p = self.vadd(p, self.math("MULTIPLY", rnd, 97.0))
        return p

    def vadd(self, a, b):
        return self.node("ShaderNodeVectorMath", {0: a, 1: b}, operation="ADD").outputs[0]

    def vmul(self, a, b):
        return self.node("ShaderNodeVectorMath", {0: a, 1: b}, operation="MULTIPLY").outputs[0]

    def noise(self, vec, scale, detail=3.0, rough=0.5, dist=0.0, out="Fac"):
        return self.node("ShaderNodeTexNoise", {"Vector": vec, "Scale": scale, "Detail": detail,
                                                "Roughness": rough, "Distortion": dist}).outputs[out]

    def voronoi(self, vec, scale, out="Distance", feature="F1"):
        return self.node("ShaderNodeTexVoronoi", {"Vector": vec, "Scale": scale},
                         feature=feature).outputs[out]

    def math(self, op, a, b=0.0, clamp=False):
        return self.node("ShaderNodeMath", {0: a, 1: b}, operation=op, use_clamp=clamp).outputs[0]

    def mr(self, x, a, b, c=0.0, d=1.0):
        return self.node("ShaderNodeMapRange", {"Value": x, "From Min": a, "From Max": b,
                                                "To Min": c, "To Max": d},
                         interpolation_type="SMOOTHSTEP").outputs[0]

    def mix(self, f, a, b):
        return self.node("ShaderNodeMix", {"Factor_Float": f, "A_Color": a, "B_Color": b},
                         data_type="RGBA").outputs[2]

    def mixf(self, f, a, b):
        return self.node("ShaderNodeMix", {"Factor_Float": f, "A_Float": a, "B_Float": b},
                         data_type="FLOAT").outputs[0]

    def comp(self, v, axis):
        return self.node("ShaderNodeSeparateXYZ", {0: v}).outputs[axis]

    def attr(self, name, out="Fac"):
        return self.node("ShaderNodeAttribute", attribute_name=name).outputs[out]

    def normal_z(self):
        return self.comp(self.node("ShaderNodeNewGeometry").outputs["Normal"], "Z")

    def ao_shade(self, col, dist, floor=0.45):
        ao = self.node("ShaderNodeAmbientOcclusion", {"Distance": dist}, only_local=True,
                       samples=16).outputs["AO"]
        return self.vmul(col, self.mr(ao, 0.0, 1.0, floor, 1.0))

    def finish(self, color, rough, height, strength, dist):
        self.color, self.rough = color, rough
        self.normal = self.node("ShaderNodeBump", {"Strength": strength, "Distance": dist,
                                                   "Height": height}).outputs["Normal"]
        self.emit = self.node("ShaderNodeEmission", {"Strength": 1.0})
        self.bsdf = self.node("ShaderNodeBsdfPrincipled", {"Normal": self.normal})
        self.output = self.node("ShaderNodeOutputMaterial")
        return self

    def mode(self, m):
        if m == "normal":
            self.nt.links.new(self.bsdf.outputs[0], self.output.inputs["Surface"])
            return
        self.nt.links.new(self.color if m == "color" else self.rough, self.emit.inputs["Color"])
        self.nt.links.new(self.emit.outputs[0], self.output.inputs["Surface"])


def rgba(r, g, b):
    return (r, g, b, 1.0)


def moss_layer(s, p, up, amount):
    """Moss on upward faces, broken into patches; returns mask, colour and its fine noise."""
    patch = s.noise(p, 1.7, 5, 0.6)
    mfine = s.noise(p, 45, 4, 0.7)
    patch = s.math("ADD", patch, s.math("MULTIPLY", s.math("SUBTRACT", mfine, 0.5), 0.25))
    mmask = s.math("MULTIPLY", s.mr(up, 0.4, 0.75), s.mr(patch, 0.47, 0.56, 0.0, amount))
    tone = s.noise(p, 6.0, 3)
    mcol = s.mix(s.mr(s.math("ADD", tone, s.math("MULTIPLY", mfine, 0.5)), 0.5, 0.9),
                 rgba(0.016, 0.026, 0.008), rgba(0.042, 0.058, 0.016))
    mcol = s.mix(s.math("MULTIPLY", s.mr(tone, 0.6, 0.7), 0.5), mcol, rgba(0.05, 0.045, 0.015))
    return mmask, mcol, mfine


def rock_shader(name, moss=1.0, lichen=1.0):
    s = SB(name)
    p = s.coord()
    nz = s.normal_z()
    big = s.noise(p, 1.3, 4, 0.55)
    col = s.mix(s.mr(big, 0.3, 0.7), rgba(0.15, 0.145, 0.135), rgba(0.25, 0.24, 0.22))
    warm = s.noise(p, 0.6, 2)
    col = s.mix(s.math("MULTIPLY", s.mr(warm, 0.45, 0.7), 0.5), col, rgba(0.26, 0.21, 0.16))
    spk = s.comp(s.voronoi(p, 160, "Color"), "X")
    col = s.mix(s.math("MULTIPLY", s.mr(spk, 0.25, 0.15), 0.55), col, rgba(0.04, 0.04, 0.04))
    col = s.mix(s.math("MULTIPLY", s.mr(spk, 0.82, 0.9), 0.4), col, rgba(0.45, 0.43, 0.40))
    streak = s.noise(s.vmul(p, (5.0, 5.0, 0.7)), 1.0, 3)
    col = s.mix(s.math("MULTIPLY", s.mr(streak, 0.55, 0.75), 0.3), col, rgba(0.10, 0.09, 0.075))
    ln = s.noise(p, 9.0, 4, 0.7)
    lmask = s.math("MULTIPLY", s.mr(ln, 0.6, 0.63), s.mr(nz, -0.4, 0.2, 0.0, lichen), clamp=True)
    lcol = s.mix(s.mr(s.noise(p, 2.0), 0.55, 0.62), rgba(0.33, 0.35, 0.27), rgba(0.40, 0.30, 0.10))
    col = s.mix(lmask, col, lcol)
    col = s.mix(s.math("MULTIPLY", s.attr("cav"), 0.6), col, rgba(0.05, 0.045, 0.04))
    mmask, mcol, mfine = moss_layer(s, p, s.comp(s.attr("bn", "Vector"), "Z"), moss)
    col = s.mix(mmask, col, mcol)
    col = s.ao_shade(col, 0.3)
    rough = s.mixf(mmask, s.mixf(lmask, s.mr(big, 0.2, 0.8, 0.72, 0.86), 0.9), 0.97)
    grain = s.noise(p, 70, 5, 0.6)
    height = s.mixf(mmask, s.math("ADD", grain, s.math("MULTIPLY", lmask, 0.6)), s.math("MULTIPLY", mfine, 2.5))
    return s.finish(col, rough, height, 0.35, 0.004)


def wood_shader(name, bark_dark, bark_light, wood_a, wood_b, weather=0.4, ring_freq=70.0,
                moss=1.0, moss_bias=0.0, rot=0.0, lichen=0.6, cap_moss=1.0):
    s = SB(name)
    p = s.coord()
    lp = s.attr("lp", "Vector")
    wood, cav, cap, rad = s.attr("wood"), s.attr("cav"), s.attr("cap"), s.attr("rad")
    nz = s.normal_z()
    bn = s.noise(s.vmul(lp, (7.0, 7.0, 1.8)), 1.0, 4, 0.6)
    bark = s.mix(s.mr(bn, 0.3, 0.7), rgba(*bark_dark), rgba(*bark_light))
    bark = s.mix(s.math("MULTIPLY", cav, 0.8), bark, rgba(0.03, 0.022, 0.016))
    ln = s.noise(p, 9.0, 3, 0.7)
    lm = s.math("MULTIPLY", s.mr(ln, 0.62, 0.66), lichen)
    bark = s.mix(s.math("MULTIPLY", lm, s.math("SUBTRACT", 1.0, cav)), bark, rgba(0.30, 0.32, 0.25))
    gn = s.noise(s.vmul(lp, (60.0, 60.0, 1.5)), 1.0, 3, 0.6)
    w = s.mix(s.mr(gn, 0.3, 0.7), rgba(*wood_a), rgba(*wood_b))
    rw = s.math("ADD", rad, s.math("MULTIPLY", s.math("SUBTRACT", s.noise(lp, 5.0, 2), 0.5), 0.012))
    ring = s.mr(s.math("SINE", s.math("MULTIPLY", rw, TAU * ring_freq)), 0.55, 1.0)
    w = s.mix(s.math("MULTIPLY", ring, s.math("MULTIPLY", cap, 0.45)), w,
              rgba(wood_a[0] * 0.55, wood_a[1] * 0.5, wood_a[2] * 0.45))
    wn = s.noise(p, 3.0, 3)
    w = s.mix(s.math("MULTIPLY", s.mr(wn, 0.3, 0.7, 0.6, 1.2), weather, clamp=True), w,
              rgba(0.17, 0.165, 0.155))
    if rot > 0:
        rotm = s.math("MULTIPLY", s.mr(rad, 0.16, 0.05), s.math("MULTIPLY", cap, rot))
        w = s.mix(rotm, w, rgba(0.07, 0.045, 0.025))
    w = s.mix(s.math("MULTIPLY", cav, 0.85), w, rgba(0.035, 0.028, 0.02))
    col = s.mix(wood, bark, w)
    mmask, mcol, mfine = moss_layer(s, p, s.math("ADD", nz, moss_bias), moss)
    mmask = s.math("MULTIPLY", mmask, s.math("SUBTRACT", 1.0, s.math("MULTIPLY", cap, 1.0 - cap_moss)))
    col = s.mix(mmask, col, mcol)
    col = s.ao_shade(col, 0.2, 0.4)
    rough = s.mixf(mmask, s.mixf(wood, s.mr(bn, 0.2, 0.8, 0.78, 0.9), 0.74), 0.97)
    gfine = s.noise(s.vmul(lp, (300.0, 300.0, 8.0)), 1.0, 3, 0.6)
    height = s.mixf(mmask, s.mixf(wood, bn, s.math("ADD", gfine, s.math("MULTIPLY", ring, s.math("MULTIPLY", cap, 0.3)))),
                    s.math("MULTIPLY", mfine, 2.0))
    return s.finish(col, rough, height, 0.4, 0.003)


def icosphere(subdiv):
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=subdiv, radius=1.0)
    V = np.array([v.co[:] for v in bm.verts])
    F = np.array([[v.index for v in f.verts] for f in bm.faces])
    bm.free()
    return V, F


def rock_high(seed, size, n_planes, edge_k, sub=7, crack=1.0):
    """Ellipsoid cut by random planes (smooth-min in radius), then noise-displaced."""
    rng = np.random.default_rng(seed)
    nz = Noise(seed)
    U, F = icosphere(sub)
    half = np.array(size) / 2
    a = half * np.array([1.15, 1.15, 1.25])
    r = 1 / np.sqrt(np.sum((U / a) ** 2, axis=1))
    for i in range(n_planes):
        while True:
            n = rng.normal(size=3)
            n /= np.linalg.norm(n)
            if n[2] > -0.25:
                break
        re = 1 / np.sqrt(np.sum((n / a) ** 2))
        d = re * rng.uniform(0.62, 0.86)
        dot = U @ n
        rp = np.where(dot > 1e-3, d / np.maximum(dot, 1e-3), 1e3)
        r = smin(r, rp, edge_k * rng.uniform(0.6, 1.4) * half.mean())
    dz = -U[:, 2]
    bottom = np.where(dz > 1e-3, a[2] * 0.62 / np.maximum(dz, 1e-3), 1e3)
    r = smin(r, bottom, 0.12 * half.mean())
    warp = np.stack([nz.fbm(U * 0.9 + k * 3.1, 3) for k in range(3)], 1)
    V = U * r[:, None]
    V += warp * half.mean() * 0.07
    lo, hi = V.min(0), V.max(0)
    V = (V - (lo + hi) / 2) * (np.array(size) / (hi - lo))
    N = vertex_normals(V, F)
    s = half.mean()
    base_n = N.copy()
    disp = s * 0.022 * nz.fbm(V / s * 1.6, 4)
    f1, f2 = nz.worley(V / s * 1.3 + 5.0)
    cr = 1 - smoothstep(0.0, 0.05, f2 - f1)
    cr *= smoothstep(0.05, 0.35, nz.perlin(V / s * 1.1 + 9)) * crack
    disp -= cr * s * 0.018
    disp += 0.004 * nz.fbm(V * 9, 3)
    V = V + N * disp[:, None]
    H = V[:, 2].max() - V[:, 2].min()
    V[:, 2] -= V[:, 2].min() + 0.15 * H
    return V, F, {"cav": cr, "bn": base_n}


def row_params(rfun, s_lo, s_hi, th, n_s, warp):
    """Row positions along the axis; with warp they are evenly spaced in profile arc length."""
    if not warp:
        return np.linspace(0, 1, n_s)
    tf = np.linspace(0, 1, 1500)
    sf = s_lo.mean() + (s_hi.mean() - s_lo.mean()) * tf
    rf = rfun(sf[:, None] + 0 * th[None, :], th[None, :] + 0 * sf[:, None]).mean(1)
    arc = np.concatenate([[0], np.cumsum(np.hypot(np.diff(rf), np.diff(sf)))])
    return np.interp(np.linspace(0, arc[-1], n_s), arc, tf)


def weld(V, F, UV):
    """Merge coincident vertices; UVs become per corner so seams stay seams."""
    key = np.round(V, 6)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    return V[first], inv.ravel()[F], UV[F]


def orient_pieces(V, F, pieces, outward):
    """Flip each piece (slice of F) whose area-weighted normal disagrees with outward(centroids)."""
    for sl in pieces:
        f = F[sl]
        n = np.cross(V[f[:, 1]] - V[f[:, 0]], V[f[:, 2]] - V[f[:, 0]])
        c = V[f].mean(1)
        if np.sum(n * outward(c, sl)) < 0:
            F[sl] = f[:, ::-1]
    return F


def trunk_low(rfun, cap_lo, cap_hi, n_s, n_th, n_rho, warp=False, strips=1, sectors=1, lo_cap=True):
    """Low-poly counterpart of trunk(): no fine relief, explicit UVs in metres, welded."""
    th = np.linspace(0, TAU, n_th + 1)
    s_lo, s_hi = cap_lo(np.ones_like(th), th), cap_hi(np.ones_like(th), th)
    t = row_params(rfun, s_lo[:-1], s_hi[:-1], th[:-1], n_s, warp)
    S = s_lo[None, :] + (s_hi - s_lo)[None, :] * t[:, None]
    TH = np.broadcast_to(th, S.shape)
    R = rfun(S, TH)
    R[:, -1] = R[:, 0]
    P = np.stack([R * np.cos(TH), R * np.sin(TH), S], -1)
    v = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=-1).mean(1))])
    seg = np.linalg.norm(np.diff(P, axis=1), axis=-1)
    u = np.concatenate([np.zeros((n_s, 1)), np.cumsum(seg, 1)], 1)
    u -= u[:, -1:] / 2
    Vl, UVl, Fl, pieces = [], [], [], []
    base = nf = 0
    cuts = np.linspace(0, n_s - 1, strips + 1).round().astype(int)
    cols = np.linspace(0, n_th, sectors + 1).round().astype(int)
    for a, b in zip(cuts[:-1], cuts[1:]):
        for c, d in zip(cols[:-1], cols[1:]):
            pts = P[a:b + 1, c:d + 1].reshape(-1, 3)
            us = u[a:b + 1, c:d + 1] - (u[a:b + 1, c:c + 1] + u[a:b + 1, d:d + 1]) / 2
            uv = np.stack([us + 10.0 * len(Vl), np.broadcast_to(v[a:b + 1, None], us.shape)], -1).reshape(-1, 2)
            idx = base + np.arange(pts.shape[0]).reshape(b - a + 1, d - c + 1)
            f = grid_tris(idx, wrap=False)
            Vl.append(pts); UVl.append(uv); Fl.append(f)
            pieces.append(("side", slice(nf, nf + len(f))))
            base += pts.shape[0]; nf += len(f)
    rho = np.linspace(0, 1, n_rho + 1)[1:]
    for row, cap, sgn in ((0, cap_lo, -1), (n_s - 1, cap_hi, 1)):
        if sgn < 0 and not lo_cap:
            continue
        RHO = np.broadcast_to(rho[:, None], (n_rho, n_th + 1))
        Sc = cap(RHO, np.broadcast_to(th, RHO.shape))
        Sc[-1] = S[row]
        X, Y = RHO * R[row] * np.cos(th), RHO * R[row] * np.sin(th)
        pts = np.concatenate([[[0, 0, cap(np.zeros(1), np.zeros(1))[0]]],
                              np.stack([X, Y, Sc], -1).reshape(-1, 3)])
        uv = pts[:, :2] * np.array([1, sgn])
        idx = base + 1 + np.arange(n_rho * (n_th + 1)).reshape(n_rho, n_th + 1)
        idx = np.vstack([np.full((1, n_th + 1), base), idx])
        f = grid_tris(idx, wrap=False)
        Vl.append(pts); UVl.append(uv); Fl.append(f)
        pieces.append((sgn, slice(nf, nf + len(f))))
        base += pts.shape[0]; nf += len(f)
    V, F, UV = np.concatenate(Vl), np.concatenate(Fl), np.concatenate(UVl)
    kinds = {sl.start: k for k, sl in pieces}

    def outward(c, sl):
        k = kinds[sl.start]
        if k == "side":
            return np.stack([c[:, 0], c[:, 1], np.zeros(len(c))], 1)
        return np.tile([0, 0, float(k)], (len(c), 1))

    F = orient_pieces(V, F, [sl for _, sl in pieces], outward)
    return weld(V, F, UV)


def trunk(nz, n_s, n_th, n_rho, rfun, cap_lo, cap_hi, side_fn, cap_fn, warp=False):
    """Tube around local +Z with per-angle end heights and polar end caps.

    rfun(S, TH) -> radius; cap_x(RHO, TH) -> axial s of the end surface;
    side_fn(S, TH, X, Y) -> (dR, wood, cav); cap_fn(RHO, TH, X, Y, sgn) -> (ds, wood, cav).
    """
    th = np.linspace(0, TAU, n_th, endpoint=False)
    s_lo, s_hi = cap_lo(np.ones_like(th), th), cap_hi(np.ones_like(th), th)
    t = row_params(rfun, s_lo, s_hi, th, n_s, warp)
    S = s_lo[None, :] + (s_hi - s_lo)[None, :] * t[:, None]
    TH = np.broadcast_to(th, S.shape)
    R = rfun(S, TH)
    X0, Y0 = R * np.cos(TH), R * np.sin(TH)
    dR, wood, cav = side_fn(S, TH, X0, Y0)
    R = R + dR
    verts = [np.stack([R * np.cos(TH), R * np.sin(TH), S], -1).reshape(-1, 3)]
    woods, cavs, caps = [wood.ravel()], [cav.ravel()], [np.zeros(S.size)]
    idx_side = np.arange(S.size).reshape(S.shape)
    tris = [grid_tris(idx_side)]
    base = S.size
    rho = np.linspace(0, 1, n_rho + 1)[1:-1]
    for row, cap, sgn in ((0, cap_lo, -1), (n_s - 1, cap_hi, 1)):
        RHO = np.broadcast_to(rho[:, None], (len(rho), n_th))
        THc = np.broadcast_to(th, RHO.shape)
        Rend = R[row][None, :]
        Xc, Yc = RHO * Rend * np.cos(THc), RHO * Rend * np.sin(THc)
        Sc = cap(RHO, THc)
        ds, cw, cc = cap_fn(RHO, THc, Xc, Yc, sgn)
        bark_t = 0.012
        ring_w = np.clip((Rend - bark_t - RHO * Rend) / 0.004 + 0.5, 0, 1)
        cw = cw * ring_w
        Sc = Sc + sgn * ds
        centre = np.array([[0.0, 0.0, cap(np.zeros(1), np.zeros(1))[0]]])
        pts = np.stack([Xc, Yc, Sc], -1).reshape(-1, 3)
        verts += [centre, pts]
        woods += [np.ones(1), cw.ravel()]
        cavs += [np.zeros(1), cc.ravel()]
        caps += [np.ones(1), np.ones(pts.shape[0])]
        ic = base
        iring = base + 1 + np.arange(pts.shape[0]).reshape(RHO.shape)
        idx = np.vstack([np.full((1, n_th), ic), iring, idx_side[row][None, :]])
        tris.append(grid_tris(idx))
        base += 1 + pts.shape[0]
    V = np.concatenate(verts)
    F = np.concatenate(tris)
    attrs = {"wood": np.concatenate(woods), "cav": np.clip(np.concatenate(cavs), 0, 1),
             "cap": np.concatenate(caps), "rad": np.hypot(V[:, 0], V[:, 1]), "lp": V.copy()}
    caps_arr = attrs["cap"]
    attrs["cap"][(caps_arr > 0) & (attrs["wood"] < 0.5)] = 0.0
    return V, F, attrs


def bark_side(nz, bark_depth, plate, stretch, bark_mask_fn, crack_scale=14.0):
    def fn(S, TH, X, Y):
        q = np.stack([X, Y, S / stretch], -1) * plate
        f1, f2 = nz.worley(q)
        fiss = 1 - smoothstep(0.0, 0.22, f2 - f1)
        var = 0.75 + 0.25 * nz.perlin(q * 0.7 + 3)
        d_bark = bark_depth * ((1 - fiss) * var - 1) + 0.002 * nz.fbm(q * 4, 2)
        m = bark_mask_fn(S, TH, X, Y)
        cq = np.stack([X * crack_scale, Y * crack_scale, S * crack_scale / 10], -1)
        c1, c2 = nz.worley(cq + 11)
        crack = (1 - smoothstep(0.0, 0.06, c2 - c1)) * smoothstep(0.0, 0.3, nz.perlin(cq * 0.3))
        d_wood = -0.012 - 0.006 * crack + 0.0012 * nz.perlin(np.stack([X * 90, Y * 90, S * 3], -1))
        dR = m * d_bark + (1 - m) * d_wood
        return dR, 1 - m, m * fiss + (1 - m) * crack
    return fn


def end_fn(nz, crack=1.0):
    def fn(RHO, TH, X, Y, sgn):
        a = np.arctan2(Y, X)
        c = nz.perlin(np.stack([np.cos(a) * 3, np.sin(a) * 3, RHO * 0.4 + sgn], -1))
        chk = (1 - smoothstep(0.0, 0.05, np.abs(c))) * smoothstep(0.35, 0.6, RHO) * crack
        return -0.008 * chk, np.ones_like(RHO), chk
    return fn


def splinter_cap(nz, s0, amp, tilt, phase, sgn, hollow=0.0, fine=0.04, seed_off=0.0, teeth=0.0):
    """Broken end surface: rough fracture inside, shards concentrated at the rim."""
    def fn(RHO, TH):
        X, Y = RHO * np.cos(TH), RHO * np.sin(TH)
        rim = smoothstep(0.5, 1.0, RHO)
        lobe = np.maximum(0, np.cos(TH - phase)) ** 2
        rid = nz.ridged(np.stack([np.cos(TH) * 4 + seed_off, np.sin(TH) * 4, np.full_like(RHO, seed_off)], -1), 2)
        shards = rim * (amp * lobe * (0.5 + 0.7 * rid) + teeth * np.clip(rid - 0.2, 0, 1))
        rough = fine * nz.fbm(np.stack([X * 4 + seed_off, Y * 4, np.full_like(RHO, 3.0)], -1), 4)
        s = tilt * RHO * np.cos(TH - phase - 1.2) + shards + rough - hollow * np.exp(-(RHO / 0.45) ** 2)
        return s0 + sgn * s
    return fn


def decimate(high, name, budget):
    low_me = high.data.copy()
    low = bpy.data.objects.new(name, low_me)
    bpy.context.scene.collection.objects.link(low)
    for a in [a.name for a in low_me.attributes if a.name in ("wood", "cav", "cap", "rad", "lp", "bn")]:
        low_me.attributes.remove(low_me.attributes[a])
    mod = low.modifiers.new("dec", "DECIMATE")
    mod.ratio = budget / tri_count(high) * 0.97
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(low.evaluated_get(dg))
    low.modifiers.clear()
    low.data = me
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.triangulate(bm, faces=bm.faces)
    bm.to_mesh(me)
    bm.free()
    me.shade_smooth()
    return low


def drop_buried(low, below):
    bm = bmesh.new()
    bm.from_mesh(low.data)
    dead = [f for f in bm.faces if max(v.co.z for v in f.verts) < below]
    bmesh.ops.delete(bm, geom=dead, context="FACES")
    bm.to_mesh(low.data)
    bm.free()


def smart_uv(objs, margin=0.006):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(60), island_margin=margin, area_weight=0.0)
    bpy.ops.uv.pack_islands(rotate=True, margin=margin)
    bpy.ops.object.mode_set(mode="OBJECT")


def pack_uv(objs, margin=0.006):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.select_all(action="SELECT")
    bpy.ops.uv.pack_islands(rotate=True, rotate_method="CARDINAL", margin=margin)
    bpy.ops.object.mode_set(mode="OBJECT")


def setup_cycles():
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        prefs.compute_device_type = "METAL"
        prefs.get_devices()
        for d in prefs.devices:
            d.use = True
        sc.cycles.device = "GPU"
    except Exception as e:
        print("GPU unavailable, baking on CPU:", e)
    sc.cycles.seed = 7
    sc.cycles.use_denoising = False


def bake_set(prop, pairs, res, extrusion, max_dist, tmp):
    """Bake each (high, low, shader) pair into one shared texture set; returns export material."""
    imgs = {}
    for kind, data in (("color", False), ("rough", True), ("normal", True)):
        img = bpy.data.images.new(f"{prop}_{kind}", res, res, alpha=False)
        img.generated_color = {"color": (0.2, 0.2, 0.2, 1), "rough": (0.85, 0.85, 0.85, 1),
                               "normal": (0.5, 0.5, 1.0, 1)}[kind]
        if data:
            img.colorspace_settings.name = "Non-Color"
        imgs[kind] = img
    mat = bpy.data.materials.new(prop)
    mat.use_backface_culling = True
    nt = mat.node_tree
    nt.nodes.clear()
    tex = {}
    for kind, img in imgs.items():
        nd = nt.nodes.new("ShaderNodeTexImage")
        nd.image = img
        tex[kind] = nd
    for _, low, _ in pairs:
        low.data.materials.clear()
        low.data.materials.append(mat)
    sc = bpy.context.scene
    for kind, btype, samples in (("color", "EMIT", 24), ("rough", "EMIT", 4), ("normal", "NORMAL", 8)):
        sc.cycles.samples = samples
        nt.nodes.active = tex[kind]
        for high, low, shader in pairs:
            shader.mode(kind)
            bpy.ops.object.select_all(action="DESELECT")
            high.select_set(True)
            low.select_set(True)
            bpy.context.view_layer.objects.active = low
            bpy.ops.object.bake(type=btype, use_selected_to_active=True, cage_extrusion=extrusion,
                                max_ray_distance=max_dist, margin=4, margin_type="EXTEND",
                                normal_space="TANGENT", use_clear=False, target="IMAGE_TEXTURES")
    for kind, img in imgs.items():
        path = os.path.join(tmp, f"{prop}_{kind}.png")
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
        img.pack()
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
    nt.links.new(tex["color"].outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(tex["rough"].outputs["Color"], bsdf.inputs["Roughness"])
    nm = nt.nodes.new("ShaderNodeNormalMap")
    nt.links.new(tex["normal"].outputs["Color"], nm.inputs["Color"])
    nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    bsdf.inputs["Metallic"].default_value = 0.0
    return mat


def export(root, prop, lows, image_format="JPEG"):
    for o in list(bpy.context.scene.objects):
        if o not in lows:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.object.select_all(action="DESELECT")
    for o in lows:
        o.select_set(True)
    path = os.path.join(root, "assets", "models", prop + ".glb")
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True,
                              export_yup=True, export_apply=True, export_tangents=True,
                              export_image_format=image_format, export_jpeg_quality=88,
                              export_materials="EXPORT", export_animations=False)
    print(f"PROP {prop}: " + ", ".join(f"{o.name}={tri_count(o)}" for o in lows))


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    setup_cycles()


ROCK_SPECS = {
    "rocks_a": [
        dict(seed=11, size=(1.4, 1.1, 0.85), n_planes=7, edge_k=0.10),
        dict(seed=12, size=(0.9, 0.75, 0.55), n_planes=9, edge_k=0.05),
        dict(seed=13, size=(1.25, 0.9, 0.5), n_planes=5, edge_k=0.08),
    ],
    "rocks_b": [
        dict(seed=21, size=(1.5, 1.2, 1.0), n_planes=8, edge_k=0.07),
        dict(seed=22, size=(0.72, 0.62, 0.45), n_planes=6, edge_k=0.12),
        dict(seed=23, size=(1.1, 1.3, 0.72), n_planes=10, edge_k=0.05),
    ],
}


def build_rocks(root, prop, tmp):
    reset()
    pairs, lows = [], []
    for i, spec in enumerate(ROCK_SPECS[prop], 1):
        V, F, attrs = rock_high(**spec)
        high = make_object(f"{prop}_hi{i}", V, F, attrs)
        sh = rock_shader(f"{prop}_hi{i}")
        high.data.materials.append(sh.mat)
        low = decimate(high, f"rock_{i}", 2400 + 60)
        drop_buried(low, min(v.co.z for v in low.data.vertices) + 0.01)
        pairs.append((high, low, sh))
        lows.append(low)
    smart_uv(lows)
    bake_set(prop, pairs, 512, 0.03, 0.08, tmp)
    export(root, prop, lows)


def build_boulder(root, prop, tmp):
    reset()
    V, F, attrs = rock_high(seed=31, size=(1.3, 1.8, 1.05), n_planes=11, edge_k=0.07, sub=8)
    V[:, 2] -= V[:, 2].min() + 0.1
    high = make_object("boulder_hi", V, F, attrs)
    sh = rock_shader("boulder_hi", moss=1.0)
    high.data.materials.append(sh.mat)
    low = decimate(high, "boulder", 6000 + 100)
    drop_buried(low, min(v.co.z for v in low.data.vertices) + 0.01)
    smart_uv([low])
    bake_set(prop, [(high, low, sh)], 1024, 0.04, 0.1, tmp)
    export(root, prop, [low])


def build_stump(root, prop, tmp, seed, broken):
    reset()
    nz = Noise(seed)
    rng = np.random.default_rng(seed)
    R0, Rg, H, lam = 0.25, 0.68, 0.5 if not broken else 0.4, 0.13
    n_lobes = rng.integers(5, 8)
    lob_th = (np.arange(n_lobes) + rng.uniform(-0.3, 0.3, n_lobes)) * TAU / n_lobes
    lob_s = rng.uniform(0.55, 1.0, n_lobes)

    def rfun(S, TH):
        thw = TH + 0.18 * nz.perlin(np.stack([S * 3, np.cos(TH), np.sin(TH)], -1))
        width = 0.16 + 0.55 * np.clip(S / 0.45, 0, 1)
        lob = 0.0
        for th_i, s_i in zip(lob_th, lob_s):
            d = np.angle(np.exp(1j * (thw - th_i)))
            lob = np.maximum(lob, s_i * np.exp(-(d / width) ** 2))
        flare = np.where(S > 0, np.exp(-np.maximum(S, 0) / lam), 1 - S * 1.2)
        r = R0 * (1 + 0.05 * nz.fbm(np.stack([np.cos(TH) * 1.5, np.sin(TH) * 1.5, S * 2], -1), 3))
        return r + (Rg - R0) * flare * (0.22 + 0.78 * lob)

    def bark_mask(S, TH, X, Y):
        v = nz.fbm(np.stack([X * 3, Y * 3, S * 2], -1), 3)
        if not broken:
            return 1 - smoothstep(-0.02, 0.02, v - 0.55)
        return 1 - smoothstep(-0.02, 0.02, v - 0.7 + 0.9 * np.clip(S - 0.2, 0, 1))

    if broken:
        cap_hi = splinter_cap(nz, H, 0.13, 0.05, rng.uniform(0, TAU), 1, hollow=0.07, fine=0.035, teeth=0.09)
    else:
        phase = rng.uniform(0, TAU)

        def cap_hi(RHO, TH):
            step = 0.014 * smoothstep(-0.04, 0.04, RHO * np.cos(TH - phase) - 0.1)
            return H + 0.035 * RHO * np.cos(TH - phase) + step - 0.02 * smoothstep(0.82, 1.0, RHO) ** 2

    def cap_lo(RHO, TH):
        return np.full_like(RHO, -0.12)

    V, F, attrs = trunk(nz, 170, 420, 70, rfun, cap_lo, cap_hi,
                        bark_side(nz, 0.014, 26.0, 4.0, bark_mask), end_fn(nz, 1.0 if not broken else 0.4),
                        warp=True)
    high = make_object(prop + "_hi", V, F, attrs)
    if broken:
        sh = wood_shader(prop + "_hi", (0.045, 0.032, 0.024), (0.11, 0.085, 0.065),
                         (0.30, 0.19, 0.10), (0.22, 0.14, 0.075), weather=0.25, ring_freq=55,
                         moss=0.8, moss_bias=0.0, rot=1.0, cap_moss=0.1)
    else:
        sh = wood_shader(prop + "_hi", (0.045, 0.032, 0.024), (0.11, 0.085, 0.065),
                         (0.30, 0.22, 0.13), (0.24, 0.17, 0.10), weather=0.55, ring_freq=60,
                         moss=0.8, moss_bias=0.0, cap_moss=0.25)
    high.data.materials.append(sh.mat)
    Vl, Fl, UVl = trunk_low(rfun, cap_lo, cap_hi, 17, 84, 5, warp=True, sectors=4, lo_cap=False)
    low = make_object(prop, Vl, Fl, uv=UVl, recalc=False)
    low.data.set_sharp_from_angle(angle=math.radians(70))
    pack_uv([low])
    bake_set(prop, [(high, low, sh)], 512, 0.05, 0.15, tmp)
    export(root, prop, [low])


def log_parts(nz, L, R0, taper, bark_keep, phase_b, knots):
    def rfun(S, TH):
        oval = 0.04 * np.cos(2 * (TH - 0.7))
        r = R0 * (1 - taper * (S / L + 0.5)) * (1 + oval + 0.06 * nz.fbm(
            np.stack([np.cos(TH) * 1.2, np.sin(TH) * 1.2, S * 1.5], -1), 3))
        for ks, kth, ka in knots:
            d = np.angle(np.exp(1j * (TH - kth)))
            r = r + ka * R0 * np.exp(-((S - ks) / (0.6 * R0)) ** 2 - (d / 0.5) ** 2)
        return r

    def bark_mask(S, TH, X, Y):
        v = nz.fbm(np.stack([X * 2.5, Y * 2.5, S * 0.9], -1), 4) + bark_keep
        return smoothstep(-0.03, 0.03, v)

    cap_lo = splinter_cap(nz, -L / 2, 0.16, 0.1, phase_b, -1, fine=0.04, seed_off=3.0)
    cap_hi = splinter_cap(nz, L / 2, 0.22, 0.08, phase_b + 2.0, 1, fine=0.04, seed_off=7.0)
    return rfun, bark_mask, cap_lo, cap_hi


def to_log_frame(V):
    return np.stack([V[:, 2], V[:, 0], V[:, 1]], 1)


def orient(V, d, base):
    d = np.asarray(d, float)
    d /= np.linalg.norm(d)
    x = np.cross(d, [0, 0, 1.0]) if abs(d[2]) < 0.95 else np.cross(d, [1.0, 0, 0])
    x /= np.linalg.norm(x)
    y = np.cross(d, x)
    return base + V[:, :1] * x + V[:, 1:2] * y + V[:, 2:3] * d


def concat_parts(parts):
    off = 0
    Vs, Fs, rest = [], [], {}
    for Vp, Fp, Ap in parts:
        Vs.append(Vp)
        Fs.append(Fp + off)
        off += len(Vp)
        if isinstance(Ap, dict):
            for k, v in Ap.items():
                rest.setdefault(k, []).append(v)
        else:
            rest.setdefault("uv", []).append(Ap)
    return np.concatenate(Vs), np.concatenate(Fs), {k: np.concatenate(v) for k, v in rest.items()}


def build_log(root, prop, tmp, seed, L, R0, stubs, knots, low_res):
    reset()
    nz = Noise(seed)
    rng = np.random.default_rng(seed)
    rfun, bmask, cap_lo, cap_hi = log_parts(nz, L, R0, 0.25, -0.1, rng.uniform(0, TAU), knots)
    side = bark_side(nz, 0.01, 30.0, 4.0, bmask)
    V, F, A = trunk(nz, int(L / 0.006), int(TAU * R0 / 0.006), 40, rfun, cap_lo, cap_hi, side, end_fn(nz, 0.6))
    n_s, n_th = low_res
    Vl, Fl, UVl = trunk_low(rfun, cap_lo, cap_hi, n_s, n_th, 4, strips=3)
    bend_ph = rng.uniform(0, 3)

    def to_world(P):
        P = to_log_frame(P)
        P[:, 1] += 0.05 * np.sin(P[:, 0] / L * 2.6 + bend_ph)
        P[:, 2] += 0.02 * np.sin(P[:, 0] / L * 3.0)
        return P

    hi_parts, lo_parts = [(to_world(V), F, A)], [(to_world(Vl), Fl, UVl)]
    for k, (sx, d, length, rs) in enumerate(stubs):
        nzs = Noise(seed * 10 + k)
        rr = lambda S, TH, rs=rs, nzs=nzs, length=length: rs * (1 - 0.35 * S / length) * (
            1 + 0.06 * nzs.fbm(np.stack([np.cos(TH), np.sin(TH), S * 3], -1), 2))
        bm = lambda S, TH, X, Y, nzs=nzs: smoothstep(-0.03, 0.03, nzs.fbm(np.stack([X * 6, Y * 6, S * 3], -1), 3) - 0.1)
        lo = lambda RHO, TH: np.zeros_like(RHO)
        hi = splinter_cap(nzs, length, 0.05, 0.03, rng.uniform(0, TAU), 1, fine=0.01, teeth=0.02)
        Vs, Fs, As = trunk(nzs, int(length / 0.006), int(TAU * rs / 0.006), 16, rr, lo, hi,
                           bark_side(nzs, 0.006, 40.0, 3.0, bm), end_fn(nzs, 0.5))
        Vsl, Fsl, UVsl = trunk_low(rr, lo, hi, 7, 10, 2, lo_cap=False)
        base = to_world(np.array([[0.0, 0.0, sx]]))[0]
        hi_parts.append((orient(Vs, d, base), Fs, As))
        lo_parts.append((orient(Vsl, d, base), Fsl, UVsl))
    V, F, A = concat_parts(hi_parts)
    Vl, Fl, rest = concat_parts(lo_parts)
    dz = V[:, 2].min() + 0.02
    V[:, 2] -= dz
    Vl[:, 2] -= dz
    high = make_object(prop + "_hi", V, F, A)
    sh = wood_shader(prop + "_hi", (0.045, 0.034, 0.026), (0.11, 0.09, 0.07),
                     (0.16, 0.14, 0.115), (0.12, 0.105, 0.085), weather=0.6, ring_freq=45,
                     moss=0.8, moss_bias=0.0, lichen=0.8, cap_moss=0.3)
    high.data.materials.append(sh.mat)
    low = make_object(prop, Vl, Fl, uv=rest["uv"], recalc=False)
    low.data.set_sharp_from_angle(angle=math.radians(75))
    pack_uv([low])
    bake_set(prop, [(high, low, sh)], 512, 0.06, 0.2, tmp)
    export(root, prop, [low])


def sweep(pts, radii, n_th, nz=None, bark=0.0, cap_base=True, uv_scale=1.0):
    """Tube along a polyline; returns V, F, per-vertex UV (metres), lp."""
    pts = np.asarray(pts, float)
    T = np.gradient(pts, axis=0)
    T /= np.linalg.norm(T, axis=1, keepdims=True)
    N = np.zeros_like(T)
    ref = np.array([0, 0, 1.0]) if abs(T[0, 2]) < 0.9 else np.array([1.0, 0, 0])
    n = np.cross(T[0], ref)
    n /= np.linalg.norm(n)
    for i in range(len(T)):
        n = n - T[i] * np.dot(n, T[i])
        n /= np.linalg.norm(n)
        N[i] = n
    B = np.cross(T, N)
    th = np.linspace(0, TAU, n_th + 1)
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    s = np.concatenate([[0], np.cumsum(seg)])
    C, Sn = np.cos(th)[None, :], np.sin(th)[None, :]
    R = np.broadcast_to(radii[:, None], (len(pts), n_th + 1)).copy()
    LPx, LPy = R * C, R * Sn
    LPs = np.broadcast_to(s[:, None], R.shape)
    if nz is not None and bark > 0:
        q = np.stack([LPx * 60, LPy * 60, LPs * 6], -1)
        R = R * (1 + bark * nz.fbm(q, 3) + 0.5 * bark * nz.perlin(np.stack([LPx, LPy, LPs * 30], -1)))
        R[:, -1] = R[:, 0]
    P = pts[:, None, :] + R[..., None] * (C[..., None] * N[:, None, :] + Sn[..., None] * B[:, None, :])
    V = P.reshape(-1, 3)
    rmean = radii.mean()
    UV = np.stack([np.broadcast_to(th / TAU * TAU * rmean, R.shape), LPs], -1).reshape(-1, 2) * uv_scale
    LP = np.stack([LPx, LPy, LPs], -1).reshape(-1, 3)
    idx = np.arange(V.shape[0]).reshape(R.shape)
    F = grid_tris(idx, wrap=False)
    Vl, UVl, LPl, Fl = [V], [UV], [LP], [F]
    base = V.shape[0]
    tip = pts[-1] + T[-1] * radii[-1] * 0.8
    Vl.append(tip[None])
    UVl.append(np.array([[TAU * rmean * 0.5, s[-1] + radii[-1]]]))
    LPl.append(np.array([[0, 0, s[-1]]]))
    Fl.append(np.stack([idx[-1, :-1], idx[-1, 1:], np.full(n_th, base)], 1))
    base += 1
    if cap_base:
        Vl.append(pts[0][None] - T[0] * radii[0] * 0.2)
        UVl.append(np.array([[-1.0, -1.0]]))
        ring_uv = np.stack([-1 + np.cos(th) * radii[0], -1 + np.sin(th) * radii[0]], 1)
        Vl.append(V[idx[0]])
        UVl.append(ring_uv)
        LPl += [np.array([[0, 0, 0.0]]), LP[idx[0]]]
        ring = base + 1 + np.arange(n_th + 1)
        Fl.append(np.stack([ring[1:], ring[:-1], np.full(n_th, base)], 1))
    return np.concatenate(Vl), np.concatenate(Fl), np.concatenate(UVl), np.concatenate(LPl)


def branch_skeleton(seed):
    rng = np.random.default_rng(seed)
    nz = Noise(seed)
    L = 1.28
    n = 90
    s = np.linspace(0, 1, n)
    y = -L / 2 + s * L
    x = 0.035 * nz.fbm(np.stack([s * 2, np.zeros(n), np.zeros(n) + seed], -1), 2)
    r_main = 0.022 * (1 - s) ** 0.8 + 0.004
    z = r_main + 0.03 * smoothstep(0.3, 1.0, s) * (0.5 + 0.5 * nz.perlin(np.stack([s * 3, np.ones(n), np.zeros(n)], -1)))
    main = (np.stack([x, y, z], 1), r_main, 1)
    limbs = [main]
    k = rng.integers(4, 7)
    ts = np.sort(rng.uniform(0.18, 0.92, k))
    side = rng.choice([-1, 1])
    for t in ts:
        side = -side
        i = int(t * (n - 1))
        p0 = main[0][i]
        r0 = main[1][i] * rng.uniform(0.45, 0.7)
        length = rng.uniform(0.18, 0.42) * (1.2 - 0.6 * t)
        ang = math.radians(rng.uniform(25, 45))
        elev = math.radians(rng.uniform(-5, 35))
        d = np.array([side * math.sin(ang) * math.cos(elev), math.cos(ang) * math.cos(elev), math.sin(elev)])
        limbs += twig(rng, p0, d, length, r0, depth=1)
    pts = np.concatenate([l[0] for l in limbs])
    w = pts[:, 0].max() - pts[:, 0].min()
    sx = min(1.0, 0.33 / w)
    out = []
    for p, r, lvl in limbs:
        p = p.copy()
        p[:, 0] *= sx
        p[:, 2] = np.maximum(p[:, 2], r * 0.9)
        p[:, 2] = np.minimum(p[:, 2], 0.33)
        out.append((p, r, lvl))
    return out


def twig(rng, p0, d, length, r0, depth):
    m = max(6, int(length / 0.02))
    pts = [p0]
    dirv = d / np.linalg.norm(d)
    for i in range(m):
        dirv = dirv + rng.normal(0, 0.12, 3) + np.array([0, 0, -0.04])
        dirv /= np.linalg.norm(dirv)
        pts.append(pts[-1] + dirv * length / m)
    pts = np.array(pts)
    r = r0 * np.linspace(1, 0.3, m + 1)
    out = [(pts, r, 2 if depth == 1 else 3)]
    if depth == 1:
        for _ in range(rng.integers(1, 3)):
            j = rng.integers(m // 3, m - 1)
            dd = dirv + rng.normal(0, 0.6, 3) * np.array([1, 0.3, 0.5])
            out += twig(rng, pts[j], dd, length * rng.uniform(0.3, 0.55), r[j] * 0.7, depth=2)
    return out


def build_branches(root, prop, tmp):
    reset()
    pairs, lows = [], []
    for b in range(3):
        limbs = branch_skeleton(100 + b)
        nz = Noise(200 + b)
        hv, hf, hlp, lv, lf, luv = [], [], [], [], [], []
        ho = lo = 0
        for li, (p, r, lvl) in enumerate(limbs):
            V, F, UV, LP = sweep(p, r, 14 if lvl == 1 else 8, nz, bark=0.08, cap_base=(lvl == 1))
            hv.append(V); hf.append(F + ho); hlp.append(LP); ho += len(V)
            step = {1: 6, 2: 3, 3: 2}[lvl]
            keep = np.unique(np.concatenate([np.arange(0, len(p), step), [len(p) - 1]]))
            rl = r[keep] * 1.08
            V2, F2, UV2, _ = sweep(p[keep], rl, {1: 6, 2: 4, 3: 3}[lvl], cap_base=(lvl == 1))
            lv.append(V2); lf.append(F2 + lo); luv.append(UV2); lo += len(V2)
        HV = np.concatenate(hv)
        attrs = {"lp": np.concatenate(hlp), "wood": smoothstep(-0.05, 0.05, nz.fbm(HV * np.array([8, 3, 8]), 3) - 0.25),
                 "cav": np.zeros(len(HV)), "cap": np.zeros(len(HV)), "rad": np.zeros(len(HV))}
        high = make_object(f"branch_hi{b}", HV, np.concatenate(hf), attrs)
        sh = wood_shader(f"branch_hi{b}", (0.07, 0.055, 0.042), (0.15, 0.12, 0.09),
                         (0.32, 0.29, 0.24), (0.25, 0.22, 0.18), weather=0.7, moss=0.0, lichen=0.9)
        high.data.materials.append(sh.mat)
        low = make_object(f"branch_{b + 1}", np.concatenate(lv), np.concatenate(lf), uv=np.concatenate(luv))
        pairs.append((high, low, sh))
        lows.append(low)
    pack_uv(lows, 0.004)
    bake_set(prop, pairs, 512, 0.008, 0.02, tmp)
    export(root, prop, lows)


FERN_SLOTS = 3
FERN_TOP = 1012


def draw_fronds(res=1024):
    """Bipinnate frond designs side by side: slot 0 lady fern, slot 1 bracken-like, slot 2 dried.

    Returns sRGB colour, alpha and height arrays with row 0 at v = 0.
    """
    rgb = np.zeros((res, res, 3))
    alpha = np.zeros((res, res))
    height = np.zeros((res, res))
    yy, xx = np.mgrid[0:res, 0:res].astype(float)
    rng = np.random.default_rng(5)

    def stamp(cx, cy, a, b, ang, col, h=1.0):
        rad = int(max(a, b)) + 2
        x0, x1 = max(0, int(cx) - rad), min(res, int(cx) + rad + 1)
        y0, y1 = max(0, int(cy) - rad), min(res, int(cy) + rad + 1)
        if x0 >= x1 or y0 >= y1:
            return
        dx, dy = xx[y0:y1, x0:x1] - cx, yy[y0:y1, x0:x1] - cy
        c, s_ = math.cos(ang), math.sin(ang)
        u, v = (dx * c + dy * s_) / a, (-dx * s_ + dy * c) / b
        q = np.sqrt(u * u + v * v)
        cov = np.clip((1 - q) * min(a, b) + 0.5, 0, 1)
        sl = (slice(y0, y1), slice(x0, x1))
        rgb[sl] = rgb[sl] * (1 - cov[..., None]) + np.array(col) * cov[..., None]
        alpha[sl] = np.maximum(alpha[sl], cov)
        hh = h * np.sqrt(np.clip(1 - q * q, 0, 1))
        height[sl] = np.where(cov > 0.5, np.maximum(height[sl], hh), height[sl])

    def polyline(pts, w0, w1, col, h):
        pts = np.asarray(pts)
        seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
        s = np.concatenate([[0], np.cumsum(seg)])
        for d in np.arange(0, s[-1], 0.8):
            p = np.array([np.interp(d, s, pts[:, 0]), np.interp(d, s, pts[:, 1])])
            w = w0 + (w1 - w0) * d / max(s[-1], 1e-6)
            stamp(p[0], p[1], w, w, 0, col, h)

    slot_w = res / FERN_SLOTS
    for slot in range(FERN_SLOTS):
        dry = slot == 2
        cx0 = slot_w * (slot + 0.5)
        half_w = slot_w / 2 - 6
        y0, y1 = 10, FERN_TOP
        if slot == 1:
            n_pairs, bare = 13, 0.1
            env_fn = lambda t: ((t + 0.05) ** 0.4 * (1 - t) ** 1.3) / (0.29 ** 0.4 * 0.76 ** 1.3)
            green = np.array([0.33, 0.44, 0.15])
        else:
            n_pairs, bare = 17, 0.06
            env_fn = lambda t: (t ** 0.75 * (1 - t) ** 1.05) / (0.417 ** 0.75 * 0.583 ** 1.05)
            green = np.array([0.28, 0.40, 0.15])
        tan = np.array([0.56, 0.44, 0.25])
        rib = np.array([0.44, 0.52, 0.25]) if not dry else np.array([0.52, 0.40, 0.23])
        wob = rng.uniform(-6, 6)
        rach = lambda t: np.array([cx0 + wob * math.sin(t * 3.0), y0 + (y1 - y0) * t])
        for k in range(n_pairs):
            for side in (-1, 1):
                t = bare + (0.97 - bare) * (k + (0.45 if side > 0 else 0)) / n_pairs
                Lp = half_w * min(env_fn(t), 1.0) * rng.uniform(0.86, 1.05)
                if dry and rng.random() < 0.2:
                    continue
                base = rach(t)
                ang = math.radians(74 - 30 * t) * rng.uniform(0.93, 1.07)
                dirv = np.array([side * math.sin(ang), math.cos(ang)])
                tipward = np.array([0.0, 1.0])
                col_p = green * rng.uniform(0.9, 1.1, 3) if not dry else tan * rng.uniform(0.85, 1.1, 3)
                if dry and rng.random() < 0.4:
                    col_p = np.array([0.40, 0.28, 0.15]) * rng.uniform(0.85, 1.1)
                costa = lambda u: base + Lp * (u * dirv + 0.18 * u * u * tipward)
                if Lp < 14:
                    mid = costa(0.5)
                    stamp(mid[0], mid[1], Lp * 0.55 + 2, Lp * 0.22 + 2,
                          math.atan2(dirv[1] + 0.1, dirv[0]), col_p, 1.0)
                    continue
                spacing = 8.5
                m = max(3, int(Lp / spacing))
                width = 0.2 if slot == 1 else 0.17
                for j in range(m):
                    u = (j + 0.6) / (m + 0.4)
                    p = costa(u)
                    d = costa(min(u + 0.02, 1.0)) - costa(max(u - 0.02, 0.0))
                    d /= np.linalg.norm(d)
                    pl = Lp * width * (1 - u) ** 0.8 + 2.0
                    for ps in (-1, 1):
                        nrm = np.array([-d[1], d[0]]) * ps
                        lobe = nrm * 0.82 + d * 0.57
                        lobe /= np.linalg.norm(lobe)
                        c = p + lobe * pl * 0.55
                        col = col_p * rng.uniform(0.93, 1.07) * (1.05 if ps * side > 0 else 0.96)
                        stamp(c[0], c[1], pl * 0.6 + 0.8, spacing * 0.45, math.atan2(lobe[1], lobe[0]), col, 1.0)
                tip = costa(1.0)
                stamp(tip[0], tip[1], Lp * 0.06 + 2, Lp * 0.035 + 1.5, math.atan2(dirv[1] + 0.2, dirv[0]), col_p, 1.0)
                polyline([costa(u) for u in np.linspace(0, 0.95, 12)], 1.1, 0.5, col_p * 0.4 + rib * 0.6, 1.4)
        polyline([rach(t) for t in np.linspace(0, 1, 50)], 3.4, 0.8, rib * (0.85 if not dry else 1.0), 2.2)
    bgs = []
    for slot in range(FERN_SLOTS):
        sl = (slice(None), slice(int(slot * slot_w), int((slot + 1) * slot_w)))
        a = alpha[sl] > 0.5
        bgs.append((sl, rgb[sl][a].mean(0)))
    out = rgb / np.maximum(alpha[..., None], 1e-3)
    for sl, bg in bgs:
        out[sl] = np.where(alpha[sl][..., None] > 0.01, out[sl], bg)
    return np.clip(out, 0, 1), alpha, height


def height_to_normal(h, strength):
    gy, gx = np.gradient(h)
    n = np.stack([-gx * strength, -gy * strength, np.ones_like(h)], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    return n * 0.5 + 0.5


def save_image(name, arr, path, fmt, data):
    """Write arr (rows bottom-up) to path, then load it back packed so the exporter keeps its format."""
    res = arr.shape[0]
    rgba_ = np.ones((res, res, 4))
    rgba_[..., :arr.shape[2]] = arr
    img = bpy.data.images.new(name, res, res, alpha=arr.shape[2] == 4)
    img.pixels.foreach_set(rgba_.astype(np.float32).ravel())
    img.filepath_raw = path
    img.file_format = fmt
    sc = bpy.context.scene
    sc.render.image_settings.quality = 88
    img.save(quality=88) if fmt == "JPEG" else img.save()
    bpy.data.images.remove(img)
    img = bpy.data.images.load(path)
    img.name = name
    if data:
        img.colorspace_settings.name = "Non-Color"
    img.pack()
    return img


def fern_mesh(seed):
    """Arching frond cards; returns V, F (wound so the front faces the frond's upper side), UV, N."""
    rng = np.random.default_rng(seed)
    n_fr = (13, 11, 14)[seed % 3]
    seg, across = 11, np.array([-1.0, -0.5, 0.0, 0.5, 1.0])
    na = len(across)
    slot_w = 1.0 / FERN_SLOTS
    n_young = int(rng.integers(2, 4))
    n_dry = int(rng.integers(1, 3))
    kinds = ["young"] * n_young + ["dry"] * n_dry + ["mature"] * (n_fr - n_young - n_dry)
    V, F, UV, UPS = [], [], [], []
    az0 = rng.uniform(0, TAU)
    for i, kind in enumerate(kinds):
        az = az0 + TAU * (i * 0.618034 % 1.0) + rng.uniform(-0.35, 0.35)
        if kind == "young":
            length = rng.uniform(0.26, 0.36)
            e0, e1 = math.radians(rng.uniform(64, 74)), math.radians(rng.uniform(15, 35))
            slot, vmax, droop = int(rng.integers(0, 2)), 1.0, 0.15
        elif kind == "dry":
            length = rng.uniform(0.42, 0.55)
            e0, e1 = math.radians(rng.uniform(18, 32)), math.radians(rng.uniform(-60, -45))
            slot, vmax, droop = 2, rng.choice([1.0, rng.uniform(0.55, 0.75)]), 0.5
        else:
            length = rng.uniform(0.44, 0.62)
            e0, e1 = math.radians(rng.uniform(40, 60)), math.radians(rng.uniform(-55, -30))
            slot, vmax, droop = int(rng.integers(0, 2)), 1.0, rng.uniform(0.3, 0.5)
        flip = rng.random() < 0.5
        sweep_ = rng.uniform(-0.35, 0.35)
        twist = rng.uniform(-0.3, 0.3)
        # Cards are 1.3x wider than the texture aspect: broader fronds read as fern, not palm.
        hw = 1.3 * 0.5 * (1024 / FERN_SLOTS) / 1000 * length
        p = np.array([0.0, 0.0, 0.01])
        base = len(V)
        for k in range(seg + 1):
            t = k / seg * vmax
            e = e0 + (e1 - e0) * t ** 1.4
            a_k = az + sweep_ * t * t
            fw = np.array([math.cos(a_k), math.sin(a_k), 0.0])
            side_v = np.array([-math.sin(a_k), math.cos(a_k), 0.0])
            if k:
                p = p + (fw * math.cos(e) + np.array([0, 0, math.sin(e)])) * length * vmax / seg
            up = np.array([0, 0, 1.0]) * math.cos(e) - fw * math.sin(e)
            lat = side_v * math.cos(twist * t) + up * math.sin(twist * t)
            for a in across:
                V.append(p + lat * a * hw - up * droop * hw * abs(a) ** 1.6 * (0.3 + 0.7 * t))
                u = (a + 1) / 2
                UV.append((slot_w * (slot + (1 - u if flip else u)), (10 + (FERN_TOP - 10) * t) / 1024))
                UPS.append(up)
        for k in range(seg):
            for j in range(na - 1):
                a0 = base + k * na + j
                F += [(a0, a0 + 1, a0 + na + 1), (a0, a0 + na + 1, a0 + na)]
    V, F, UV, UPS = np.array(V), np.array(F), np.array(UV), np.array(UPS)
    fn = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    flipf = np.sum(fn * UPS[F].mean(1), 1) < 0
    F[flipf] = F[flipf][:, ::-1]
    V[:, :2] *= 0.48 / np.abs(V[:, :2]).max()
    V[:, 2] = np.maximum(V[:, 2], 0.004)
    V[:, 2] *= 0.45 / V[:, 2].max()
    # Cards are thin and lit from above: lean each normal toward an upward dome so whole fronds
    # shade like curved leaves and still catch overhead moonlight.
    N = vertex_normals(V, F)
    dome = V - np.array([0, 0, -0.3])
    dome /= np.linalg.norm(dome, axis=1, keepdims=True)
    N = 0.3 * N + 0.2 * dome + np.array([0, 0, 0.5])
    N /= np.linalg.norm(N, axis=1, keepdims=True)
    return V, F, UV, N


def build_fern(root, prop, tmp):
    reset()
    rgb, alpha, height = draw_fronds(1024)
    col = save_image("fern_color", np.concatenate([rgb, alpha[..., None]], -1),
                     os.path.join(tmp, "fern_color.png"), "PNG", False)
    nrm = save_image("fern_normal", height_to_normal(height, 1.0),
                     os.path.join(tmp, "fern_normal.jpg"), "JPEG", True)
    s = SB("fern")
    tc = s.node("ShaderNodeTexImage", image=col)
    tn = s.node("ShaderNodeTexImage", image=nrm)
    nm = s.node("ShaderNodeNormalMap", {"Color": tn.outputs["Color"], "Strength": 0.8})
    clip = s.math("ROUND", tc.outputs["Alpha"])
    bsdf = s.node("ShaderNodeBsdfPrincipled", {"Base Color": tc.outputs["Color"], "Roughness": 0.62,
                                               "Normal": nm.outputs["Normal"], "Alpha": clip, "Metallic": 0.0})
    out = s.node("ShaderNodeOutputMaterial")
    s.nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
    s.mat.use_backface_culling = False
    lows = []
    for i in range(3):
        V, F, UV, N = fern_mesh(40 + i)
        o = make_object(f"fern_{i + 1}", V, F, uv=UV, recalc=False)
        o.data.normals_split_custom_set_from_vertices([tuple(n) for n in N])
        o.data.materials.append(s.mat)
        lows.append(o)
    export(root, prop, lows, image_format="AUTO")


BUILDERS = {
    "rocks_a": lambda r, t: build_rocks(r, "rocks_a", t),
    "rocks_b": lambda r, t: build_rocks(r, "rocks_b", t),
    "boulder": lambda r, t: build_boulder(r, "boulder", t),
    "stump_a": lambda r, t: build_stump(r, "stump_a", t, 51, broken=False),
    "stump_b": lambda r, t: build_stump(r, "stump_b", t, 52, broken=True),
    "log_a": lambda r, t: build_log(r, "log_a", t, 61, 2.75, 0.14, [],
                                    [(-0.6, 1.2, 0.25), (0.35, 4.0, 0.2), (0.9, 2.2, 0.18)], (64, 28)),
    "log_b": lambda r, t: build_log(r, "log_b", t, 62, 3.6, 0.23, [
        (0.6, (0.25, 0.1, 1.0), 0.66, 0.085),
        (-0.7, (0.3, 1.0, 0.25), 0.44, 0.075),
        (1.3, (-0.2, -1.0, 0.35), 0.42, 0.065),
        (-1.4, (-0.3, -0.7, 0.8), 0.4, 0.06),
    ], [(0.6, 1.57, 0.25), (-0.7, 0.0, 0.25), (-1.0, 3.5, 0.15)], (64, 28)),
    "branches": lambda r, t: build_branches(r, "branches", t),
    "fern": lambda r, t: build_fern(r, "fern", t),
}


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not argv:
        sys.exit("usage: blender -b -P tool/gen_props.py -- <project_root> [prop ...]")
    root, names = argv[0], argv[1:] or list(BUILDERS)
    with tempfile.TemporaryDirectory() as tmp:
        for name in names:
            BUILDERS[name](root, tmp)


main()
