import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter_scene/noise.dart';
import 'package:flutter_scene/scene.dart';
import 'package:vector_math/vector_math.dart' as vm;

import 'quality.dart';

/// A circular blocker on the ground plane.
class Obstacle {
  const Obstacle(this.x, this.z, this.radius);
  final double x, z, radius;
}

class _Template {
  _Template(this.name, this.geometry, this.material, this.base);
  final String name;
  final Geometry geometry;
  final Material material;
  final vm.Matrix4 base;

  double get footprint {
    final b = geometry.localBounds;
    if (b == null) return 0.5;
    return (b.max.x - b.min.x + b.max.z - b.min.z) * 0.25;
  }
}

/// The moonlit clearing: ground, props, lighting, and the look.
class World {
  static const double arenaRadius = 72;
  static const double _groundHalf = 150;
  static const double _groundCell = 1.5;
  static const double _groundTile = 6.5;
  static const double _gridCell = 4;

  static final int _gridSize = (arenaRadius * 2 / _gridCell).ceil() + 2;

  final List<Obstacle> obstacles = [];
  final List<List<int>> _grid = List.generate(
    _gridSize * _gridSize,
    (_) => <int>[],
  );

  final vm.Vector3 moonDirection = vm.Vector3(-0.52, -0.62, 0.58).normalized();

  Future<void> build(Scene scene) async {
    final rng = math.Random(20261109);
    await _buildLook(scene);
    scene.add(await _buildGround());
    await _scatterProps(scene, rng);
    _buildGrid();
  }

  Future<void> _buildLook(Scene scene) async {
    await scene.loadEnvironment(
      'assets/env/night_sky.hdr',
      rotationY: 2.2,
      maxWidth: 1024,
    );
    scene.directionalLight = DirectionalLight(
      direction: moonDirection,
      color: vm.Vector3(0.56, 0.7, 1.0),
      intensity: 2.6,
      castsShadow: true,
      shadowMaxDistance: 70,
      shadowCascadeCount: 3,
      shadowMapResolution: 2048,
      shadowSoftness: 0.12,
      shadowFilter: DirectionalShadowFilter.pcss,
    );
    _environment = scene.environment;
    _skybox = scene.skybox;
  }

  EnvironmentMap? _environment;
  Skybox? _skybox;
  final List<Node> _detail = [];

  /// Sets shadow, post-processing and clutter cost for [quality].
  void applyQuality(Scene scene, Quality quality) {
    final high = quality == Quality.high, low = quality == Quality.low;
    scene.renderQuality
      ..tier = switch (quality) {
        Quality.low => RenderQualityTier.low,
        Quality.medium => RenderQualityTier.medium,
        Quality.high => RenderQualityTier.high,
      }
      // Trades resolution for frame rate when a device falls behind.
      ..adaptive = true
      ..minRenderScale = low ? 0.6 : 0.7;
    scene.directionalLight!
      ..shadowMapResolution = high ? 2048 : 1024
      ..shadowCascadeCount = high ? 3 : 2
      ..shadowMaxDistance = high ? 70 : 40
      ..shadowFilter = high
          ? DirectionalShadowFilter.pcss
          : DirectionalShadowFilter.rotatedPoisson;
    for (final node in _detail) {
      node.visible = !low;
    }
    scene.environmentSettings = EnvironmentSettings(
      environment: _environment,
      skybox: _skybox,
      environmentIntensity: 1.4,
      toneMapping: ToneMappingMode.aces,
      exposure: 1.0,
      colorGradingEnabled: true,
      contrast: 1.1,
      saturation: 1.0,
      temperature: -0.05,
      fogEnabled: true,
      fogMode: FogMode.exponential,
      fogColor: vm.Vector3(0.018, 0.03, 0.05),
      fogDensity: 0.022,
      fogHeight: 0,
      fogHeightFalloff: 0.12,
      fogSunInScatter: 0.6,
      // Without ambient occlusion lit materials use the engine's lean
      // shader, the largest single saving on phone GPUs.
      ambientOcclusionEnabled: !low,
      ambientOcclusionMethod: high
          ? AmbientOcclusionMethod.groundTruth
          : AmbientOcclusionMethod.obscurance,
      ambientOcclusionIntensity: 1.3,
      ambientOcclusionPower: 1.8,
      ambientOcclusionRadius: 0.6,
      bloomEnabled: true,
      bloomThreshold: 1.0,
      bloomIntensity: 0.22,
      bloomScatter: 0.75,
      vignetteEnabled: true,
      vignetteIntensity: 0.55,
      vignetteRadius: 0.7,
      filmGrainEnabled: high,
      filmGrainIntensity: 0.05,
      chromaticAberrationEnabled: high,
      chromaticAberrationIntensity: 0.08,
      godRaysEnabled: high,
      godRaysIntensity: 0.5,
      godRaysDensity: 0.4,
      godRaysColor: vm.Vector3(0.6, 0.75, 1.0),
    );
  }

