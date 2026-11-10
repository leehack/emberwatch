import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter_scene/scene.dart';
import 'package:vector_math/vector_math.dart' as vm;

import 'player.dart';
import 'world.dart';

class EnemyType {
  const EnemyType({
    required this.name,
    required this.health,
    required this.speed,
    required this.scale,
    required this.damage,
    required this.ember,
    required this.color,
  });

  final String name;
  final double health, speed, scale;

  /// Damage per second while touching the player.
  final double damage;

  /// Ember value dropped on death.
  final int ember;
  final List<double> color;

  double get radius => 0.42 * scale;

  static const skitterer = EnemyType(
    name: 'Skitterer',
    health: 9,
    speed: 4.3,
    scale: 0.55,
    damage: 7,
    ember: 1,
    color: [0.06, 0.05, 0.045],
  );
  static const stalker = EnemyType(
    name: 'Stalker',
    health: 24,
    speed: 2.8,
    scale: 0.85,
    damage: 12,
    ember: 1,
    color: [0.075, 0.045, 0.03],
  );
  static const brute = EnemyType(
    name: 'Brute',
    health: 150,
    speed: 1.9,
    scale: 1.55,
    damage: 26,
    ember: 5,
    color: [0.13, 0.03, 0.022],
  );
  static const matriarch = EnemyType(
    name: 'Matriarch',
    health: 2600,
    speed: 2.3,
    scale: 3.4,
    damage: 55,
    ember: 40,
    color: [0.03, 0.03, 0.045],
  );

  static const values = [skitterer, stalker, brute, matriarch];
}

/// A killed enemy, reported to the game for drops and effects.
class Kill {
  Kill(this.x, this.z, this.type, this.dirX, this.dirZ);
  final double x, z, dirX, dirZ;
  final EnemyType type;
}

/// A landed hit, reported for impact effects.
class Hit {
  Hit(this.x, this.y, this.z, this.dirX, this.dirZ, this.amount);
  final double x, y, z, dirX, dirZ, amount;
}

/// Simulates and draws every enemy. State is structure-of-arrays and all
/// spiders render through four instanced meshes, so cost grows with the
/// instance count rather than the node count.
class Swarm {
  static const int capacity = 520;
  static const int _legs = 8;
  static const double _femur = 0.5;
  static const double _tibia = 0.74;
  static const double _stride = 0.3;
  static const double _dyingTime = 0.5;
  static const double _corpseTime = 5.0;
  static const double _sinkTime = 1.2;
  static const double _cell = 2.2;
  static const int _buckets = 4096;

  // Hip anchors and resting foot spots for the four right legs (mirrored for
  // the left), in body space at scale 1, +Z forward.
  static const _hipZ = [0.24, 0.13, 0.02, -0.08];
  static const _footAngle = [0.56, 1.15, 1.75, 2.36];
  static const _footReach = [0.92, 0.86, 0.86, 0.97];

  int count = 0;
  int alive = 0;
  final Float32List x = Float32List(capacity);
  final Float32List z = Float32List(capacity);
  final Float32List _vx = Float32List(capacity);
  final Float32List _vz = Float32List(capacity);
  final Float32List _kx = Float32List(capacity);
  final Float32List _kz = Float32List(capacity);
  final Float32List _yaw = Float32List(capacity);
  final Float32List health = Float32List(capacity);
  final Float32List _speed = Float32List(capacity);
  final Float32List _phase = Float32List(capacity);
  final Float32List _flash = Float32List(capacity);
  final Float32List _stateTime = Float32List(capacity);

  /// Seconds until an orbiting weapon may hit this enemy again.
  final Float32List contactCooldown = Float32List(capacity);
  final Uint8List _type = Uint8List(capacity);
  final Uint8List _dying = Uint8List(capacity);
  final Uint8List _legColorDirty = Uint8List(capacity);

  final Int32List _head = Int32List(_buckets);
  final Int32List _next = Int32List(capacity);

  final List<Kill> kills = [];
  final List<Hit> hits = [];

  late final InstancedMesh _bodies, _eyes, _femurs, _tibias;
  final vm.Vector2 _resolved = vm.Vector2.zero();
  final vm.Vector4 _tint = vm.Vector4.zero();
  final math.Random _rng = math.Random(4);

