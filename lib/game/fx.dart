import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter_scene/scene.dart';
import 'package:vector_math/vector_math.dart' as vm;

enum _Layer { glow, streak, smoke }

class _Sprites {
  _Sprites(
    Scene scene,
    String name,
    this.capacity,
    SpriteMaterial material, {
    BillboardFacing facing = BillboardFacing.spherical,
  }) : geometry = BillboardGeometry(capacity: capacity) {
    geometry
      ..facing = facing
      ..velocityStretch = 1;
    scene.add(
      Node(name: name, mesh: Mesh(geometry, material))
        ..frustumCulled = false
        ..shadowCastingMode = ShadowCastingMode.off
        ..raycastable = false,
    );
  }

  final int capacity;
  final BillboardGeometry geometry;
  int count = 0;

  void add(
    double x,
    double y,
    double z,
    double width,
    double height,
    double r,
    double g,
    double b,
    double a, {
    double vx = 0,
    double vy = 0,
    double vz = 0,
    double rotation = 0,
  }) {
    if (count >= capacity) return;
    final d = geometry.instanceData;
    final o = count * BillboardGeometry.floatsPerInstance;
    d[o] = x;
    d[o + 1] = y;
    d[o + 2] = z;
    d[o + 3] = width;
    d[o + 4] = height;
    d[o + 5] = rotation;
    d[o + 6] = r;
    d[o + 7] = g;
    d[o + 8] = b;
    d[o + 9] = a;
    d[o + 10] = 0;
    d[o + 11] = vx;
    d[o + 12] = vy;
    d[o + 13] = vz;
    count++;
  }

  void commit() {
    geometry.commit(count);
    count = 0;
  }
}

class _Bolt {
  _Bolt(this.a, this.b, this.life);
  final vm.Vector3 a, b;
  double life;
}

class _Flash {
  _Flash(this.node, this.light);
  final Node node;
  final PointLight light;
  double life = 0, duration = 1, peak = 0;
}

/// Sprites, transient lights, lightning, and ground decals. Sprite buffers
/// are rebuilt every frame: simulated particles first, then anything pushed
/// through [glow] / [streak] since the last [update].
class Fx {
  static const int _maxParticles = 2600;
  static const int _maxDecals = 360;

  final math.Random _rng = math.Random(99);
  late final _Sprites _glow, _streak, _smoke;
  final List<_Bolt> _bolts = [];
  final List<_Flash> _flashes = [];
  late final InstancedMesh _decals;
  int _decalCursor = 0;

  final Uint8List _layer = Uint8List(_maxParticles);
  final Float32List _p = Float32List(_maxParticles * 3);
  final Float32List _v = Float32List(_maxParticles * 3);
  final Float32List _color = Float32List(_maxParticles * 4);
  final Float32List _life = Float32List(_maxParticles);
  final Float32List _maxLife = Float32List(_maxParticles);
  final Float32List _size = Float32List(_maxParticles);
  final Float32List _grow = Float32List(_maxParticles);
  final Float32List _gravity = Float32List(_maxParticles);
  final Float32List _drag = Float32List(_maxParticles);
  int _count = 0;

  void build(Scene scene) {
    final glowTexture = _radialTexture(
      64,
      (d) => math.pow(1 - d, 2.2).toDouble(),
    );
    final smokeTexture = _radialTexture(64, (d) => (1 - d * d) * (1 - d));
    _smoke = _Sprites(
      scene,
      'fx_smoke',
      700,
      SpriteMaterial(colorTexture: smokeTexture)..softDepthFade = 0.6,
    );
    _glow = _Sprites(
      scene,
      'fx_glow',
      2400,
      SpriteMaterial(colorTexture: glowTexture)
        ..blendMode = SpriteBlendMode.additive
        ..softDepthFade = 0.25,
    );
    _streak = _Sprites(
      scene,
      'fx_streak',
      2000,
      SpriteMaterial(colorTexture: glowTexture)
        ..blendMode = SpriteBlendMode.additive,
      facing: BillboardFacing.velocityStretched,
    );

    // Every pooled light counts toward the per-object light limit even
    // while dark, so the pool stays small.
    for (var i = 0; i < 6; i++) {
      final light = PointLight(intensity: 0, range: 14);
      final node = Node(name: 'fx_light_$i')
        ..addComponent(PointLightComponent(light));
      scene.add(node);
      _flashes.add(_Flash(node, light));
    }

    final splat = PhysicallyBasedMaterial(baseColorTexture: _splatTexture())
      ..alphaMode = AlphaMode.blend
      ..metallicFactor = 0
      ..roughnessFactor = 0.22
      ..depthLayer = 1;
    _decals = InstancedMesh(
      geometry: PlaneGeometry(),
      material: splat,
      sortTransparentInstances: false,
    );
    final hidden = vm.Matrix4.translation(vm.Vector3(0, -50, 0));
    for (var i = 0; i < _maxDecals; i++) {
      _decals.addInstance(hidden);
    }
    scene.add(
      Node(name: 'fx_decals')
        ..addComponent(InstancedMeshComponent(_decals))
        ..frustumCulled = false
        ..shadowCastingMode = ShadowCastingMode.off
        ..raycastable = false,
    );
  }