  Future<Node> _buildGround() async {
    const prefix = 'assets/textures/forest_floor';
    final material =
        PhysicallyBasedMaterial(
            baseColorTexture: await loadTexture('${prefix}_Diffuse.jpg'),
            normalTexture: await loadTexture('${prefix}_nor_gl.jpg'),
            metallicRoughnessTexture: await loadTexture('${prefix}_Rough.jpg'),
          )
          // The roughness map is grayscale, so its blue channel is not metalness.
          ..metallicFactor = 0
          ..roughnessFactor = 1
          ..normalScale = 1.4;

    final macro = FastNoiseLite(seed: 7)
      ..noiseType = NoiseType.openSimplex2
      ..fractalType = FractalType.fbm
      ..octaves = 4
      ..frequency = 0.035;
    final moss = FastNoiseLite(seed: 91)
      ..noiseType = NoiseType.openSimplex2
      ..fractalType = FractalType.fbm
      ..octaves = 3
      ..frequency = 0.012;

    final n = (_groundHalf * 2 / _groundCell).round() + 1;
    final positions = Float32List(n * n * 3);
    final normals = Float32List(n * n * 3);
    final uvs = Float32List(n * n * 2);
    final colors = Float32List(n * n * 4);
    final tangents = Float32List(n * n * 4);
    for (var r = 0; r < n; r++) {
      for (var c = 0; c < n; c++) {
        final i = r * n + c;
        final x = -_groundHalf + c * _groundCell;
        final z = -_groundHalf + r * _groundCell;
        positions[i * 3] = x;
        positions[i * 3 + 2] = z;
        normals[i * 3 + 1] = 1;
        uvs[i * 2] = x / _groundTile;
        uvs[i * 2 + 1] = z / _groundTile;
        tangents[i * 4] = 1;
        tangents[i * 4 + 3] = 1;
        // Large-scale tint breaks up the texture repeat seen from above.
        final shade = 0.8 + 0.42 * macro.getNoise2(x, z);
        final green = math.max(0.0, moss.getNoise2(x, z));
        colors[i * 4] = shade * (1 - 0.25 * green);
        colors[i * 4 + 1] = shade * (1 + 0.1 * green);
        colors[i * 4 + 2] = shade * (1 - 0.3 * green);
        colors[i * 4 + 3] = 1;
      }
    }
    final indices = <int>[];
    for (var r = 0; r < n - 1; r++) {
      for (var c = 0; c < n - 1; c++) {
        final v00 = r * n + c;
        final v10 = v00 + 1;
        final v01 = v00 + n;
        final v11 = v01 + 1;
        indices
          ..addAll([v00, v01, v10])
          ..addAll([v10, v01, v11]);
      }
    }
    final geometry = MeshGeometry.fromArrays(
      positions: positions,
      normals: normals,
      texCoords: uvs,
      colors: colors,
      tangents: tangents,
      indices: indices,
    );
    return Node(name: 'ground', mesh: Mesh(geometry, material))
      ..shadowCastingMode = ShadowCastingMode.off;
  }

  Future<List<_Template>> _load(String name) async {
    final root = await loadScene('assets/models/$name.glb');
    final templates = [
      for (final node in root.meshNodes)
        for (final primitive in node.mesh!.primitives)
          _Template(
            node.name,
            primitive.geometry,
            primitive.material,
            node.globalTransform.clone(),
          ),
    ]..sort((a, b) => a.name.compareTo(b.name));
    return templates;
  }