  EnemyType typeOf(int i) => EnemyType.values[_type[i]];
  bool isAlive(int i) => _dying[i] == 0;
  double radiusOf(int i) => typeOf(i).radius;

  void build(Scene scene) {
    final shell = PhysicallyBasedMaterial()
      ..metallicFactor = 0
      ..roughnessFactor = 0.36;
    final eyes = PhysicallyBasedMaterial()
      ..baseColorFactor = vm.Vector4(0.02, 0, 0, 1)
      ..metallicFactor = 0
      ..roughnessFactor = 0.2
      ..emissiveFactor = vm.Vector4(1, 0.08, 0.02, 1)
      ..emissiveStrength = 14;

    _bodies = _mesh(scene, 'swarm_bodies', _bodyGeometry(), shell, capacity);
    _eyes = _mesh(
      scene,
      'swarm_eyes',
      _eyeGeometry(),
      eyes,
      capacity,
      shadows: false,
    );
    _femurs = _mesh(
      scene,
      'swarm_femurs',
      _segment(_femur, 0.055, 0.04),
      shell,
      capacity * _legs,
    );
    _tibias = _mesh(
      scene,
      'swarm_tibias',
      _segment(_tibia, 0.04, 0.008),
      shell,
      capacity * _legs,
    );
  }

  InstancedMesh _mesh(
    Scene scene,
    String name,
    Geometry geometry,
    Material material,
    int instances, {
    bool shadows = true,
  }) {
    final mesh = InstancedMesh(geometry: geometry, material: material);
    final identity = vm.Matrix4.identity();
    for (var i = 0; i < instances; i++) {
      mesh.addInstance(identity);
    }
    mesh.drawSelector = (_) => MeshDrawSelection(
      instanceCount: instances == capacity ? count : count * _legs,
    );
    scene.add(
      Node(name: name)
        ..addComponent(InstancedMeshComponent(mesh))
        // Instances move every frame; a stale aggregate bound must not cull.
        ..frustumCulled = false
        ..raycastable = false
        ..shadowCastingMode = shadows
            ? ShadowCastingMode.on
            : ShadowCastingMode.off,
    );
    return mesh;
  }

  static MeshData _ellipsoid(
    vm.Vector3 center,
    vm.Vector3 radii, {
    double pitch = 0,
    int segments = 16,
    int rings = 10,
  }) {
    // Baking the stretch into the vertices keeps instance scale uniform,
    // which lit meshes need for correct normals.
    return SphereGeometry(
      radius: 1,
      segments: segments,
      rings: rings,
    ).extractMeshData().transformed(
      vm.Matrix4.compose(
        center,
        vm.Quaternion.axisAngle(vm.Vector3(1, 0, 0), pitch),
        radii,
      ),
    );
  }

  static Geometry _bodyGeometry() {
    final parts = <MeshData>[
      _ellipsoid(vm.Vector3(0, 0.27, 0.1), vm.Vector3(0.17, 0.115, 0.22)),
      _ellipsoid(
        vm.Vector3(0, 0.35, -0.38),
        vm.Vector3(0.23, 0.19, 0.3),
        pitch: -0.22,
      ),
      _ellipsoid(vm.Vector3(0, 0.27, -0.1), vm.Vector3(0.07, 0.06, 0.1)),
      _ellipsoid(vm.Vector3(0, 0.36, -0.69), vm.Vector3(0.05, 0.05, 0.07)),
    ];
    for (final side in [-1.0, 1.0]) {
      parts
        ..add(
          CylinderGeometry(
            bottomRadius: 0.006,
            topRadius: 0.04,
            height: 0.16,
            radialSegments: 8,
          ).extractMeshData().transformed(
            vm.Matrix4.compose(
              vm.Vector3(side * 0.055, 0.2, 0.31),
              vm.Quaternion.axisAngle(vm.Vector3(1, 0, 0), -0.5),
              vm.Vector3.all(1),
            ),
          ),
        )
        ..add(
          _ellipsoid(
            vm.Vector3(side * 0.11, 0.22, 0.3),
            vm.Vector3(0.028, 0.028, 0.1),
            pitch: 0.5,
            segments: 8,
            rings: 6,
          ),
        );
    }
    return MeshGeometry.fromMeshData(MeshData.merge(parts));
  }