  static Texture2D _radialTexture(int size, double Function(double d) alpha) {
    final pixels = Uint8List(size * size * 4);
    final half = (size - 1) / 2;
    for (var y = 0; y < size; y++) {
      for (var x = 0; x < size; x++) {
        final dx = (x - half) / half, dy = (y - half) / half;
        final d = math.min(1.0, math.sqrt(dx * dx + dy * dy));
        final o = (y * size + x) * 4;
        pixels[o] = pixels[o + 1] = pixels[o + 2] = 255;
        pixels[o + 3] = (alpha(d).clamp(0.0, 1.0) * 255).round();
      }
    }
    return Texture2D.fromPixels(pixels, size, size);
  }

  /// An irregular blot with satellite droplets, alpha only.
  static Texture2D _splatTexture() {
    const size = 128;
    final rng = math.Random(5);
    final alpha = Float32List(size * size);
    void blot(double cx, double cy, double radius) {
      final r0 = math.max(0, (cy - radius).floor());
      final r1 = math.min(size - 1, (cy + radius).ceil());
      final c0 = math.max(0, (cx - radius).floor());
      final c1 = math.min(size - 1, (cx + radius).ceil());
      for (var y = r0; y <= r1; y++) {
        for (var x = c0; x <= c1; x++) {
          final d = math.sqrt((x - cx) * (x - cx) + (y - cy) * (y - cy));
          final a = ((radius - d) / 2.5).clamp(0.0, 1.0);
          final i = y * size + x;
          if (a > alpha[i]) alpha[i] = a;
        }
      }
    }

    for (var i = 0; i < 26; i++) {
      final angle = rng.nextDouble() * math.pi * 2;
      final d = rng.nextDouble() * 22;
      blot(
        64 + math.cos(angle) * d,
        64 + math.sin(angle) * d,
        8 + rng.nextDouble() * 14,
      );
    }
    for (var i = 0; i < 40; i++) {
      final angle = rng.nextDouble() * math.pi * 2;
      final d = 30 + rng.nextDouble() * 28;
      blot(
        64 + math.cos(angle) * d,
        64 + math.sin(angle) * d,
        1.5 + rng.nextDouble() * 4,
      );
    }
    final pixels = Uint8List(size * size * 4);
    for (var i = 0; i < size * size; i++) {
      pixels[i * 4] = pixels[i * 4 + 1] = pixels[i * 4 + 2] = 255;
      pixels[i * 4 + 3] = (alpha[i] * 235).round();
    }
    return Texture2D.fromPixels(pixels, size, size);
  }

  void clear() {
    _count = 0;
    _bolts.clear();
    for (final flash in _flashes) {
      flash.life = 0;
      flash.light.intensity = 0;
    }
    final hidden = vm.Matrix4.translation(vm.Vector3(0, -50, 0));
    for (var i = 0; i < _maxDecals; i++) {
      _decals.setInstanceTransform(i, hidden);
    }
  }

  void _spawn(
    _Layer layer,
    double x,
    double y,
    double z,
    double vx,
    double vy,
    double vz,
    double life,
    double size,
    double grow,
    double r,
    double g,
    double b,
    double a, {
    double gravity = 0,
    double drag = 0,
  }) {
    if (_count >= _maxParticles) return;
    final i = _count++;
    _layer[i] = layer.index;
    _p[i * 3] = x;
    _p[i * 3 + 1] = y;
    _p[i * 3 + 2] = z;
    _v[i * 3] = vx;
    _v[i * 3 + 1] = vy;
    _v[i * 3 + 2] = vz;
    _life[i] = life;
    _maxLife[i] = life;
    _size[i] = size;
    _grow[i] = grow;
    _color[i * 4] = r;
    _color[i * 4 + 1] = g;
    _color[i * 4 + 2] = b;
    _color[i * 4 + 3] = a;
    _gravity[i] = gravity;
    _drag[i] = drag;
  }