  /// Like [_instance], with the last 60% in a batch that low quality hides.
  void _instanceDetail(Scene scene, _Template t, List<vm.Matrix4> placements) {
    final core = placements.length * 2 ~/ 5;
    _instance(scene, t, placements.sublist(0, core));
    final extra = _instance(scene, t, placements.sublist(core));
    if (extra != null) _detail.add(extra);
  }

  Node? _instance(Scene scene, _Template t, List<vm.Matrix4> placements) {
    if (placements.isEmpty) return null;
    final mesh = InstancedMesh(
      geometry: t.geometry,
      material: t.material,
      cullInstances: true,
    );
    for (final placement in placements) {
      mesh.addInstance(placement * t.base);
    }
    final node = Node(name: t.name)
      ..addComponent(InstancedMeshComponent(mesh))
      ..shadowStatic = true;
    scene.add(node);
    return node;
  }

  bool _isFree(double x, double z, double radius) {
    if (x * x + z * z < (7 + radius) * (7 + radius)) return false;
    for (final o in obstacles) {
      final dx = o.x - x, dz = o.z - z, rr = o.radius + radius + 0.6;
      if (dx * dx + dz * dz < rr * rr) return false;
    }
    return true;
  }

  /// Picks a free spot between [minRadius] and [maxRadius] from the origin.
  (double, double)? _pick(
    math.Random rng,
    double radius, {
    double minRadius = 0,
    double maxRadius = arenaRadius,
  }) {
    for (var attempt = 0; attempt < 30; attempt++) {
      final angle = rng.nextDouble() * math.pi * 2;
      // sqrt keeps the density uniform over the disc.
      final t = math.sqrt(rng.nextDouble());
      final d = minRadius + (maxRadius - minRadius) * t;
      final x = math.cos(angle) * d, z = math.sin(angle) * d;
      if (_isFree(x, z, radius)) return (x, z);
    }
    return null;
  }

  static vm.Matrix4 _place(
    double x,
    double y,
    double z,
    double yaw,
    double scale, {
    double tiltX = 0,
    double tiltZ = 0,
  }) {
    final rotation =
        vm.Quaternion.axisAngle(vm.Vector3(0, 1, 0), yaw) *
        vm.Quaternion.axisAngle(vm.Vector3(1, 0, 0), tiltX) *
        vm.Quaternion.axisAngle(vm.Vector3(0, 0, 1), tiltZ);
    return vm.Matrix4.compose(
      vm.Vector3(x, y, z),
      rotation,
      vm.Vector3.all(scale),
    );
  }