  static Geometry _eyeGeometry() {
    const spots = [
      [0.035, 0.335, 0.285, 0.026],
      [0.09, 0.32, 0.25, 0.02],
      [0.055, 0.365, 0.235, 0.016],
    ];
    return MeshGeometry.fromMeshData(
      MeshData.merge([
        for (final s in spots)
          for (final side in [-1.0, 1.0])
            _ellipsoid(
              vm.Vector3(side * s[0], s[1], s[2]),
              vm.Vector3.all(s[3]),
              segments: 8,
              rings: 6,
            ),
      ]),
    );
  }

  /// A tapered limb from the origin along +Y.
  static Geometry _segment(double length, double base, double tip) {
    return MeshGeometry.fromMeshData(
      CylinderGeometry(
        bottomRadius: base,
        topRadius: tip,
        height: length,
        radialSegments: 6,
      ).extractMeshData().transformed(
        vm.Matrix4.translation(vm.Vector3(0, length / 2, 0)),
      ),
    );
  }

  void clear() {
    count = 0;
    alive = 0;
    kills.clear();
    hits.clear();
  }

  void spawn(EnemyType type, double px, double pz, double healthScale) {
    if (count >= capacity && !_evictCorpse()) return;
    final i = count++;
    alive++;
    x[i] = px;
    z[i] = pz;
    _vx[i] = _vz[i] = _kx[i] = _kz[i] = 0;
    _yaw[i] = 0;
    health[i] = type.health * healthScale;
    _speed[i] = type.speed * (0.9 + _rng.nextDouble() * 0.2);
    _phase[i] = _rng.nextDouble();
    _flash[i] = 0;
    _stateTime[i] = 0;
    contactCooldown[i] = 0;
    _type[i] = EnemyType.values.indexOf(type);
    _dying[i] = 0;
    _legColorDirty[i] = 1;
  }

  bool _evictCorpse() {
    for (var i = 0; i < count; i++) {
      if (_dying[i] == 1) {
        _removeAt(i);
        return true;
      }
    }
    return false;
  }

  void _removeAt(int i) {
    final last = --count;
    if (i == last) return;
    x[i] = x[last];
    z[i] = z[last];
    _vx[i] = _vx[last];
    _vz[i] = _vz[last];
    _kx[i] = _kx[last];
    _kz[i] = _kz[last];
    _yaw[i] = _yaw[last];
    health[i] = health[last];
    _speed[i] = _speed[last];
    _phase[i] = _phase[last];
    _flash[i] = _flash[last];
    _stateTime[i] = _stateTime[last];
    contactCooldown[i] = contactCooldown[last];
    _type[i] = _type[last];
    _dying[i] = _dying[last];
    _legColorDirty[i] = 1;
  }

  /// Damages enemy [i], pushing it along ([dirX], [dirZ]) by [knockback].
  void damage(
    int i,
    double amount,
    double dirX,
    double dirZ, {
    double knockback = 0,
  }) {
    if (_dying[i] == 1) return;
    final type = typeOf(i);
    health[i] -= amount;
    _flash[i] = 0.12;
    final push = knockback / (type.scale * type.scale);
    _kx[i] += dirX * push;
    _kz[i] += dirZ * push;
    hits.add(Hit(x[i], 0.3 * type.scale, z[i], dirX, dirZ, amount));
    if (health[i] <= 0) {
      _dying[i] = 1;
      _stateTime[i] = 0;
      alive--;
      kills.add(Kill(x[i], z[i], type, dirX, dirZ));
    }
  }

  static int _bucket(int cx, int cz) =>
      ((cx * 73856093) ^ (cz * 19349663)) & (_buckets - 1);

  void _rebuildGrid() {
    _head.fillRange(0, _buckets, -1);
    for (var i = 0; i < count; i++) {
      if (_dying[i] == 1) continue;
      final b = _bucket((x[i] / _cell).floor(), (z[i] / _cell).floor());
      _next[i] = _head[b];
      _head[b] = i;
    }
  }