  double _spread(double amount) => (_rng.nextDouble() - 0.5) * 2 * amount;

  /// A glow sprite that lasts one frame. Colors above 1 bloom.
  void glow(
    double x,
    double y,
    double z,
    double size,
    double r,
    double g,
    double b,
  ) {
    _glow.add(x, y, z, size, size, r, g, b, 1);
  }

  /// A one-frame glowing line from [a] to [b].
  void streak(
    vm.Vector3 a,
    vm.Vector3 b,
    double width,
    double r,
    double g,
    double bl,
  ) {
    _streak.add(
      (a.x + b.x) / 2,
      (a.y + b.y) / 2,
      (a.z + b.z) / 2,
      width,
      0,
      r,
      g,
      bl,
      1,
      vx: b.x - a.x,
      vy: b.y - a.y,
      vz: b.z - a.z,
    );
  }

  void sparks(
    vm.Vector3 at,
    int count,
    double speed,
    double r,
    double g,
    double b, {
    vm.Vector3? direction,
    double life = 0.35,
  }) {
    for (var i = 0; i < count; i++) {
      final s = speed * (0.35 + _rng.nextDouble() * 0.9);
      var dx = _spread(1), dy = _rng.nextDouble() * 1.1, dz = _spread(1);
      if (direction != null) {
        dx = dx * 0.6 + direction.x;
        dy = dy * 0.6 + direction.y;
        dz = dz * 0.6 + direction.z;
      }
      final n = math.sqrt(dx * dx + dy * dy + dz * dz) + 1e-6;
      _spawn(
        _Layer.streak,
        at.x,
        at.y,
        at.z,
        dx / n * s,
        dy / n * s,
        dz / n * s,
        life * (0.5 + _rng.nextDouble()),
        0.035,
        0,
        r,
        g,
        b,
        1,
        gravity: 12,
        drag: 2.5,
      );
    }
  }

  void smoke(
    vm.Vector3 at,
    int count,
    double size,
    double shade, {
    double rise = 1.2,
    double spread = 1.0,
    double life = 1.4,
    double alpha = 0.35,
  }) {
    for (var i = 0; i < count; i++) {
      _spawn(
        _Layer.smoke,
        at.x + _spread(spread * 0.4),
        at.y + _rng.nextDouble() * 0.3,
        at.z + _spread(spread * 0.4),
        _spread(spread),
        rise * (0.4 + _rng.nextDouble()),
        _spread(spread),
        life * (0.6 + _rng.nextDouble() * 0.8),
        size * (0.6 + _rng.nextDouble() * 0.7),
        size * 1.6,
        shade,
        shade,
        shade * 1.05,
        alpha,
        drag: 1.6,
      );
    }
  }

  /// Dark droplets thrown away from a hit along [dirX], [dirZ].
  void ichor(vm.Vector3 at, double dirX, double dirZ, int count) {
    for (var i = 0; i < count; i++) {
      final s = 2 + _rng.nextDouble() * 4;
      _spawn(
        _Layer.smoke,
        at.x,
        at.y,
        at.z,
        dirX * s + _spread(1.6),
        1 + _rng.nextDouble() * 3,
        dirZ * s + _spread(1.6),
        0.35 + _rng.nextDouble() * 0.3,
        0.06 + _rng.nextDouble() * 0.09,
        0,
        0.1,
        0.2,
        0.03,
        0.95,
        gravity: 14,
      );
    }
  }

  void muzzle(vm.Vector3 at, vm.Vector3 direction) {
    _spawn(_Layer.glow, at.x, at.y, at.z, 0, 0, 0, 0.05, 0.55, 0, 7, 5, 2.2, 1);
    sparks(at, 3, 9, 6, 4, 1.5, direction: direction, life: 0.12);
    flash(at, 1.0, 0.75, 0.4, 28, 0.07, range: 9);
  }