  Future<void> _scatterProps(Scene scene, math.Random rng) async {
    final rocks = [...await _load('rocks_a'), ...await _load('rocks_b')];
    final boulder = (await _load('boulder')).first;
    final stumps = [...await _load('stump_a'), ...await _load('stump_b')];
    final logs = [...await _load('log_a'), ...await _load('log_b')];
    final branches = await _load('branches');
    final ferns = await _load('fern');

    final rockPlacements = [for (final _ in rocks) <vm.Matrix4>[]];
    void addRock(double x, double z, double scale, {bool blocks = true}) {
      final i = rng.nextInt(rocks.length);
      final radius = rocks[i].footprint * scale;
      if (blocks) obstacles.add(Obstacle(x, z, radius * 0.85));
      rockPlacements[i].add(
        _place(x, 0, z, rng.nextDouble() * math.pi * 2, scale),
      );
    }

    final boulderPlacements = <vm.Matrix4>[];
    for (var i = 0; i < 7; i++) {
      final scale = 1.6 + rng.nextDouble() * 1.3;
      final radius = boulder.footprint * scale;
      final spot = _pick(rng, radius, minRadius: 14);
      if (spot == null) continue;
      obstacles.add(Obstacle(spot.$1, spot.$2, radius * 0.9));
      boulderPlacements.add(
        _place(spot.$1, 0, spot.$2, rng.nextDouble() * 6.28, scale),
      );
    }

    for (var i = 0; i < 170; i++) {
      final scale = 0.45 + rng.nextDouble() * 0.9;
      final spot = _pick(rng, 1.4 * scale);
      if (spot != null) addRock(spot.$1, spot.$2, scale);
    }

    final stumpPlacements = [for (final _ in stumps) <vm.Matrix4>[]];
    for (var i = 0; i < 70; i++) {
      final t = rng.nextInt(stumps.length);
      final scale = 0.8 + rng.nextDouble() * 0.5;
      final radius = stumps[t].footprint * scale * 0.55;
      final spot = _pick(rng, radius);
      if (spot == null) continue;
      obstacles.add(Obstacle(spot.$1, spot.$2, radius));
      stumpPlacements[t].add(
        _place(spot.$1, 0, spot.$2, rng.nextDouble() * 6.28, scale),
      );
    }

    // Standing dead pines: sparse inside the clearing, a wall around it.
    final pinePlacements = <vm.Matrix4>[];
    final snagPlacements = <vm.Matrix4>[];
    void addPine(double x, double z, double scale, {bool blocks = true}) {
      if (blocks) obstacles.add(Obstacle(x, z, 0.34 * scale));
      // Full-height trunks would hide the player from the overhead camera,
      // so trees inside the clearing are broken snags.
      (blocks ? snagPlacements : pinePlacements).add(
        _place(
          x,
          (blocks ? _snagHeight : _pineHeight) * 0.5 * scale - 0.5,
          z,
          rng.nextDouble() * 6.28,
          scale,
          tiltX: (rng.nextDouble() - 0.5) * 0.14,
          tiltZ: (rng.nextDouble() - 0.5) * 0.14,
        ),
      );
    }

    for (var i = 0; i < 90; i++) {
      final spot = _pick(rng, 0.5, minRadius: 10);
      if (spot != null) {
        addPine(spot.$1, spot.$2, 0.75 + rng.nextDouble() * 0.5);
      }
    }
    for (var i = 0; i < 520; i++) {
      final angle = rng.nextDouble() * math.pi * 2;
      final d = arenaRadius + 2 + math.pow(rng.nextDouble(), 1.6) * 60;
      addPine(
        math.cos(angle) * d,
        math.sin(angle) * d,
        0.9 + rng.nextDouble() * 0.9,
        blocks: false,
      );
      if (i % 4 == 0) {
        final a = rng.nextDouble() * math.pi * 2;
        final r = arenaRadius + 1 + rng.nextDouble() * 30;
        addRock(
          math.cos(a) * r,
          math.sin(a) * r,
          0.9 + rng.nextDouble() * 1.2,
          blocks: false,
        );
      }
    }

    final logPlacements = [for (final _ in logs) <vm.Matrix4>[]];
    for (var i = 0; i < 55; i++) {
      final t = rng.nextInt(logs.length);
      final scale = 1.0 + rng.nextDouble() * 0.6;
      final spot = _pick(rng, 2.2 * scale);
      if (spot == null) continue;
      final yaw = rng.nextDouble() * math.pi * 2;
      logPlacements[t].add(_place(spot.$1, 0, spot.$2, yaw, scale));
      // The trunk lies along local X; block it with a row of circles.
      final half = 1.5 * scale;
      for (var s = -1; s <= 1; s++) {
        obstacles.add(
          Obstacle(
            spot.$1 + math.cos(yaw) * half * s * 0.8,
            spot.$2 - math.sin(yaw) * half * s * 0.8,
            0.38 * scale,
          ),
        );
      }
    }

    final clutter = FastNoiseLite(seed: 311)
      ..noiseType = NoiseType.openSimplex2
      ..frequency = 0.03;
    final branchPlacements = [for (final _ in branches) <vm.Matrix4>[]];
    for (var i = 0; i < 900; i++) {
      final angle = rng.nextDouble() * math.pi * 2;
      final d = math.sqrt(rng.nextDouble()) * (arenaRadius + 20);
      branchPlacements[rng.nextInt(branches.length)].add(
        _place(
          math.cos(angle) * d,
          0.02,
          math.sin(angle) * d,
          rng.nextDouble() * 6.28,
          1.0 + rng.nextDouble() * 0.8,
        ),
      );
    }
    final fernPlacements = [for (final _ in ferns) <vm.Matrix4>[]];
    var placed = 0;
    for (var attempt = 0; attempt < 30000 && placed < 3200; attempt++) {
      final angle = rng.nextDouble() * math.pi * 2;
      final d = math.sqrt(rng.nextDouble()) * (arenaRadius + 25);
      final x = math.cos(angle) * d, z = math.sin(angle) * d;
      // Ferns grow in patches, not evenly.
      if (clutter.getNoise2(x, z) < -0.1 || x * x + z * z < 9) continue;
      fernPlacements[rng.nextInt(ferns.length)].add(
        _place(x, 0, z, rng.nextDouble() * 6.28, 0.8 + rng.nextDouble() * 0.9),
      );
      placed++;
    }

    for (var i = 0; i < rocks.length; i++) {
      _instance(scene, rocks[i], rockPlacements[i]);
    }
    _instance(scene, boulder, boulderPlacements);
    for (var i = 0; i < stumps.length; i++) {
      _instance(scene, stumps[i], stumpPlacements[i]);
    }
    for (var i = 0; i < logs.length; i++) {
      _instance(scene, logs[i], logPlacements[i]);
    }
    for (var i = 0; i < branches.length; i++) {
      _instanceDetail(scene, branches[i], branchPlacements[i]);
    }
    for (var i = 0; i < ferns.length; i++) {
      _instanceDetail(scene, ferns[i], fernPlacements[i]);
    }
    final bark = await _barkMaterial();
    _instance(scene, _trunk(bark, _pineHeight, 0.1), pinePlacements);
    _instance(scene, _trunk(bark, _snagHeight, 0.24), snagPlacements);
  }