  /// Calls [visit] for each living enemy whose body overlaps the circle.
  /// Valid after [update] has run for the frame.
  void forEachInRadius(
    double px,
    double pz,
    double radius,
    void Function(int i) visit,
  ) {
    // The Matriarch is wider than a cell, so pad the search by her radius.
    final reach = radius + EnemyType.matriarch.radius;
    final c0 = ((px - reach) / _cell).floor(),
        c1 = ((px + reach) / _cell).floor();
    final r0 = ((pz - reach) / _cell).floor(),
        r1 = ((pz + reach) / _cell).floor();
    if ((c1 - c0 + 1) * (r1 - r0 + 1) > 64) {
      for (var i = 0; i < count; i++) {
        if (_dying[i] == 0 && _within(i, px, pz, radius)) visit(i);
      }
      return;
    }
    for (var r = r0; r <= r1; r++) {
      for (var c = c0; c <= c1; c++) {
        for (var i = _head[_bucket(c, r)]; i >= 0; i = _next[i]) {
          // Buckets can alias distant cells; the distance test filters those.
          if ((x[i] / _cell).floor() == c &&
              (z[i] / _cell).floor() == r &&
              _within(i, px, pz, radius)) {
            visit(i);
          }
        }
      }
    }
  }

  bool _within(int i, double px, double pz, double radius) {
    final dx = x[i] - px, dz = z[i] - pz, rr = radius + radiusOf(i);
    return dx * dx + dz * dz <= rr * rr;
  }

  /// The closest living enemy within [range], skipping [exclude], or -1.
  int nearest(double px, double pz, double range, {Set<int>? exclude}) {
    var best = -1;
    var bestD = range * range;
    for (var i = 0; i < count; i++) {
      if (_dying[i] == 1 || (exclude != null && exclude.contains(i))) continue;
      final dx = x[i] - px, dz = z[i] - pz;
      final d = dx * dx + dz * dz;
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    }
    return best;
  }

  void update(double dt, Player player, World world) {
    final px = player.position.x, pz = player.position.y;
    var i = 0;
    while (i < count) {
      _stateTime[i] += dt;
      _flash[i] = math.max(0, _flash[i] - dt);
      if (_dying[i] == 1) {
        if (_stateTime[i] > _corpseTime + _sinkTime) {
          _removeAt(i);
          continue;
        }
        i++;
        continue;
      }
      final type = typeOf(i);
      var dx = px - x[i], dz = pz - z[i];
      final d = math.sqrt(dx * dx + dz * dz) + 1e-6;
      dx /= d;
      dz /= d;
      final blend = 1 - math.exp(-5 * dt);
      _vx[i] += (dx * _speed[i] - _vx[i]) * blend;
      _vz[i] += (dz * _speed[i] - _vz[i]) * blend;
      final decay = math.exp(-7 * dt);
      _kx[i] *= decay;
      _kz[i] *= decay;
      x[i] += (_vx[i] + _kx[i]) * dt;
      z[i] += (_vz[i] + _kz[i]) * dt;

      final reach = type.radius + Player.radius;
      if (d < reach) {
        if (player.alive) player.hurt(type.damage * dt);
        x[i] = px - dx * reach;
        z[i] = pz - dz * reach;
      }
      if (x[i] * x[i] + z[i] * z[i] < World.arenaRadius * World.arenaRadius) {
        world.resolve(x[i], z[i], type.radius * 0.55, _resolved);
        x[i] = _resolved.x;
        z[i] = _resolved.y;
      }

      final speed = math.sqrt(_vx[i] * _vx[i] + _vz[i] * _vz[i]);
      if (speed > 0.2) {
        final turn = wrapAngle(math.atan2(_vx[i], _vz[i]) - _yaw[i]);
        _yaw[i] += turn * (1 - math.exp(-9 * dt));
      }
      _phase[i] = (_phase[i] + speed * dt / (2 * _stride * type.scale)) % 1;
      contactCooldown[i] = math.max(0, contactCooldown[i] - dt);
      i++;
    }

    _rebuildGrid();
    _separate();
    _rebuildGrid();
  }