  void tracer(vm.Vector3 from, vm.Vector3 to) {
    final dx = to.x - from.x, dy = to.y - from.y, dz = to.z - from.z;
    final length = math.sqrt(dx * dx + dy * dy + dz * dz);
    if (length < 0.01) return;
    const speed = 150.0;
    const tail = 2.2;
    final life = length / speed;
    // Streak length is the velocity magnitude, so the velocity carries the
    // tail length and the travel speed is applied through `_grow`.
    final i = _count;
    _spawn(
      _Layer.streak,
      from.x,
      from.y,
      from.z,
      dx / length * tail,
      dy / length * tail,
      dz / length * tail,
      life,
      0.05,
      speed / tail,
      9,
      6,
      2.4,
      1,
    );
    if (_count > i) _drag[i] = -1;
  }

  void explosion(vm.Vector3 at, double radius) {
    _spawn(
      _Layer.glow,
      at.x,
      at.y + 0.6,
      at.z,
      0,
      0,
      0,
      0.16,
      radius * 2.6,
      radius * 6,
      12,
      7,
      2.5,
      1,
    );
    _spawn(
      _Layer.glow,
      at.x,
      at.y + 0.5,
      at.z,
      0,
      0,
      0,
      0.3,
      radius * 1.2,
      radius * 2,
      6,
      2.4,
      0.6,
      1,
    );
    sparks(at, 40, 14, 8, 4.5, 1.2, life: 0.6);
    smoke(
      at,
      12,
      radius * 0.7,
      0.07,
      rise: 2.2,
      spread: radius * 0.9,
      life: 2.2,
      alpha: 0.5,
    );
    flash(vm.Vector3(at.x, 1.2, at.z), 1.0, 0.6, 0.25, 520, 0.35, range: 20);
    decal(at.x, at.z, radius * 1.1, 0.02, 0.018, 0.016, 0.55);
  }

  void bolt(vm.Vector3 a, vm.Vector3 b) {
    _bolts.add(_Bolt(a.clone(), b.clone(), 0.14));
    flash(b, 0.5, 0.75, 1.0, 90, 0.14, range: 8);
    sparks(b, 6, 6, 3, 5, 9, life: 0.2);
  }

  void ring(double x, double z, double radius, double r, double g, double b) {
    const segments = 40;
    for (var i = 0; i < segments; i++) {
      final a = i / segments * math.pi * 2;
      _spawn(
        _Layer.glow,
        x + math.cos(a) * 0.4,
        0.25,
        z + math.sin(a) * 0.4,
        math.cos(a) * radius * 3.2,
        0,
        math.sin(a) * radius * 3.2,
        0.32,
        0.7,
        -1.2,
        r,
        g,
        b,
        1,
        drag: 1.2,
      );
    }
    smoke(
      vm.Vector3(x, 0.1, z),
      10,
      1.2,
      0.2,
      rise: 0.5,
      spread: radius * 0.8,
      life: 1.0,
      alpha: 0.18,
    );
  }

  /// Lights the surroundings briefly. Reuses the dimmest pooled light.
  void flash(
    vm.Vector3 at,
    double r,
    double g,
    double b,
    double intensity,
    double duration, {
    double range = 12,
  }) {
    // The pool is empty until the scene is built.
    if (_flashes.isEmpty) return;
    var slot = _flashes.first;
    for (final f in _flashes) {
      if (f.life <= 0) {
        slot = f;
        break;
      }
      if (f.light.intensity < slot.light.intensity) slot = f;
    }
    slot
      ..life = duration
      ..duration = duration
      ..peak = intensity;
    slot.light
      ..color = vm.Vector3(r, g, b)
      ..intensity = intensity
      ..range = range;
    slot.node.position = at;
  }

  /// Stamps a flat blot on the ground, replacing the oldest one.
  void decal(
    double x,
    double z,
    double size,
    double r,
    double g,
    double b,
    double alpha,
  ) {
    final i = _decalCursor;
    _decalCursor = (_decalCursor + 1) % _maxDecals;
    _decals
      ..setInstanceTransform(
        i,
        vm.Matrix4.compose(
          vm.Vector3(x, 0.012, z),
          vm.Quaternion.axisAngle(
            vm.Vector3(0, 1, 0),
            _rng.nextDouble() * math.pi * 2,
          ),
          vm.Vector3.all(size),
        ),
      )
      ..setInstanceColor(i, vm.Vector4(r, g, b, alpha));
  }

