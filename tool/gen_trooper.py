"""Generate the player character (a night-watch trooper) from code.

Usage: blender -b -P tool/gen_trooper.py -- <project_root>

Builds a continuous, smooth-skinned under-suit body (a union of shaped volumes, voxel-remeshed and
heat-weighted), rigid contoured armour, gloved hands and a carbine on a 22-joint armature, keys the
looping clips `Idle` (3.2 s), `Run` (0.667 s, 4.4 m/s) and `RunBack` (0.733 s, 3.3 m/s backpedal),
all in place, bakes a 1024 px texture set (base colour, OpenGL normal, roughness/metallic, emissive)
from procedural Cycles materials and writes <project_root>/assets/models/trooper.glb.
Blender +Y forward exports as glTF -Z forward. Everything is deterministic.
"""
import math
import os
import shutil
import sys
import tempfile

import bpy
import numpy as np
from mathutils import Matrix, Vector

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
ROOT = os.path.abspath(ARGS[0] if ARGS else ".")
OUT = os.path.join(ROOT, "assets", "models", "trooper.glb")

D2R = math.pi / 180
TAU = 2 * math.pi
FPS = 30
TEX = 1024
BODY_TRIS = 4800

OLIVE, GUNMETAL, PANEL, CLOTH, WEB, GLOVE, STEEL, POLY, VISOR, LENS, BOOT = range(11)


def lin(c):
    out = []
    for v in c:
        v /= 255.0
        out.append(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4)
    return out


MATERIALS = {
    OLIVE: dict(base=(84, 98, 64), wear=(158, 160, 150), rough=(0.6, 0.35), metal=(0.0, 0.8), wear_amt=1.0),
    GUNMETAL: dict(base=(62, 66, 72), wear=(170, 172, 176), rough=(0.48, 0.3), metal=(0.35, 0.9), wear_amt=1.0),
    PANEL: dict(base=(170, 174, 152), wear=(205, 206, 198), rough=(0.55, 0.35), metal=(0.0, 0.7), wear_amt=0.8),
    CLOTH: dict(base=(56, 62, 56), wear=(78, 84, 74), rough=(0.93, 0.9), metal=(0.0, 0.0), wear_amt=0.3,
                cloth=True),
    WEB: dict(base=(66, 76, 58), wear=(96, 104, 86), rough=(0.86, 0.8), metal=(0.0, 0.0), wear_amt=0.4,
              web=True),
    GLOVE: dict(base=(34, 35, 34), wear=(70, 70, 66), rough=(0.7, 0.55), metal=(0.0, 0.0), wear_amt=0.5),
    STEEL: dict(base=(44, 46, 50), wear=(155, 157, 160), rough=(0.42, 0.25), metal=(0.8, 1.0), wear_amt=1.0,
                scratch=True),
    POLY: dict(base=(30, 31, 33), wear=(74, 75, 76), rough=(0.58, 0.45), metal=(0.0, 0.0), wear_amt=0.7,
               scratch=True),
    VISOR: dict(base=(7, 12, 14), wear=(7, 12, 14), rough=(0.04, 0.04), metal=(0.0, 0.0), wear_amt=0.0,
                emit=(0.002, 0.005, 0.006)),
    LENS: dict(base=(255, 246, 222), wear=(255, 246, 222), rough=(0.1, 0.1), metal=(0.0, 0.0), wear_amt=0.0,
               emit=(1.0, 0.92, 0.74)),
    BOOT: dict(base=(28, 28, 27), wear=(62, 60, 56), rough=(0.8, 0.6), metal=(0.0, 0.0), wear_amt=0.6),
}


def Rx(a):
    return Matrix.Rotation(a, 3, "X")


def Ry(a):
    return Matrix.Rotation(a, 3, "Y")


def Rz(a):
    return Matrix.Rotation(a, 3, "Z")


MIRROR = Matrix(((-1, 0, 0), (0, 1, 0), (0, 0, 1)))


def mirror(r):
    return MIRROR @ r @ MIRROR


def smooth(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def np_rot(axis, a):
    c, s = math.cos(a), math.sin(a)
    return {"x": np.array([[1, 0, 0], [0, c, -s], [0, s, c]]),
            "y": np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]),
            "z": np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])}[axis]


def frame_z(axis):
    z = np.asarray(axis, float)
    z = z / np.linalg.norm(z)
    ref = np.array([1.0, 0, 0]) if abs(z[0]) < 0.9 else np.array([0, 1.0, 0])
    y = np.cross(z, ref)
    y /= np.linalg.norm(y)
    x = np.cross(y, z)
    return np.stack([x, y, z], 1)


def unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


# Rest pose: arms in a 40 degree A-pose and feet slightly apart, so the remeshed body keeps the
# limbs separate from the torso and from each other.
_ARM = np.array([math.sin(40 * D2R), 0, -math.cos(40 * D2R)])
SH = np.array([0.185, -0.01, 1.45])
EL = SH + 0.315 * _ARM
WR = EL + 0.265 * _ARM
HT = WR + 0.17 * _ARM
HIP = np.array([0.095, 0.0, 0.94])
KN = np.array([0.11, 0.012, 0.51])
AN = np.array([0.12, 0.0, 0.085])
BALL = np.array([0.12, 0.135, 0.022])
TOE_TIP = np.array([0.12, 0.215, 0.022])


def side_bones(s, tag):
    def m(p):
        return (s * p[0], p[1], p[2])
    return [
        (f"Clavicle_{tag}", "Chest", m((0.03, 0.0, 1.45)), m(SH), (0, 0, 1)),
        (f"UpperArm_{tag}", f"Clavicle_{tag}", m(SH), m(EL), (0, 1, 0)),
        (f"Forearm_{tag}", f"UpperArm_{tag}", m(EL), m(WR), (0, 1, 0)),
        (f"Hand_{tag}", f"Forearm_{tag}", m(WR), m(HT), (s, 0, 0)),
        (f"Thigh_{tag}", "Hips", m(HIP), m(KN), (0, 1, 0)),
        (f"Shin_{tag}", f"Thigh_{tag}", m(KN), m(AN), (0, 1, 0)),
        (f"Foot_{tag}", f"Shin_{tag}", m(AN), m(BALL), (0, 0, 1)),
        (f"Toe_{tag}", f"Foot_{tag}", m(BALL), m(TOE_TIP), (0, 0, 1)),
    ]


BONES = [
    ("Hips", None, (0, 0, 1.0), (0, 0, 1.1), (0, 1, 0)),
    ("Spine", "Hips", (0, 0, 1.1), (0, 0, 1.25), (0, 1, 0)),
    ("Chest", "Spine", (0, 0, 1.25), (0, 0, 1.47), (0, 1, 0)),
    ("Neck", "Chest", (0, 0, 1.49), (0, 0.01, 1.58), (0, 1, 0)),
    ("Head", "Neck", (0, 0.01, 1.58), (0, 0.01, 1.78), (0, 1, 0)),
    *side_bones(-1, "L"),
    *side_bones(1, "R"),
    ("Weapon", "Chest", (0.1, 0.3, 1.15), (0.1, 0.4, 1.15), (0, 0, 1)),
]