  void _separate() {
    for (var i = 0; i < count; i++) {
      if (_dying[i] == 1) continue;
      final ri = radiusOf(i);
      final cx = (x[i] / _cell).floor(), cz = (z[i] / _cell).floor();
      final span = ri > _cell * 0.5 ? 2 : 1;
      for (var r = cz - span; r <= cz + span; r++) {
        for (var c = cx - span; c <= cx + span; c++) {
          for (var j = _head[_bucket(c, r)]; j >= 0; j = _next[j]) {
            if (j <= i) continue;
            final dx = x[j] - x[i], dz = z[j] - z[i];
            final min = (ri + radiusOf(j)) * 0.9;
            final d2 = dx * dx + dz * dz;
            if (d2 >= min * min || d2 < 1e-8) continue;
            final d = math.sqrt(d2);
            // Heavier bodies yield less.
            final mi = ri * ri, mj = radiusOf(j) * radiusOf(j);
            final push = (min - d) / d;
            final wi = mj / (mi + mj), wj = mi / (mi + mj);
            x[i] -= dx * push * wi;
            z[i] -= dz * push * wi;
            x[j] += dx * push * wj;
            z[j] += dz * push * wj;
          }
        }
      }
    }
  }

  /// Writes this frame's poses into the instance buffers.
  void render() {
    if (count == 0) return;
    // Poses never mirror, so the per-instance winding check is skipped.
    _bodies.updateInstanceTransforms((bodies) {
      _eyes.updateInstanceTransforms((eyes) {
        _femurs.updateInstanceTransforms((femurs) {
          _tibias.updateInstanceTransforms((tibias) {
            for (var i = 0; i < count; i++) {
              _pose(i, bodies[i], femurs, tibias);
              eyes[i].setFrom(bodies[i]);
            }
          }, recomputeWinding: false);
        }, recomputeWinding: false);
      }, recomputeWinding: false);
    }, recomputeWinding: false);

    for (var i = 0; i < count; i++) {
      final c = typeOf(i).color;
      final flash = _flash[i] > 0 ? 1.0 : 0.0;
      _tint.setValues(
        c[0] + flash * 0.9,
        c[1] + flash * 0.55,
        c[2] + flash * 0.4,
        1,
      );
      _bodies.setInstanceColor(i, _tint);
      if (_legColorDirty[i] == 1) {
        _legColorDirty[i] = 0;
        _tint.setValues(c[0], c[1], c[2], 1);
        for (var leg = 0; leg < _legs; leg++) {
          _femurs.setInstanceColor(i * _legs + leg, _tint);
          _tibias.setInstanceColor(i * _legs + leg, _tint);
        }
      }
    }
  }

  // Scratch for [_pose].
  final Float64List _m = Float64List(12);
  final Float64List _pt = Float64List(9);