  static const double _pineHeight = 13;
  static const double _snagHeight = 2.3;

  _Template _trunk(Material bark, double height, double topRadius) {
    final geometry = CylinderGeometry(
      bottomRadius: 0.34,
      topRadius: topRadius,
      height: height,
      radialSegments: 12,
      heightSegments: 3,
    );
    return _Template('dead_pine', geometry, bark, vm.Matrix4.identity());
  }

  Future<Material> _barkMaterial() async {
    const prefix = 'assets/textures/bark';
    final scale = vm.Vector2(2, 6);
    return PhysicallyBasedMaterial(
        baseColorTexture: await loadTexture('${prefix}_Diffuse.jpg'),
        normalTexture: await loadTexture('${prefix}_nor_gl.jpg'),
        metallicRoughnessTexture: await loadTexture('${prefix}_Rough.jpg'),
      )
      ..metallicFactor = 0
      ..baseColorFactor = vm.Vector4(0.75, 0.72, 0.7, 1)
      ..baseColorTextureTransform = TextureTransform(scale: scale)
      ..normalTextureTransform = TextureTransform(scale: scale)
      ..metallicRoughnessTextureTransform = TextureTransform(scale: scale);
  }

  void _buildGrid() {
    for (var i = 0; i < obstacles.length; i++) {
      final o = obstacles[i];
      final c0 = _cell(o.x - o.radius), c1 = _cell(o.x + o.radius);
      final r0 = _cell(o.z - o.radius), r1 = _cell(o.z + o.radius);
      for (var r = r0; r <= r1; r++) {
        for (var c = c0; c <= c1; c++) {
          _grid[r * _gridSize + c].add(i);
        }
      }
    }
  }

  int _cell(double v) =>
      ((v + arenaRadius) / _gridCell + 1).floor().clamp(0, _gridSize - 1);

  /// Pushes a circle at ([x], [z]) out of every obstacle it overlaps and
  /// writes the corrected position to [out].
  void resolve(double x, double z, double radius, vm.Vector2 out) {
    final c0 = _cell(x - radius), c1 = _cell(x + radius);
    final r0 = _cell(z - radius), r1 = _cell(z + radius);
    for (var r = r0; r <= r1; r++) {
      for (var c = c0; c <= c1; c++) {
        for (final i in _grid[r * _gridSize + c]) {
          final o = obstacles[i];
          final dx = x - o.x, dz = z - o.z;
          final min = o.radius + radius;
          final d2 = dx * dx + dz * dz;
          if (d2 >= min * min || d2 == 0) continue;
          final d = math.sqrt(d2);
          x = o.x + dx / d * min;
          z = o.z + dz / d * min;
        }
      }
    }
    out.setValues(x, z);
  }
}