  void update(double dt) {
    var i = 0;
    while (i < _count) {
      _life[i] -= dt;
      if (_life[i] <= 0) {
        _removeAt(i);
        continue;
      }
      final t = 1 - _life[i] / _maxLife[i];
      final px = i * 3, c = i * 4;
      if (_drag[i] < 0) {
        // Tracers fly at constant speed; `_grow` holds the speed multiplier.
        final k = _grow[i] * dt;
        _p[px] += _v[px] * k;
        _p[px + 1] += _v[px + 1] * k;
        _p[px + 2] += _v[px + 2] * k;
        _streak.add(
          _p[px],
          _p[px + 1],
          _p[px + 2],
          _size[i],
          0,
          _color[c],
          _color[c + 1],
          _color[c + 2],
          1,
          vx: _v[px],
          vy: _v[px + 1],
          vz: _v[px + 2],
        );
        i++;
        continue;
      }
      final damp = math.exp(-_drag[i] * dt);
      _v[px] *= damp;
      _v[px + 1] = _v[px + 1] * damp - _gravity[i] * dt;
      _v[px + 2] *= damp;
      _p[px] += _v[px] * dt;
      _p[px + 1] += _v[px + 1] * dt;
      _p[px + 2] += _v[px + 2] * dt;
      if (_p[px + 1] < 0.02 && _gravity[i] > 0) {
        _p[px + 1] = 0.02;
        _v[px + 1] *= -0.3;
      }
      final fade = 1 - t;
      switch (_Layer.values[_layer[i]]) {
        case _Layer.glow:
          final size = math.max(0.0, _size[i] + _grow[i] * t * _maxLife[i]);
          _glow.add(
            _p[px],
            _p[px + 1],
            _p[px + 2],
            size,
            size,
            _color[c] * fade,
            _color[c + 1] * fade,
            _color[c + 2] * fade,
            1,
          );
        case _Layer.streak:
          const stretch = 0.035;
          _streak.add(
            _p[px],
            _p[px + 1],
            _p[px + 2],
            _size[i],
            _size[i],
            _color[c] * fade,
            _color[c + 1] * fade,
            _color[c + 2] * fade,
            1,
            vx: _v[px] * stretch,
            vy: _v[px + 1] * stretch,
            vz: _v[px + 2] * stretch,
          );
        case _Layer.smoke:
          final size = _size[i] + _grow[i] * t;
          final a =
              _color[c + 3] * math.min(1.0, fade * 2) * math.min(1.0, t * 8);
          _smoke.add(
            _p[px],
            _p[px + 1],
            _p[px + 2],
            size,
            size,
            _color[c],
            _color[c + 1],
            _color[c + 2],
            a,
            rotation: i * 1.7,
          );
      }
      i++;
    }

    _bolts.removeWhere((bolt) => (bolt.life -= dt) <= 0);
    final from = vm.Vector3.zero(), to = vm.Vector3.zero();
    for (final bolt in _bolts) {
      const segments = 7;
      final k = bolt.life / 0.14;
      from.setFrom(bolt.a);
      for (var s = 1; s <= segments; s++) {
        final t = s / segments;
        to
          ..setFrom(bolt.a)
          ..addScaled(bolt.b - bolt.a, t);
        if (s < segments) {
          to.x += _spread(0.35);
          to.y += _spread(0.25);
          to.z += _spread(0.35);
        }
        streak(from, to, 0.16, 2.5 * k, 5 * k, 10 * k);
        streak(from, to, 0.05, 9 * k, 10 * k, 12 * k);
        from.setFrom(to);
      }
    }

    for (final f in _flashes) {
      if (f.life <= 0) continue;
      f.life -= dt;
      final k = math.max(0.0, f.life / f.duration);
      f.light.intensity = f.peak * k * k;
    }

    _smoke.commit();
    _glow.commit();
    _streak.commit();
  }

  void _removeAt(int i) {
    final last = --_count;
    if (i == last) return;
    _layer[i] = _layer[last];
    _life[i] = _life[last];
    _maxLife[i] = _maxLife[last];
    _size[i] = _size[last];
    _grow[i] = _grow[last];
    _gravity[i] = _gravity[last];
    _drag[i] = _drag[last];
    for (var k = 0; k < 3; k++) {
      _p[i * 3 + k] = _p[last * 3 + k];
      _v[i * 3 + k] = _v[last * 3 + k];
    }
    for (var k = 0; k < 4; k++) {
      _color[i * 4 + k] = _color[last * 4 + k];
    }
  }
}