  void _pose(
    int i,
    vm.Matrix4 body,
    List<vm.Matrix4> femurs,
    List<vm.Matrix4> tibias,
  ) {
    final type = typeOf(i);
    final s = type.scale;
    final dying = _dying[i] == 1;
    final t = _stateTime[i];

    // Flip onto the back while dying, then sink once the corpse has aged.
    final curl = dying ? math.min(1.0, t / _dyingTime) : 0.0;
    final ease = curl * curl * (3 - 2 * curl);
    final roll = ease * math.pi;
    var lift = dying
        ? math.sin(curl * math.pi) * 0.35 * s + ease * 0.62 * s
        : math.sin(_phase[i] * math.pi * 4) * 0.012 * s;
    if (dying && t > _corpseTime) {
      lift -= (t - _corpseTime) / _sinkTime * 0.9 * s;
    }

    final cy = math.cos(_yaw[i]), sy = math.sin(_yaw[i]);
    final cr = math.cos(roll), sr = math.sin(roll);
    // Columns of yaw * roll, scaled: the body's world basis.
    final m = _m;
    m[0] = cy * cr * s;
    m[1] = sr * s;
    m[2] = -sy * cr * s;
    m[3] = -cy * sr * s;
    m[4] = cr * s;
    m[5] = sy * sr * s;
    m[6] = sy * s;
    m[7] = 0;
    m[8] = cy * s;
    m[9] = x[i];
    m[10] = lift;
    m[11] = z[i];
    body.setValues(
      m[0],
      m[1],
      m[2],
      0,
      m[3],
      m[4],
      m[5],
      0,
      m[6],
      m[7],
      m[8],
      0,
      m[9],
      m[10],
      m[11],
      1,
    );

    for (var leg = 0; leg < _legs; leg++) {
      final pair = leg >> 1;
      final side = leg.isEven ? 1.0 : -1.0;
      final hx = side * 0.15, hy = 0.27, hz = _hipZ[pair];
      var fx = side * _footReach[pair] * math.sin(_footAngle[pair]);
      var fy = 0.0;
      var fz = _footReach[pair] * math.cos(_footAngle[pair]) + 0.05;

      // Alternating tetrapod gait: diagonal legs share a phase.
      final p = (_phase[i] + ((pair + (leg & 1)).isEven ? 0.0 : 0.5)) % 1;
      if (p < 0.5) {
        fz += _stride * (0.5 - p * 2);
      } else {
        final swing = (p - 0.5) * 2;
        fz += _stride * (swing - 0.5);
        fy = math.sin(swing * math.pi) * 0.16;
      }
      if (dying) {
        // Legs fold in over the belly, which faces up once the body rolls.
        fx += (side * 0.22 - fx) * ease;
        fy += (-0.18 - fy) * ease;
        fz += (hz * 0.6 + 0.05 - fz) * ease;
      }

      var dx = fx - hx, dy = fy - hy, dz = fz - hz;
      var d = math.sqrt(dx * dx + dy * dy + dz * dz);
      final reach = (_femur + _tibia) * 0.985;
      if (d > reach) {
        fx = hx + dx / d * reach;
        fy = hy + dy / d * reach;
        fz = hz + dz / d * reach;
        d = reach;
      }
      dx = (fx - hx) / d;
      dy = (fy - hy) / d;
      dz = (fz - hz) / d;
      // Two-bone IK: the knee sits on the circle where both limbs reach,
      // bent toward body-up (body-down once it lies on its back).
      final a = (_femur * _femur - _tibia * _tibia + d * d) / (2 * d);
      final h =
          math.sqrt(math.max(0.0, _femur * _femur - a * a)) * (1 - 2 * ease);
      var ux = -dy * dx, uy = 1 - dy * dy, uz = -dy * dz;
      final ul = math.sqrt(ux * ux + uy * uy + uz * uz);
      if (ul < 1e-4) {
        ux = side;
        uy = 0;
        uz = 0;
      } else {
        ux /= ul;
        uy /= ul;
        uz /= ul;
      }
      final kx = hx + dx * a + ux * h;
      final ky = hy + dy * a + uy * h;
      final kz = hz + dz * a + uz * h;

      final pt = _pt;
      _toWorld(hx, hy, hz, 0);
      _toWorld(kx, ky, kz, 3);
      _toWorld(fx, fy, fz, 6);
      final index = i * _legs + leg;
      _limb(femurs[index], pt[0], pt[1], pt[2], pt[3], pt[4], pt[5], _femur, s);
      _limb(tibias[index], pt[3], pt[4], pt[5], pt[6], pt[7], pt[8], _tibia, s);
    }
  }

  void _toWorld(double lx, double ly, double lz, int o) {
    final m = _m;
    _pt[o] = m[0] * lx + m[3] * ly + m[6] * lz + m[9];
    _pt[o + 1] = m[1] * lx + m[4] * ly + m[7] * lz + m[10];
    _pt[o + 2] = m[2] * lx + m[5] * ly + m[8] * lz + m[11];
  }

  /// Aims a limb of rest [length] from a to b. The points are already in
  /// world units, so the Y column carries the scale.
  static void _limb(
    vm.Matrix4 out,
    double ax,
    double ay,
    double az,
    double bx,
    double by,
    double bz,
    double length,
    double scale,
  ) {
    final yx = (bx - ax) / length,
        yy = (by - ay) / length,
        yz = (bz - az) / length;
    // X = up cross Y (falling back to world X when the limb is vertical),
    // Z = X cross Y, which keeps the basis right-handed.
    var xx = yz, xy = 0.0, xz = -yx;
    var xl = math.sqrt(xx * xx + xz * xz);
    if (xl < 1e-5) {
      xx = 1;
      xz = 0;
      xl = 1;
    }
    xx = xx / xl * scale;
    xz = xz / xl * scale;
    // X and Y both carry the scale, so their cross product carries it twice.
    final zx = (xy * yz - xz * yy) / scale;
    final zy = (xz * yx - xx * yz) / scale;
    final zz = (xx * yy - xy * yx) / scale;
    out.setValues(xx, xy, xz, 0, yx, yy, yz, 0, zx, zy, zz, 0, ax, ay, az, 1);
  }
}