def build_armature():
    data = bpy.data.armatures.new("TrooperRig")
    arm = bpy.data.objects.new("Trooper", data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    for name, parent, head, tail, up in BONES:
        eb = data.edit_bones.new(name)
        eb.head, eb.tail = tuple(head), tuple(tail)
        eb.align_roll(Vector(up))
        if parent:
            eb.parent = data.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    for pb in arm.pose.bones:
        pb.rotation_mode = "QUATERNION"
    return arm


def _sp(x, p):
    return np.sign(x) * np.abs(x) ** p


def _ring_faces(nrings, nseg):
    top = 1 + nrings * nseg
    f = [(0, 1 + (j + 1) % nseg, 1 + j) for j in range(nseg)]
    for i in range(nrings - 1):
        for j in range(nseg):
            a, b = 1 + i * nseg + j, 1 + i * nseg + (j + 1) % nseg
            f.append((a, b, b + nseg, a + nseg))
    base = 1 + (nrings - 1) * nseg
    f += [(base + j, base + (j + 1) % nseg, top) for j in range(nseg)]
    return f


def superq(c, r, e=(0.5, 0.5), nu=8, nv=12, R=None, post=None):
    lat = np.linspace(-np.pi / 2, np.pi / 2, nu + 1)[1:-1]
    lon = np.linspace(-np.pi, np.pi, nv, endpoint=False)
    la, lo = np.meshgrid(lat, lon, indexing="ij")
    s = np.stack([_sp(np.cos(la), e[0]) * _sp(np.cos(lo), e[1]),
                  _sp(np.cos(la), e[0]) * _sp(np.sin(lo), e[1]),
                  _sp(np.sin(la), e[0])], -1).reshape(-1, 3)
    v = np.vstack([[0, 0, -1], s, [0, 0, 1]]) * np.asarray(r, float)
    if R is not None:
        v = v @ np.asarray(R).T
    v = v + np.asarray(c, float)
    if post:
        v = post(v)
    return v, _ring_faces(nu - 1, nv)


def capsule(p0, p1, r0, r1, nseg=10, ncap=3):
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    ax = p1 - p0
    length = np.linalg.norm(ax)
    fr = frame_z(ax)
    u, v, w = fr[:, 0], fr[:, 1], fr[:, 2]
    rings = [(-r0 * math.cos(k / ncap * math.pi / 2), r0 * math.sin(k / ncap * math.pi / 2))
             for k in range(1, ncap + 1)]
    rings += [(length + r1 * math.cos(k / ncap * math.pi / 2), r1 * math.sin(k / ncap * math.pi / 2))
              for k in range(ncap, 0, -1)]
    ang = np.linspace(0, TAU, nseg, endpoint=False)
    verts = [p0 - r0 * w]
    for off, rad in rings:
        for a in ang:
            verts.append(p0 + w * off + rad * (math.cos(a) * u + math.sin(a) * v))
    verts.append(p1 + r1 * w)
    return np.array(verts), _ring_faces(len(rings), nseg)


def ellipsoid_along(p0, p1, rx, ry, nu=8, nv=12):
    """Ellipsoid spanning p0..p1 (a muscle or limb volume)."""
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    return superq((p0 + p1) / 2, (rx, ry, np.linalg.norm(p1 - p0) / 2), (1, 1), nu, nv, R=frame_z(p1 - p0))


def slab(surf, nu, nv, thick, k=0.5):
    """Closed plate of constant thickness over a parametric surface patch with rounded corners."""
    U, V = np.meshgrid(np.linspace(-1, 1, nu), np.linspace(-1, 1, nv), indexing="ij")
    Um = U * (1 - k + k * np.sqrt(1 - V ** 2 / 2))
    Vm = V * (1 - k + k * np.sqrt(1 - U ** 2 / 2))
    P, N = surf(Um, Vm)
    n = nu * nv
    verts = np.vstack([P.reshape(-1, 3), (P + N * thick).reshape(-1, 3)])

    def idx(i, j):
        return i * nv + j
    faces = []
    for i in range(nu - 1):
        for j in range(nv - 1):
            q = (idx(i, j), idx(i + 1, j), idx(i + 1, j + 1), idx(i, j + 1))
            faces.append(tuple(x + n for x in q))
            faces.append(tuple(reversed(q)))
    rim = [(i, 0) for i in range(nu)] + [(nu - 1, j) for j in range(1, nv)]
    rim += [(i, nv - 1) for i in range(nu - 2, -1, -1)] + [(0, j) for j in range(nv - 2, 0, -1)]
    for a, b in zip(rim, rim[1:] + rim[:1]):
        pa, pb = idx(*a), idx(*b)
        faces.append((pb + n, pa + n, pa, pb))
    return verts, faces


def ell_patch(c, r, R, a_rng, b_rng, gap=0.0):
    """Patch of an ellipsoid; (a, b) are longitude/latitude around the local +Y axis."""
    c, r, R = np.asarray(c, float), np.asarray(r, float), np.asarray(R, float)

    def f(U, V):
        a = (a_rng[0] + a_rng[1]) / 2 + U * (a_rng[1] - a_rng[0]) / 2
        b = (b_rng[0] + b_rng[1]) / 2 + V * (b_rng[1] - b_rng[0]) / 2
        d = np.stack([np.sin(a) * np.cos(b), np.cos(a) * np.cos(b), np.sin(b)], -1)
        nrm = d / r
        nrm = (nrm / np.linalg.norm(nrm, axis=-1, keepdims=True)) @ R.T
        return (d * r) @ R.T + c + nrm * gap, nrm
    return f


def limb_patch(p0, p1, r0, r1, toward, half_ang, t_rng, gap):
    """Patch wrapped around a tapered limb, centred on the `toward` side."""
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    ax = p1 - p0
    w = ax / np.linalg.norm(ax)
    d = np.asarray(toward, float)
    d = unit(d - d.dot(w) * w)
    e = np.cross(w, d)

    def f(U, V):
        t = t_rng[0] + (U + 1) / 2 * (t_rng[1] - t_rng[0])
        phi = V * half_ang
        radial = np.cos(phi)[..., None] * d + np.sin(phi)[..., None] * e
        rad = (r0 + (r1 - r0) * t + gap)[..., None]
        return p0 + t[..., None] * ax + radial * rad, radial
    return f


def arc_patch(center, axis, start, radius, sweep, width):
    """Band wrapped around an axis (a finger group closing round a grip)."""
    axis, start = unit(axis), np.asarray(start, float)
    start = unit(start - start.dot(axis) * axis)
    other = np.cross(axis, start)

    def f(U, V):
        th = (U + 1) / 2 * sweep
        radial = np.cos(th)[..., None] * start + np.sin(th)[..., None] * other
        return center + radial * radius + (V * width / 2)[..., None] * axis, radial
    return f


class MeshBuilder:
    def __init__(self):
        self.parts = []

    def add(self, part, bone, mat):
        v, f = part
        v = np.asarray(v, float)
        vol = 0.0
        for face in f:
            for i in range(1, len(face) - 1):
                vol += np.dot(v[face[0]], np.cross(v[face[i]], v[face[i + 1]]))
        if vol < 0:
            f = [tuple(reversed(face)) for face in f]
        self.parts.append((v, f, bone, mat))

    def to_object(self, name):
        verts, faces, fmat, groups = [], [], [], {}
        off = 0
        for v, f, bone, mat in self.parts:
            verts.append(v)
            faces += [tuple(i + off for i in face) for face in f]
            fmat += [mat] * len(f)
            if bone:
                groups.setdefault(bone, []).extend(range(off, off + len(v)))
            off += len(v)
        me = bpy.data.meshes.new(name)
        me.from_pydata(np.vstack(verts).tolist(), [], faces)
        me.attributes.new("region", "INT", "FACE").data.foreach_set("value", np.array(fmat, np.int32))
        me.update()
        obj = bpy.data.objects.new(name, me)
        bpy.context.scene.collection.objects.link(obj)
        for bone, idx in groups.items():
            obj.vertex_groups.new(name=bone).add(idx, 1.0, "REPLACE")
        return obj


def mirrored(mb, parts, central=("Hips", "Spine", "Chest", "Neck", "Head")):
    for (v, f), bone, mat in parts:
        if bone is None or bone in central:
            mb.add((v, f), bone, mat)
            mb.add((v * (-1, 1, 1), f), bone, mat)
        else:
            mb.add((v, f), bone + "_R", mat)
            mb.add((v * (-1, 1, 1), f), bone + "_L", mat)


def body_volumes():
    """Right half of the under-suit body as overlapping closed volumes (unioned by the remesh)."""
    out = []

    def add(part):
        out.append((part, None, CLOTH))
    add(superq((0.0, -0.012, 0.965), (0.15, 0.105, 0.1), (0.9, 0.9), 10, 14))
    add(superq((0.062, -0.05, 0.935), (0.075, 0.068, 0.085), (1, 1), 8, 12))
    add(superq((0.0, -0.006, 1.12), (0.128, 0.09, 0.11), (0.9, 0.9), 10, 14))
    add(superq((0.0, -0.012, 1.29), (0.155, 0.105, 0.15), (0.9, 0.9), 10, 14))
    add(superq((0.0, -0.022, 1.4), (0.172, 0.092, 0.072), (0.9, 0.9), 8, 14))
    add(capsule((0, -0.008, 1.42), (0, 0.004, 1.6), 0.054, 0.05, 12, 3))
    add(superq((0, 0.012, 1.655), (0.078, 0.096, 0.105), (1, 1), 10, 14))
    add(superq(SH + (0.012, 0, 0.004), (0.062, 0.062, 0.066), (1, 1), 8, 12))
    add(capsule(SH, EL, 0.053, 0.043, 12, 3))
    add(ellipsoid_along(SH + 0.25 * (EL - SH), SH + 0.8 * (EL - SH), 0.056, 0.052))
    add(capsule(EL, WR, 0.043, 0.032, 12, 3))
    add(ellipsoid_along(EL + 0.05 * (WR - EL), EL + 0.55 * (WR - EL), 0.05, 0.046))
    add(capsule(HIP, KN, 0.086, 0.058, 12, 3))
    add(ellipsoid_along(HIP + 0.15 * (KN - HIP) + (0, 0.012, 0), HIP + 0.85 * (KN - HIP) + (0, 0.01, 0),
                        0.083, 0.085))
    add(capsule(KN, AN, 0.056, 0.04, 12, 3))
    add(ellipsoid_along(KN + 0.1 * (AN - KN) + (0, -0.02, 0), KN + 0.55 * (AN - KN) + (0, -0.018, 0),
                        0.056, 0.058))
    return out


def build_body(arm):
    mb = MeshBuilder()
    for (v, f), bone, mat in body_volumes():
        mb.add((v, f), bone, mat)
        if abs(v[:, 0].mean()) > 1e-6:
            mb.add((v * (-1, 1, 1), f), bone, mat)
    obj = mb.to_object("Body")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    rm = obj.modifiers.new("remesh", "REMESH")
    rm.mode, rm.voxel_size, rm.adaptivity = "VOXEL", 0.008, 0.0
    sm = obj.modifiers.new("smooth", "SMOOTH")
    sm.factor, sm.iterations = 0.7, 6
    for m in ("remesh", "smooth"):
        bpy.ops.object.modifier_apply(modifier=m)
    dec = obj.modifiers.new("decimate", "DECIMATE")
    dec.ratio = BODY_TRIS / sum(len(p.vertices) - 2 for p in obj.data.polygons)
    dec.use_symmetry, dec.symmetry_axis = True, "X"
    bpy.ops.object.modifier_apply(modifier="decimate")
    obj.data.attributes.new("region", "INT", "FACE").data.foreach_set(
        "value", np.full(len(obj.data.polygons), CLOTH, np.int32))

    skin_weights(obj, arm)
    return obj


# Segment radius used to normalise the distance from a body vertex to each bone.
SKIN_RADIUS = {"Hips": 0.13, "Spine": 0.12, "Chest": 0.15, "Neck": 0.05, "Head": 0.09, "Clavicle": 0.06,
               "UpperArm": 0.05, "Forearm": 0.042, "Hand": 0.035, "Thigh": 0.07, "Shin": 0.052, "Foot": 0.045}
SKIN_SHARPNESS = 7.0


def skin_weights(obj, arm):
    """Soft nearest-bone weights: a softmin over radius-normalised distances to the bone segments,
    so each limb is rigid along its length and blends smoothly only around the joints."""
    names, heads, tails, radii = [], [], [], []
    for b in arm.data.bones:
        key = b.name.split("_")[0]
        if key in SKIN_RADIUS:
            names.append(b.name)
            heads.append(b.head_local)
            tails.append(b.tail_local)
            radii.append(SKIN_RADIUS[key])
    V = np.array([v.co for v in obj.data.vertices])
    H, T, R = np.array(heads), np.array(tails), np.array(radii)
    seg = T - H
    t = np.clip(np.einsum("nbk,bk->nb", V[:, None, :] - H[None], seg) / (seg * seg).sum(1), 0, 1)
    d = np.linalg.norm(V[:, None, :] - (H[None] + t[..., None] * seg[None]), axis=2) / R
    w = np.exp(-SKIN_SHARPNESS * (d - d.min(1, keepdims=True)))
    keep = np.argsort(-w, axis=1, kind="stable")[:, :4]
    top = np.take_along_axis(w, keep, 1)
    top[top < 0.02 * top[:, :1]] = 0.0
    top /= top.sum(1, keepdims=True)
    groups = {n: obj.vertex_groups.new(name=n) for n in names}
    for vi in range(len(V)):
        for bi, wt in zip(keep[vi], top[vi]):
            if wt > 0:
                groups[names[bi]].add([vi], float(wt), "REPLACE")



def helmet_brim(v):
    cy = -0.005
    az = np.arctan2(v[:, 0], v[:, 1] - cy)
    zcut = 1.6558 + 0.0365 * np.cos(az) - 0.0043 * np.cos(2 * az)
    low = v[:, 2] < zcut
    v = v.copy()
    v[low, 2] = zcut[low]
    v[low, 0] *= 1.04
    v[low, 1] = cy + (v[low, 1] - cy) * 1.04
    return v


def hand_parts(grip_r, thumb_side):
    """Gloved hand in hand-bone space (Y wrist->knuckles, Z back of hand) closed round a grip of
    radius grip_r whose axis runs along local X through hand_grip_point(grip_r)."""
    g = hand_grip_point(grip_r)
    t = thumb_side
    p = []
    p.append((capsule((0, -0.04, 0), (0, 0.008, 0), 0.042, 0.04, 12, 2), GLOVE))
    p.append((superq((0, 0.046, -0.002), (0.043, 0.05, 0.021), (0.5, 0.6), 6, 10), GLOVE))
    p.append((superq((0, 0.058, 0.019), (0.038, 0.034, 0.008), (0.4, 0.5), 4, 8), GUNMETAL))
    start = np.array([0, 0.092, -0.004]) - g
    p.append((slab(arc_patch(g, (1, 0, 0), start, grip_r + 0.002, -250 * D2R, 0.082), 9, 4, 0.021, 0.25),
              GLOVE))
    knuckle = np.array([t * 0.036, 0.022, -0.016])
    mid = g + unit(np.array([0, -0.6, 0.4])) * (grip_r + 0.012) + np.array([t * 0.038, 0, 0])
    tip = g + unit(np.array([0, -0.2, -1.0])) * (grip_r + 0.01) + np.array([t * 0.03, 0, 0])
    p.append((capsule(knuckle, mid, 0.014, 0.0125, 8, 2), GLOVE))
    p.append((capsule(mid, tip, 0.0125, 0.011, 8, 2), GLOVE))
    return p


def hand_grip_point(grip_r):
    return np.array([0.0, 0.07, -(0.022 + grip_r)])


GRIP_R_RADIUS = 0.021
GRIP_L_RADIUS = 0.03


def kit_parts(B):
    """Rigid right-side kit in rest world space (mirrored for the left)."""
    out = []

    def add(part, bone, mat):
        out.append((part, bone, mat))

    def axes(bone):
        m = np.array(B[bone + "_R"])
        return m[:3, 0], m[:3, 1], m[:3, 2]
    ux, uy, uz = axes("UpperArm")
    fx, fy, fz = axes("Forearm")
    up_out = unit((0.45, 0, 0.9))
    add(slab(ell_patch(SH + (0.0, -0.005, 0.01), (0.1, 0.112, 0.095), frame_to(up_out, (0, 1, 0)),
                       (-62 * D2R, 62 * D2R), (-70 * D2R, 70 * D2R)), 7, 8, 0.012, 0.6), "Clavicle", OLIVE)
    add(slab(ell_patch(SH + (0.0, -0.005, 0.01), (0.106, 0.118, 0.1), frame_to(up_out, (0, 1, 0)),
                       (-30 * D2R, 18 * D2R), (-34 * D2R, 34 * D2R), gap=0.012), 5, 5, 0.004, 0.6), "Clavicle", PANEL)
    add(slab(ell_patch(SH + 0.07 * _ARM, (0.082, 0.09, 0.08), frame_to(unit(ux), (0, 1, 0)),
                       (-50 * D2R, 50 * D2R), (-75 * D2R, 75 * D2R)), 6, 7, 0.011, 0.6), "UpperArm", OLIVE)
    add(slab(ell_patch(EL - 0.004 * uz, (0.05, 0.056, 0.05), frame_to(-uz, uy), (-48 * D2R, 48 * D2R),
                       (-48 * D2R, 48 * D2R), gap=0.0), 5, 5, 0.012, 0.6), "Forearm", GUNMETAL)
    add(slab(limb_patch(EL, WR, 0.047, 0.035, fx, 75 * D2R, (0.22, 0.86), 0.0), 6, 6, 0.008, 0.5),
        "Forearm", OLIVE)

    tx, ty, tz = (np.array(B["Thigh_R"])[:3, i] for i in range(3))
    add(slab(limb_patch(HIP, KN, 0.098, 0.084, unit(tz + 0.55 * tx), 62 * D2R, (0.1, 0.5), 0.004), 6, 6, 0.011,
             0.5), "Thigh", OLIVE)
    add(superq(HIP + 0.55 * (KN - HIP) + (0.075, -0.012, 0), (0.026, 0.055, 0.07), (0.3, 0.4), 4, 8,
               R=frame_z(KN - HIP)), "Thigh", WEB)
    sx, sy, sz = (np.array(B["Shin_R"])[:3, i] for i in range(3))
    add(slab(ell_patch(KN + 0.01 * sz, (0.066, 0.064, 0.07), frame_to(sz, -sy), (-62 * D2R, 62 * D2R),
                       (-55 * D2R, 55 * D2R)), 6, 6, 0.016, 0.6), "Shin", GUNMETAL)
    add(slab(limb_patch(KN, AN, 0.058, 0.043, sz, 68 * D2R, (0.2, 0.72), 0.0), 7, 6, 0.009, 0.5), "Shin", OLIVE)
    add(capsule(AN + (0, -0.004, 0.14), AN + (0, -0.004, 0.0), 0.054, 0.05, 12, 2), "Shin", BOOT)
    add(superq(AN + (0, 0.022, -0.03), (0.055, 0.1, 0.055), (0.5, 0.6), 6, 12), "Foot", BOOT)
    add(superq((AN[0], 0.024, 0.012), (0.053, 0.104, 0.012), (0.25, 0.45), 4, 14), "Foot", GLOVE)
    add(superq((BALL[0], 0.17, 0.031), (0.052, 0.062, 0.031), (0.45, 0.55), 5, 10), "Toe", BOOT)
    add(superq((BALL[0], 0.172, 0.011), (0.05, 0.06, 0.011), (0.25, 0.45), 4, 12), "Toe", GLOVE)

    add(superq((0.172, 0.04, 1.0), (0.032, 0.042, 0.048), (0.25, 0.3), 4, 8), "Hips", WEB)
    add(superq((0.09, -0.135, 1.01), (0.05, 0.028, 0.042), (0.25, 0.3), 4, 8), "Hips", WEB)
    add(superq((0.127, 0.0, 1.638), (0.045, 0.045, 0.02), (0.2, 1.0), 4, 12, R=np_rot("y", math.pi / 2)),
        "Head", GUNMETAL)
    add(superq((0.132, 0.03, 1.69), (0.008, 0.05, 0.008), (0.3, 0.3), 4, 6), "Head", GUNMETAL)
    add(slab(limb_patch((0.11, 0.09, 1.4), (0.11, -0.14, 1.4), 0.07, 0.07, (0, 0, 1), 26 * D2R, (0.0, 1.0),
                        0.0), 8, 3, 0.008, 0.2), "Chest", WEB)
    return out


def frame_to(fwd, up):
    """Rotation taking local +Y to `fwd`, local +Z towards `up`."""
    y = unit(fwd)
    z = np.asarray(up, float)
    z = unit(z - z.dot(y) * y)
    return np.stack([np.cross(y, z), y, z], 1)


def weapon_parts():
    def cyl_y(c, rad, half, nv=12):
        return superq(c, (rad, rad, half), (0.12, 1.0), 4, nv, R=np_rot("x", -math.pi / 2))
    p = []
    p.append((superq((0, 0.05, 0.035), (0.022, 0.13, 0.035), (0.25, 0.25), 4, 8), STEEL))
    p.append((superq((0, 0.06, 0.073), (0.012, 0.13, 0.005), (0.2, 0.2), 4, 6), STEEL))
    p.append((superq((0, 0.07, 0.094), (0.019, 0.048, 0.022), (0.3, 0.3), 4, 8), STEEL))
    p.append((superq((0, 0.29, 0.04), (0.027, 0.115, 0.03), (0.3, 0.6), 4, 10), POLY))
    p.append((cyl_y((0, 0.47, 0.045), 0.011, 0.07, 8), STEEL))
    p.append((cyl_y((0, 0.555, 0.045), 0.016, 0.02, 8), STEEL))
    p.append((superq((0, 0.11, -0.06), (0.014, 0.03, 0.06), (0.3, 0.3), 4, 8, R=np_rot("x", 12 * D2R)), POLY))
    p.append((superq((0, -0.025, -0.05), (0.016, 0.022, 0.055), (0.4, 0.4), 4, 8, R=np_rot("x", -15 * D2R)),
              POLY))
    p.append((superq((0, -0.135, 0.015), (0.02, 0.075, 0.038), (0.3, 0.3), 4, 8), POLY))
    p.append((superq((0, -0.215, 0.008), (0.022, 0.012, 0.052), (0.3, 0.3), 4, 8), POLY))
    p.append((cyl_y((0, 0.385, -0.005), 0.018, 0.045, 10), STEEL))
    p.append((cyl_y((0, 0.4315, -0.005), 0.0148, 0.0025, 10), LENS))
    return p


def build_kit(arm):
    B = {b.name: b.matrix_local for b in arm.data.bones}
    mb = MeshBuilder()
    mb.add(superq((0, -0.006, 1.31), (0.172, 0.124, 0.165), (0.45, 0.6), 8, 16), "Chest", WEB)
    front = ell_patch((0, -0.03, 1.3), (0.186, 0.175, 0.2), np.eye(3), (-50 * D2R, 50 * D2R),
                      (-34 * D2R, 36 * D2R))
    mb.add(slab(front, 8, 8, 0.016, 0.55), "Chest", OLIVE)
    mb.add(slab(ell_patch((0, 0.02, 1.31), (0.186, 0.168, 0.2), np_rot("z", math.pi), (-48 * D2R, 48 * D2R),
                          (-30 * D2R, 36 * D2R)), 8, 8, 0.016, 0.55), "Chest", OLIVE)
    mb.add(slab(ell_patch((0, -0.03, 1.3), (0.186, 0.175, 0.2), np.eye(3), (-16 * D2R, 16 * D2R),
                          (14 * D2R, 26 * D2R), gap=0.016), 4, 4, 0.004, 0.5), "Chest", PANEL)
    for x in (-0.068, 0.0, 0.068):
        mb.add(superq((x, 0.152, 1.215), (0.03, 0.026, 0.05), (0.25, 0.3), 4, 8), "Chest", WEB)
    mb.add(superq((0, -0.01, 1.462), (0.098, 0.088, 0.03), (0.6, 0.8), 4, 12), "Chest", WEB)
    mb.add(superq((0, 0, 1.15), (0.158, 0.116, 0.062), (0.3, 0.6), 6, 14), "Spine", WEB)
    mb.add(superq((0, 0, 1.035), (0.168, 0.124, 0.03), (0.2, 0.6), 4, 16), "Hips", WEB)
    mb.add(superq((0, 0.124, 1.035), (0.025, 0.008, 0.02), (0.3, 0.3), 4, 6), "Hips", GUNMETAL)
    mb.add(superq((0, -0.232, 1.3), (0.13, 0.08, 0.16), (0.35, 0.45), 6, 12), "Chest", WEB)
    mb.add(superq((0, -0.236, 1.462), (0.124, 0.076, 0.028), (0.3, 0.45), 4, 12), "Chest", PANEL)
    mb.add(superq((0, -0.312, 1.26), (0.09, 0.022, 0.08), (0.25, 0.4), 4, 8), "Chest", GUNMETAL)
    for x in (-0.142, 0.142):
        mb.add(superq((x, -0.23, 1.25), (0.022, 0.05, 0.08), (0.3, 0.4), 4, 8), "Chest", WEB)
    mb.add(superq((0, -0.005, 1.67), (0.13, 0.147, 0.13), (1, 1), 10, 18, post=helmet_brim), "Head", OLIVE)
    top = np_rot("x", math.pi / 2)
    mb.add(slab(ell_patch((0, -0.005, 1.67), (0.13, 0.147, 0.13), top, (-38 * D2R, 38 * D2R),
                          (-48 * D2R, 30 * D2R), gap=0.0), 6, 6, 0.006, 0.6), "Head", PANEL)
    mb.add(slab(ell_patch((0, 0.016, 1.645), (0.112, 0.124, 0.1), np.eye(3), (-80 * D2R, 80 * D2R),
                          (-30 * D2R, 15 * D2R)), 10, 5, 0.005, 0.5), "Head", VISOR)
    mb.add(slab(ell_patch((0, 0.012, 1.63), (0.096, 0.114, 0.09), np.eye(3), (-58 * D2R, 58 * D2R),
                          (-62 * D2R, -30 * D2R)), 6, 4, 0.01, 0.5), "Head", GUNMETAL)
    mb.add(superq((0, 0.142, 1.718), (0.026, 0.014, 0.022), (0.3, 0.3), 4, 6), "Head", GUNMETAL)
    mirrored(mb, kit_parts(B))

    for tag, s, radius in (("R", 1, GRIP_R_RADIUS), ("L", -1, GRIP_L_RADIUS)):
        bm = np.array(B[f"Hand_{tag}"])
        for (v, f), mat in hand_parts(radius, -1 if tag == "R" else 1):
            mb.add((v @ bm[:3, :3].T + bm[:3, 3], f), f"Hand_{tag}", mat)
    bw = np.array(B["Weapon"])
    for (v, f), mat in weapon_parts():
        mb.add((v @ bw[:3, :3].T + bw[:3, 3], f), "Weapon", mat)
    return mb.to_object("Kit")


def build_mesh(arm):
    body = build_body(arm)
    kit = build_kit(arm)
    bpy.ops.object.select_all(action="DESELECT")
    body.select_set(True)
    kit.select_set(True)
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.join()
    obj = body
    obj.name = "TrooperBody"
    me = obj.data
    me.polygons.foreach_set("use_smooth", np.ones(len(me.polygons), bool))
    me.set_sharp_from_angle(angle=62 * D2R)
    obj.parent = arm
    obj.modifiers.new("Armature", "ARMATURE").object = arm
    tris = sum(len(p.vertices) - 2 for p in me.polygons)
    print(f"TROOPER mesh: {len(me.vertices)} verts, {tris} tris, {len(obj.vertex_groups)} groups")
    return obj


def region_material(idx, spec):
    mat = bpy.data.materials.new(f"region_{idx}")
    nt = mat.node_tree
    N, L = nt.nodes, nt.links
    N.clear()

    def node(kind, **kw):
        n = N.new(kind)
        for k, val in kw.items():
            setattr(n, k, val)
        return n

    def math_(op, a, b=None, clamp=False):
        n = node("ShaderNodeMath", operation=op, use_clamp=clamp)
        for i, x in enumerate((a, b)):
            if x is None:
                continue
            if isinstance(x, (int, float)):
                n.inputs[i].default_value = x
            else:
                L.new(x, n.inputs[i])
        return n.outputs[0]

    def mix(fac, a, b, blend="MIX"):
        n = node("ShaderNodeMix", data_type="RGBA", blend_type=blend)
        if isinstance(fac, (int, float)):
            n.inputs[0].default_value = fac
        else:
            L.new(fac, n.inputs[0])
        for sock, col in ((n.inputs[6], a), (n.inputs[7], b)):
            if isinstance(col, (tuple, list)):
                sock.default_value = (*col[:3], 1.0)
            else:
                L.new(col, sock)
        return n.outputs[2]

    tc = node("ShaderNodeTexCoord").outputs["Object"]

    def noise(scale, detail=4.0, rough=0.55, vec=None, distortion=0.0):
        n = node("ShaderNodeTexNoise")
        n.inputs["Scale"].default_value = scale
        n.inputs["Detail"].default_value = detail
        n.inputs["Roughness"].default_value = rough
        n.inputs["Distortion"].default_value = distortion
        L.new(vec if vec is not None else tc, n.inputs["Vector"])
        return n.outputs["Fac"]

    def wave(scale, direction, profile="SIN", distortion=0.0, vec=None, wave_type="BANDS"):
        w = node("ShaderNodeTexWave", wave_type=wave_type, bands_direction=direction, wave_profile=profile)
        w.inputs["Scale"].default_value = scale
        w.inputs["Distortion"].default_value = distortion
        w.inputs["Detail"].default_value = 2.0
        L.new(vec if vec is not None else tc, w.inputs["Vector"])
        return w.outputs["Fac"]

    def ramp(x, lo, hi):
        n = node("ShaderNodeMapRange", clamp=True)
        L.new(x, n.inputs["Value"])
        n.inputs["From Min"].default_value = lo
        n.inputs["From Max"].default_value = hi
        return n.outputs[0]

    bevel = node("ShaderNodeBevel", samples=8)
    bevel.inputs["Radius"].default_value = 0.004
    geo = node("ShaderNodeNewGeometry")
    dot = node("ShaderNodeVectorMath", operation="DOT_PRODUCT")
    L.new(bevel.outputs[0], dot.inputs[0])
    L.new(geo.outputs["Normal"], dot.inputs[1])
    curvature = ramp(math_("SUBTRACT", 1.0, dot.outputs["Value"]), 0.004, 0.05)
    n_var, n_chip, n_fine, n_grime = noise(5.0), noise(60.0, 8.0, 0.65), noise(160.0, 3.0), noise(2.2, 3.0)
    wear = math_("MULTIPLY", curvature, ramp(n_chip, 0.38, 0.62))
    wear = math_("MULTIPLY", wear, spec["wear_amt"], clamp=True)
    if spec.get("scratch"):
        mp = node("ShaderNodeMapping")
        mp.inputs["Scale"].default_value = (90.0, 3.0, 90.0)
        L.new(tc, mp.inputs["Vector"])
        streak = math_("GREATER_THAN", noise(1.0, 2.0, 0.5, mp.outputs[0]), 0.72)
        wear = math_("MAXIMUM", wear, math_("MULTIPLY", streak, 0.6 * spec["wear_amt"]))
    base = lin(spec["base"])
    col = mix(n_var, [c * 0.86 for c in base], [min(c * 1.12, 1.0) for c in base])
    height = math_("MULTIPLY", n_fine, 0.2)
    if spec.get("cloth"):
        wr = math_("MULTIPLY", wave(9.0, "Z", "SIN", 7.0), ramp(noise(3.0), 0.35, 0.75))
        height = math_("ADD", height, math_("MULTIPLY", wr, 1.4))
        height = math_("ADD", height, math_("MULTIPLY", math_("ADD", wave(300, "X"), wave(300, "Z")), 0.12))
    if spec.get("web"):
        rows = wave(12.6, "Z")
        stitch_line = ramp(rows, 0.86, 0.94)
        dash = math_("GREATER_THAN", wave(70.0, "X"), 0.5)
        stitch = math_("MULTIPLY", stitch_line, dash)
        col = mix(stitch, col, [c * 0.55 for c in base])
        height = math_("ADD", height, math_("MULTIPLY", rows, 0.5))
        height = math_("SUBTRACT", height, math_("MULTIPLY", stitch, 0.6))
        height = math_("ADD", height, math_("MULTIPLY", math_("ADD", wave(300, "X"), wave(300, "Z")), 0.15))
    col = mix(wear, col, lin(spec["wear"]))
    ao = node("ShaderNodeAmbientOcclusion", samples=8, only_local=True)
    ao.inputs["Distance"].default_value = 0.06
    occl = ramp(ao.outputs["AO"], 0.0, 1.0)
    grime = math_("MULTIPLY", math_("ADD", 0.55, math_("MULTIPLY", occl, 0.45)),
                  math_("ADD", 0.85, math_("MULTIPLY", n_grime, 0.15)))
    gcol = node("ShaderNodeCombineColor")
    for i in range(3):
        L.new(grime, gcol.inputs[i])
    col = mix(1.0, col, gcol.outputs[0], "MULTIPLY")
    r0, r1 = spec["rough"]
    rough = math_("ADD", r0, math_("MULTIPLY", wear, r1 - r0))
    rough = math_("ADD", rough, math_("MULTIPLY", math_("SUBTRACT", n_var, 0.5), 0.1), clamp=True)
    m0, m1 = spec["metal"]
    metal = math_("ADD", m0, math_("MULTIPLY", wear, m1 - m0), clamp=True)
    height = math_("SUBTRACT", height, math_("MULTIPLY", wear, 0.3))
    bump = node("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.4 if spec.get("cloth") or spec.get("web") else 0.25
    bump.inputs["Distance"].default_value = 0.002
    L.new(height, bump.inputs["Height"])
    L.new(bevel.outputs[0], bump.inputs["Normal"])
    bsdf = node("ShaderNodeBsdfPrincipled")
    L.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    emit_col = node("ShaderNodeRGB")
    emit_col.outputs[0].default_value = (*spec.get("emit", (0, 0, 0)), 1.0)
    emission = node("ShaderNodeEmission")
    out = node("ShaderNodeOutputMaterial")
    img = node("ShaderNodeTexImage")
    socks = dict(base=col, rough=rough, metal=metal, emit=emit_col.outputs[0])
    return mat, dict(socks=socks, bsdf=bsdf, emission=emission, out=out, img=img, links=L, nodes=N)


def setup_cycles():
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.seed = 7
    sc.cycles.use_denoising = False


def unwrap(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=60 * D2R, island_margin=0.003, area_weight=0.0)
    bpy.ops.uv.pack_islands(rotate=True, margin=0.003)
    bpy.ops.object.mode_set(mode="OBJECT")


def bake(obj, arm, tmp):
    arm.data.pose_position = "REST"
    regions = [region_material(i, MATERIALS[i]) for i in range(len(MATERIALS))]
    obj.data.materials.clear()
    for mat, _ in regions:
        obj.data.materials.append(mat)
    region = np.empty(len(obj.data.polygons), np.int32)
    obj.data.attributes["region"].data.foreach_get("value", region)
    obj.data.polygons.foreach_set("material_index", region)
    obj.data.attributes.remove(obj.data.attributes["region"])
    imgs = {}
    for kind, data in (("base", False), ("rough", True), ("metal", True), ("emit", False), ("normal", True)):
        img = bpy.data.images.new(f"trooper_{kind}", TEX, TEX, alpha=False)
        if data:
            img.colorspace_settings.name = "Non-Color"
        imgs[kind] = img
    sc = bpy.context.scene
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    for kind in ("base", "rough", "metal", "emit", "normal"):
        for _, r in regions:
            r["img"].image = imgs[kind]
            r["nodes"].active = r["img"]
            L = r["links"]
            if kind == "normal":
                L.new(r["bsdf"].outputs[0], r["out"].inputs["Surface"])
            else:
                L.new(r["socks"][kind], r["emission"].inputs["Color"])
                L.new(r["emission"].outputs[0], r["out"].inputs["Surface"])
        sc.cycles.samples = 1 if kind == "emit" else 12
        bpy.ops.object.bake(type="NORMAL" if kind == "normal" else "EMIT", margin=6, margin_type="EXTEND",
                            normal_space="TANGENT", use_clear=True, target="IMAGE_TEXTURES")
    px = {}
    for kind in ("rough", "metal"):
        a = np.empty(TEX * TEX * 4, np.float32)
        imgs[kind].pixels.foreach_get(a)
        px[kind] = a
    orm = np.ones(TEX * TEX * 4, np.float32)
    orm[1::4] = px["rough"][0::4]
    orm[2::4] = px["metal"][0::4]
    img_orm = bpy.data.images.new("trooper_orm", TEX, TEX, alpha=False)
    img_orm.colorspace_settings.name = "Non-Color"
    img_orm.pixels.foreach_set(orm)
    final = {"base": imgs["base"], "orm": img_orm, "normal": imgs["normal"], "emit": imgs["emit"]}
    for kind, img in final.items():
        img.filepath_raw = os.path.join(tmp, f"trooper_{kind}.png")
        img.file_format = "PNG"
        img.save()
        img.pack()

    mat = bpy.data.materials.new("Trooper")
    mat.use_backface_culling = True
    nt = mat.node_tree
    nt.nodes.clear()
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
    tex = {}
    for kind, img in final.items():
        tex[kind] = nt.nodes.new("ShaderNodeTexImage")
        tex[kind].image = img
    nt.links.new(tex["base"].outputs["Color"], bsdf.inputs["Base Color"])
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(tex["orm"].outputs["Color"], sep.inputs["Color"])
    nt.links.new(sep.outputs["Green"], bsdf.inputs["Roughness"])
    nt.links.new(sep.outputs["Blue"], bsdf.inputs["Metallic"])
    nm = nt.nodes.new("ShaderNodeNormalMap")
    nt.links.new(tex["normal"].outputs["Color"], nm.inputs["Color"])
    nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    nt.links.new(tex["emit"].outputs["Color"], bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = 1.0
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    obj.data.polygons.foreach_set("material_index", np.zeros(len(obj.data.polygons), np.int32))
    for m, _ in regions:
        bpy.data.materials.remove(m)
    arm.data.pose_position = "POSE"


class Poser:
    def __init__(self, arm):
        bones = arm.data.bones
        self.order = [b.name for b in bones]
        self.parent = {b.name: b.parent.name if b.parent else None for b in bones}
        self.B = {b.name: b.matrix_local.copy() for b in bones}
        self.Bi = {n: m.inverted() for n, m in self.B.items()}
        self.R0 = {n: m.to_3x3() for n, m in self.B.items()}
        self.length = {b.name: b.length for b in bones}
        self.P, self.D = {}, {}
        self.ratios = {}

    def head(self, n):
        p = self.parent[n]
        return self.P[p] @ self.Bi[p] @ self.B[n].translation

    def set(self, n, loc, rot):
        self.P[n] = Matrix.LocRotScale(loc, rot, None)

    def fk(self, n, local=None, world=None):
        d = world if world is not None else self.D[self.parent[n]] @ local
        self.D[n] = d
        self.set(n, self.head(n) if self.parent[n] else self.B[n].translation, d @ self.R0[n])

    def root(self, loc, d):
        self.D["Hips"] = d
        self.set("Hips", loc, d @ self.R0["Hips"])

    def two_bone(self, upper, lower, target, pole, zsign):
        head = self.head(upper)
        l1, l2 = self.length[upper], self.length[lower]
        d = target - head
        dist = d.length
        self.ratios[upper] = max(self.ratios.get(upper, 0.0), dist / (l1 + l2))
        dist = min(dist, (l1 + l2) * 0.9995)
        u = d.normalized()
        x = (l1 * l1 - l2 * l2 + dist * dist) / (2 * dist)
        h = math.sqrt(max(l1 * l1 - x * x, 0.0))
        w = (pole - u * pole.dot(u)).normalized()
        joint = head + u * x + w * h
        end = head + u * dist
        y1, y2 = (joint - head).normalized(), (end - joint).normalized()
        hinge = y1.cross(w * zsign).normalized()
        self.set(upper, head, Matrix((hinge, y1, hinge.cross(y1))).transposed())
        self.set(lower, joint, Matrix((hinge, y2, hinge.cross(y2))).transposed())
        return end

    def leg(self, tag, ankle, yaw, pitch, toe_bend, pole):
        end = self.two_bone(f"Thigh_{tag}", f"Shin_{tag}", ankle, pole, 1)
        rf = Rz(yaw) @ Rx(pitch)
        self.set(f"Foot_{tag}", end, rf @ self.R0[f"Foot_{tag}"])
        ball = end + rf @ (self.B[f"Toe_{tag}"].translation - self.B[f"Foot_{tag}"].translation)
        self.set(f"Toe_{tag}", ball, Rz(yaw) @ Rx(pitch + toe_bend) @ self.R0[f"Toe_{tag}"])

    def arm(self, tag, wrist, hand_rot, pole):
        fore, hand = f"Forearm_{tag}", f"Hand_{tag}"
        end = self.two_bone(f"UpperArm_{tag}", fore, wrist, pole, -1)
        # Real forearms share the wrist's twist along their length; giving the forearm half of it
        # keeps the smooth-skinned wrist from wringing like a candy wrapper.
        rf = self.P[fore].to_3x3()
        rel = (rf.transposed() @ hand_rot @ self.R0[hand].transposed() @ self.R0[fore]).to_quaternion()
        twist = 2 * math.atan2(rel.y, rel.w)
        self.set(fore, self.P[fore].translation, rf @ Ry(0.5 * twist))
        self.set(hand, end, hand_rot)

    def basis(self):
        out = {}
        for n in self.order:
            p = self.parent[n]
            if p is None:
                out[n] = self.Bi[n] @ self.P[n]
            else:
                out[n] = (self.Bi[p] @ self.B[n]).inverted() @ self.P[p].inverted() @ self.P[n]
        return out


def _frame(x, y, z):
    return Matrix((Vector(x).normalized(), Vector(y).normalized(), Vector(z).normalized())).transposed()


PISTOL_GRIP = Vector((0, -0.025, -0.05))
HANDGUARD = Vector((0, 0.2, 0.04))
BUTT = Vector((0, -0.227, 0.008))
_g = Vector((0, -math.sin(15 * D2R), -math.cos(15 * D2R)))
GRIP_R = _frame(_g, Vector((1, 0, 0)).cross(_g), (1, 0, 0))
GRIP_L = Ry(-35 * D2R) @ _frame((0, 1, 0), (1, 0, 0), (0, 0, -1))


def hold_weapon(po, butt_rest, rot, elbow_r, elbow_l):
    butt = po.P["Chest"] @ po.Bi["Chest"] @ Vector(butt_rest)
    pos = butt - rot @ BUTT
    po.set("Weapon", pos, rot)
    gr, gl = Vector(hand_grip_point(GRIP_R_RADIUS)), Vector(hand_grip_point(GRIP_L_RADIUS))
    po.arm("R", pos + rot @ (PISTOL_GRIP - GRIP_R @ gr), rot @ GRIP_R, Vector(elbow_r))
    po.arm("L", pos + rot @ (HANDGUARD - GRIP_L @ gl), rot @ GRIP_L, Vector(elbow_l))


def upper_body(po, spine, chest, head_world, clav_r, clav_l):
    po.fk("Spine", spine)
    po.fk("Chest", chest)
    qc, qh = po.D["Chest"].to_quaternion(), head_world.to_quaternion()
    po.fk("Neck", world=qc.slerp(qh, 0.5).to_matrix())
    po.fk("Head", world=head_world)
    po.fk("Clavicle_R", clav_r)
    po.fk("Clavicle_L", mirror(clav_l))


# Sole points relative to the ankle (y, z): heel contact, and the toe joint the foot rolls over.
HEEL = (-0.055, -0.085)
TOE_JOINT = (BALL[1] - AN[1], BALL[2] - AN[2])
HEEL_TO_TOE = TOE_JOINT[0] - HEEL[0]
TOE_Z = BALL[2]


def _rot2(p, y, z):
    return y * math.cos(p) - z * math.sin(p), y * math.sin(p) + z * math.cos(p)


class Gait:
    """In-place foot cycle: while planted the ground under the foot slides at `speed` (along +Y,
    signed); the foot pivots on the heel when pitched toes-up and on the toe joint when toes-down.
    The swing is a smooth curve whose end velocities match the contact phase."""

    def __init__(self, frames, duty, speed, strike, pitch_fn, swing_h):
        self.T = frames / FPS
        self.duty, self.speed, self.strike, self.pitch_fn, self.swing_h = duty, speed, strike, pitch_fn, swing_h
        self.lo, self.land = self.contact(1.0), self.contact(0.0)
        self.v_lo, self.v_land = self._vel(0.999, 1.0), self._vel(0.0, 0.001)

    def contact(self, u):
        s = self.strike + self.speed * u * self.duty * self.T
        p = self.pitch_fn(u)
        if p >= 0:
            hy, hz = _rot2(p, *HEEL)
            return s - hy, -hz, p, 0.0
        jy, jz = _rot2(p, *TOE_JOINT)
        return s + HEEL_TO_TOE - jy, TOE_Z - jz, p, -p

    def _vel(self, u0, u1):
        a, b = self.contact(u0), self.contact(u1)
        dt = (u1 - u0) * self.duty * self.T
        return (b[0] - a[0]) / dt, (b[1] - a[1]) / dt

    def foot(self, phi):
        phi %= 1.0
        if phi < self.duty:
            return self.contact(phi / self.duty)
        s = (phi - self.duty) / (1 - self.duty)
        span = (1 - self.duty) * self.T
        (y0, z0, p0, t0), (y1, z1, p1, t1) = self.lo, self.land
        (vy0, vz0), (vy1, vz1) = self.v_lo, self.v_land
        S = smooth(s)
        f0 = s * (1 - s) ** 4
        f1 = -(s ** 6) * (1 - s)
        y = y0 + (y1 - y0) * S + span * (vy0 * f0 + vy1 * f1)
        z = z0 + (z1 - z0) * S + span * (vz0 * f0 + vz1 * f1) + self.swing_h * math.sin(math.pi * s) ** 1.2
        p = p0 + (p1 - p0) * S
        toe = t0 * (1 - smooth(s / 0.45)) + t1 * smooth((s - 0.55) / 0.45)
        return y, z, p, toe

    def min_sole(self):
        lo = 1.0
        for i in range(400):
            y, z, p, toe = self.foot(i / 400)
            jy, jz = _rot2(p, *TOE_JOINT)
            tip = jz + 0.09 * math.sin(p + toe) - TOE_Z
            lo = min(lo, z + _rot2(p, *HEEL)[1], z + tip)
        return lo


RUN_FRAMES, RUN_DUTY = 20, 0.25
BACK_FRAMES, BACK_DUTY = 22, 0.3


def _run_pitch(u):
    if u < 0.18:
        return 14 * D2R * (1 - smooth(u / 0.18))
    if u < 0.42:
        return 0.0
    return -50 * D2R * ((u - 0.42) / 0.58) ** 1.6


def _back_pitch(u):
    if u < 0.3:
        return -26 * D2R * (1 - smooth(u / 0.3))
    if u < 0.68:
        return 0.0
    return -22 * D2R * smooth((u - 0.68) / 0.32)


RUN_GAIT = Gait(RUN_FRAMES, RUN_DUTY, -4.4, 0.2, _run_pitch, 0.2)
BACK_GAIT = Gait(BACK_FRAMES, BACK_DUTY, 3.3, -0.33 - HEEL_TO_TOE, _back_pitch, 0.13)
# Interpolating the IK chain between keys dips the sole a few millimetres below the exact keys.
FOOT_LIFT = 0.005


def legs(po, gait, phi, x=0.085):
    for tag, sx, off in (("R", 1, 0.0), ("L", -1, 0.5)):
        y, z, p, toe = gait.foot(phi + off)
        po.leg(tag, Vector((sx * x, y, z + FOOT_LIFT)), 0.0, p, toe, Vector((0.08 * sx, 1, 0)))


def run_pose(po, phi):
    psi = 9 * D2R * math.cos(TAU * phi)
    bob = 0.022 * math.cos(2 * TAU * (phi - RUN_DUTY / 2 - 0.25))
    roll = -3.5 * D2R * math.cos(TAU * (phi - RUN_DUTY / 2))
    po.root(Vector((0, 0, 0.935 + bob)), Rz(psi) @ Ry(roll) @ Rx(-6 * D2R))
    upper_body(po, Rz(-0.75 * psi) @ Rx(-8 * D2R) @ Ry(-0.6 * roll), Rz(-0.65 * psi - 10 * D2R) @ Rx(-4 * D2R),
               Rx(-4 * D2R), Rz(6 * D2R), Rz(26 * D2R))
    sway = 0.6 * D2R * math.sin(TAU * phi)
    hold_weapon(po, (0.115, 0.15, 1.33), Rz(4 * D2R + sway) @ Rx(-10 * D2R), (0.4, -0.25, -0.88),
                (-0.45, -0.1, -0.9))
    legs(po, RUN_GAIT, phi)


def runback_pose(po, phi):
    psi = -5 * D2R * math.cos(TAU * phi)
    bob = 0.016 * math.cos(2 * TAU * (phi - BACK_DUTY / 2 - 0.25))
    roll = -2.5 * D2R * math.cos(TAU * (phi - BACK_DUTY / 2))
    po.root(Vector((0, 0, 0.893 + bob)), Rz(psi) @ Ry(roll) @ Rx(-9 * D2R))
    upper_body(po, Rz(-0.8 * psi) @ Rx(-6 * D2R) @ Ry(-0.6 * roll), Rz(-0.6 * psi - 12 * D2R) @ Rx(-3 * D2R),
               Rx(-1 * D2R), Rz(8 * D2R), Rz(22 * D2R))
    sway = 0.5 * D2R * math.sin(TAU * phi)
    hold_weapon(po, (0.12, 0.13, 1.37), Rz(5 * D2R + sway) @ Rx(-2 * D2R), (0.35, -0.3, -0.88),
                (-0.45, -0.1, -0.9))
    legs(po, BACK_GAIT, phi, 0.095)


def idle_pose(po, phi):
    breath = math.sin(TAU * phi)
    shift = math.sin(TAU * phi + 0.6)
    po.root(Vector((0, 0, 0.955 + 0.004 * breath)),
            Rz(-14 * D2R + 1.5 * D2R * shift) @ Ry(2.2 * D2R * shift) @ Rx(-5 * D2R))
    head = Rz(4 * D2R + 7 * D2R * math.sin(TAU * phi - 1.0)) @ Rx((-3 + 1.5 * math.sin(2 * TAU * phi)) * D2R)
    upper_body(po, Rz(-4 * D2R) @ Rx(-4 * D2R) @ Ry(-1.5 * D2R * shift),
               Rz(-6 * D2R) @ Rx((-3 + 1.2 * breath) * D2R), head,
               Rz(8 * D2R) @ Ry(-1.2 * D2R * breath), Rz(18 * D2R) @ Ry(-1.2 * D2R * breath))
    sway_y = 1.2 * D2R * math.sin(2 * TAU * phi + 0.3)
    sway_p = 1.0 * D2R * math.sin(2 * TAU * phi + 1.7) + 0.6 * D2R * breath
    hold_weapon(po, (0.115, 0.15, 1.33), Rz(8 * D2R + sway_y) @ Rx(-30 * D2R + sway_p) @ Ry(-4 * D2R),
                (0.45, -0.3, -0.85), (-0.4, -0.1, -0.9))
    po.leg("L", Vector((-0.13, 0.12, AN[2])), 10 * D2R, 0.0, 0.0, Vector((-0.25, 1, 0)))
    po.leg("R", Vector((0.15, -0.12, AN[2])), -28 * D2R, 0.0, 0.0, Vector((0.55, 1, 0)))


def key_clip(arm, po, name, nframes, posefn):
    ad = arm.animation_data or arm.animation_data_create()
    act = bpy.data.actions.new(name)
    ad.action = act
    po.ratios = {}
    frames = []
    for f in range(nframes):
        posefn(po, f / nframes)
        frames.append(po.basis())
    frames.append(frames[0])
    prev, first = {}, {}
    for f, basis in enumerate(frames):
        for n in po.order:
            pb = arm.pose.bones[n]
            q = basis[n].to_quaternion().normalized()
            if n in prev and q.dot(prev[n]) < 0:
                q.negate()
            if f == nframes:
                q = first[n]
            prev[n] = q
            pb.rotation_quaternion = q
            pb.keyframe_insert("rotation_quaternion", frame=f, group=n)
            if n in ("Hips", "Weapon"):
                pb.location = Vector([0.0 if abs(c) < 1e-9 else c for c in basis[n].translation])
                pb.keyframe_insert("location", frame=f, group=n)
        if f == 0:
            first = dict(prev)
    track = ad.nla_tracks.new()
    track.name = name
    track.strips.new(name, 0, act)
    ad.action = None
    print(f"TROOPER clip {name}: reach ratios " + ", ".join(f"{k}={v:.3f}" for k, v in sorted(po.ratios.items())))


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    setup_cycles()
    sc = bpy.context.scene
    sc.render.fps = FPS
    bpy.context.preferences.edit.keyframe_new_interpolation_type = "LINEAR"
    arm = build_armature()
    body = build_mesh(arm)
    unwrap(body)
    tmp = tempfile.mkdtemp(prefix="gen_trooper_")
    try:
        bake(body, arm, tmp)
        po = Poser(arm)
        print(f"TROOPER sole min height: run {RUN_GAIT.min_sole():.4f}, runback {BACK_GAIT.min_sole():.4f}")
        key_clip(arm, po, "Idle", 96, idle_pose)
        key_clip(arm, po, "Run", RUN_FRAMES, run_pose)
        key_clip(arm, po, "RunBack", BACK_FRAMES, runback_pose)
        for pb in arm.pose.bones:
            pb.rotation_quaternion = (1, 0, 0, 0)
            pb.location = (0, 0, 0)
        sc.frame_start, sc.frame_end = 0, 96
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        bpy.ops.export_scene.gltf(
            filepath=OUT, export_format="GLB", use_selection=False, export_yup=True, export_apply=False,
            export_texcoords=True, export_normals=True, export_tangents=True, export_materials="EXPORT",
            export_image_format="JPEG", export_jpeg_quality=80, export_skins=True,
            export_all_influences=False, export_animations=True, export_animation_mode="ACTIONS",
            export_force_sampling=True, export_frame_step=1, export_optimize_animation_size=True,
            export_morph=False, export_lights=False, export_cameras=False, export_extras=False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"TROOPER wrote {OUT} ({os.path.getsize(OUT)} bytes)")


main()
