import 'dart:math' as math;

import 'package:flutter/foundation.dart';
import 'package:flutter_scene/kit.dart';
import 'package:flutter_scene/scene.dart';
import 'package:vector_math/vector_math.dart' as vm;

import 'arsenal.dart';
import 'director.dart';
import 'embers.dart';
import 'fx.dart';
import '../settings.dart';
import 'player.dart';
import 'quality.dart';
import 'sfx.dart';
import 'swarm.dart';
import 'world.dart';

enum GamePhase {
  loading,

  /// Assets are in; pipelines compile behind the loading cover.
  warming,
  title,
  playing,
  levelUp,
  paused,
  dead,
  victory,
}

/// Owns the scene and all simulation state. The widget layer only forwards
/// ticks and input, and listens for HUD changes.
class Game extends ChangeNotifier {
  // Lazy, because creating a scene needs the GPU and the simulation does not.
  late final Scene scene = Scene();
  final World world = World();
  final Player player = Player();
  final Swarm swarm = Swarm();
  final Fx fx = Fx();
  final Embers embers = Embers();
  final Director director = Director();
  final Stats stats = Stats();
  final Sfx sfx = Sfx();
  final math.Random rng = math.Random();
  final CameraShake shake = CameraShake(
    decayRate: 1.8,
    frequency: 22,
    maxTranslation: vm.Vector3(0.35, 0.25, 0.35),
    maxRotation: vm.Vector3(0.025, 0.025, 0.02),
  );

  late final List<Upgrade> upgrades;
  late final Carbine _carbine;
  Settings settings = Settings.transient();

  GamePhase phase = GamePhase.loading;
  double time = 0;
  int kills = 0;
  int level = 1;
  int experience = 0;

  /// Whether the run that just ended beat the saved best time.
  bool newBest = false;

  /// The choices shown while [phase] is [GamePhase.levelUp].
  List<Upgrade> offers = const [];

  /// Movement intent from the input layer, +Y toward the top of the screen.
  final vm.Vector2 moveInput = vm.Vector2.zero();

  /// Drives a simple bot instead of [moveInput], for unattended captures.
  bool autoplay = false;

  /// Holds fire and makes the player invulnerable, to inspect the swarm.
  bool debugDisarmed = false;

  final Node _cameraNode = Node(name: 'camera');
  final vm.Vector3 _cameraFocus = vm.Vector3.zero();
  final vm.Vector3 _cameraOffset = vm.Vector3(0, 11.0, -7.8);

  /// Scales the camera's distance from the player.
  set cameraZoom(double value) =>
      _cameraOffset.setValues(0, 11.0 * value, -7.8 * value);
  double _hudTimer = 0;
  double _autoChoose = 0;

  int get experienceNeeded => (6 + 4 * level + 0.5 * level * level).round();
  Iterable<Upgrade> get owned => upgrades.where((u) => u.level > 0);
  double get nightProgress => (time / Director.nightLength).clamp(0.0, 1.0);

  Future<void> load() async {
    settings = await Settings.load();
    await Scene.initializeStaticResources();
    // The debug-build overlap scan walks every instance of every prop and
    // grows the heap by over 100 MB a second, which stalls frames for
    // hundreds of milliseconds.
    scene.debugCheckCoplanarOverlaps = false;
    await world.build(scene);
    fx.build(scene);
    swarm.build(scene);
    await player.load();
    scene.add(player.root);
    await sfx.load(scene);
    upgrades = buildUpgrades(scene);
    _carbine = upgrades.whereType<Carbine>().first;
    _cameraNode.addComponent(
      CameraComponent(
        projection: PerspectiveProjection(
          fovRadiansY: 40 * math.pi / 180,
          near: 0.3,
          far: 400,
        ),
        activateOnMount: true,
      ),
    );
    scene.add(_cameraNode);
    // Poses the player and aims the flashlight for the title screen.
    player
      ..yaw = 2.3
      ..update(0, vm.Vector2.zero(), null, world);
    _updateCamera(0, snap: true);
    sfx.muted = settings.muted;
    _applyQuality();
    phase = GamePhase.warming;
    notifyListeners();
  }

  void setQuality(Quality quality) {
    settings
      ..quality = quality
      ..save();
    _applyQuality();
    notifyListeners();
  }

  void _applyQuality() {
    world.applyQuality(scene, settings.quality);
    player.applyQuality(settings.quality);
  }

  void toggleMute() {
    settings
      ..muted = !settings.muted
      ..save();
    sfx.muted = settings.muted;
    notifyListeners();
  }

  int _warmFrames = 0;

  /// Draws one of everything for a few frames, so each render pipeline
  /// compiles under the loading cover instead of on its first use in play.
  void _warm(double dt) {
    if (_warmFrames == 0) {
      for (final (index, type) in EnemyType.values.indexed) {
        swarm.spawn(type, -4.5 + index * 3, 4, 1);
      }
      swarm
        ..spawn(EnemyType.stalker, 0, 2.5, 1)
        ..damage(swarm.count - 1, 1e6, 0, 1)
        ..update(0, player, world);
      swarm.kills.clear();
      swarm.hits.clear();
      embers
        ..drop(1, 2, 1)
        ..drop(-1, 2, Embers.medkit);
      final at = vm.Vector3(0, 0.5, 3);
      fx
        ..explosion(at, 2)
        ..ring(0, 3, 2, 1, 2, 4)
        ..bolt(vm.Vector3(-2, 1, 3), vm.Vector3(2, 1, 3))
        ..muzzle(at, vm.Vector3(0, 0, 1))
        ..tracer(vm.Vector3(0, 1, 0), vm.Vector3(0, 1, 6))
        ..ichor(at, 0, 1, 4)
        ..decal(0, 3, 1, 0.03, 0.07, 0.015, 0.9);
    }
    embers.update(dt, player, 0, fx);
    swarm.render();
    fx.update(dt);
    if (++_warmFrames < 40) return;
    swarm.clear();
    embers.clear();
    fx
      ..clear()
      ..update(0);
    phase = GamePhase.title;
    notifyListeners();
  }

  void start() {
    time = 0;
    kills = 0;
    level = 1;
    experience = 0;
    offers = const [];
    stats.reset();
    player
      ..maxHealth = 100
      ..moveSpeed = 4.4
      ..regen = 0
      ..armor = 0
      ..reset();
    swarm.clear();
    embers.clear();
    fx.clear();
    director.reset();
    for (final upgrade in upgrades) {
      upgrade.reset(this);
    }
    _grant(_carbine);
    phase = GamePhase.playing;
    _updateCamera(0, snap: true);
    notifyListeners();
  }

  void pause() {
    if (phase == GamePhase.playing) togglePause();
  }

  void togglePause() {
    if (phase == GamePhase.playing) {
      phase = GamePhase.paused;
    } else if (phase == GamePhase.paused) {
      phase = GamePhase.playing;
    } else {
      return;
    }
    notifyListeners();
  }

  /// Takes offer [index] from the level-up screen.
  void choose(int index) {
    if (phase != GamePhase.levelUp || index >= offers.length) return;
    _grant(offers[index]);
    offers = const [];
    phase = GamePhase.playing;
    _checkLevelUp();
    notifyListeners();
  }

  void _grant(Upgrade upgrade) {
    upgrade.level++;
    upgrade.onLevel(this);
  }

  void _checkLevelUp() {
    if (experience < experienceNeeded) return;
    experience -= experienceNeeded;
    level++;
    final pool = upgrades.where((u) => !u.maxed).toList()..shuffle(rng);
    if (pool.isEmpty) {
      player.heal(30);
      _checkLevelUp();
      return;
    }
    offers = pool.take(3).toList();
    phase = GamePhase.levelUp;
    sfx.play(Sound.levelup);
    _autoChoose = 0.4;
    final p = player.position;
    fx
      ..ring(p.x, p.y, 3, 5, 3, 0.8)
      ..flash(vm.Vector3(p.x, 1.2, p.y), 1, 0.7, 0.3, 200, 0.5);
  }

  /// Jumps to [seconds] into the night with a plausible loadout, for
  /// inspecting later stages.
  void debugSkipTo(double seconds) {
    time = seconds;
    director.skipTo(seconds);
    for (var i = 0; i < seconds / 13; i++) {
      final pool = upgrades.where((u) => !u.maxed).toList();
      if (pool.isEmpty) break;
      _grant(pool[rng.nextInt(pool.length)]);
      level++;
    }
  }

  void tick(double dt) {
    dt = math.min(dt, 1 / 20);
    sfx.update(dt);
    if (autoplay) _runBot(dt);
    if (phase == GamePhase.warming) {
      _warm(dt);
    } else if (phase == GamePhase.playing) {
      _simulate(dt);
    }
    _updateCamera(dt);
  }

  /// Forces a state that is slow to reach by playing: `levelUp` or `dead`.
  void debugForce(String state) {
    if (state == 'levelUp') {
      experience = experienceNeeded;
      _holdOffers = true;
    } else if (state == 'dead') {
      player.health = 0;
    }
  }

  bool _holdOffers = false;

  void _runBot(double dt) {
    final a = time * 0.4;
    moveInput.setValues(math.cos(a), math.sin(a));
    if (phase == GamePhase.levelUp &&
        !_holdOffers &&
        (_autoChoose -= dt) <= 0) {
      choose(0);
    }
    if (phase == GamePhase.dead) start();
  }

  void _simulate(double dt) {
    time += dt;
    final move = moveInput.clone();
    if (move.length2 > 1) move.normalize();
    final healthBefore = player.health;
    player.update(dt, move, _carbine.aim, world);
    if (director.update(
      dt,
      time,
      swarm,
      player,
      rng,
      density: settings.quality.swarmDensity,
    )) {
      sfx.play(Sound.growl);
      shake.addTrauma(0.5);
    }
    swarm.update(dt, player, world);
    if (debugDisarmed) {
      player.health = player.maxHealth;
    } else {
      for (final upgrade in upgrades) {
        if (upgrade is Weapon && upgrade.level > 0) upgrade.update(this, dt);
      }
    }
    _resolveCombat();
    final gained = embers.update(dt, player, stats.pickupRadius, fx);
    if (gained > 0) {
      experience += gained;
      // The chime climbs as the bar fills.
      sfx.play(Sound.ember, pitch: 0.9 + 0.5 * experience / experienceNeeded);
    }
    swarm.render();
    fx.update(dt);

    if (player.health < healthBefore - 0.01) {
      shake.addTrauma(math.min(0.6, (healthBefore - player.health) * 0.05));
      sfx.play(Sound.hurt);
    }
    if (!player.alive) {
      newBest = settings.recordRun(time);
      phase = GamePhase.dead;
      notifyListeners();
      return;
    }
    if (time >= Director.nightLength) {
      newBest = settings.recordRun(time);
      phase = GamePhase.victory;
      notifyListeners();
      return;
    }
    _checkLevelUp();
    if (phase != GamePhase.playing || (_hudTimer -= dt) <= 0) {
      _hudTimer = 0.1;
      notifyListeners();
    }
  }

  void _resolveCombat() {
    // Impact effects are capped so an explosion in a crowd stays cheap.
    final shown = math.min(swarm.hits.length, 30);
    for (var i = 0; i < shown; i++) {
      final hit = swarm.hits[i];
      fx.ichor(vm.Vector3(hit.x, hit.y, hit.z), hit.dirX, hit.dirZ, 3);
    }
    swarm.hits.clear();

    for (final kill in swarm.kills) {
      kills++;
      sfx.play(Sound.squish, pitch: 1.3 - 0.25 * kill.type.scale);
      final scale = kill.type.scale;
      fx
        ..ichor(
          vm.Vector3(kill.x, 0.3 * scale, kill.z),
          kill.dirX,
          kill.dirZ,
          (8 * scale).round(),
        )
        ..decal(
          kill.x + kill.dirX * 0.4 * scale,
          kill.z + kill.dirZ * 0.4 * scale,
          (1.1 + rng.nextDouble() * 0.9) * scale,
          0.02,
          0.034,
          0.012,
          0.85,
        );
      embers.drop(kill.x, kill.z, kill.type.ember);
      if (rng.nextDouble() < 0.012) {
        embers.drop(kill.x + 0.4, kill.z, Embers.medkit);
      }
      if (kill.type == EnemyType.matriarch) {
        fx.explosion(vm.Vector3(kill.x, 0.5, kill.z), 4);
        shake.addTrauma(0.8);
        for (var i = 0; i < 12; i++) {
          final a = i / 12 * math.pi * 2;
          embers.drop(
            kill.x + math.cos(a) * 1.8,
            kill.z + math.sin(a) * 1.8,
            5,
          );
        }
      }
    }
    swarm.kills.clear();
  }

  void _updateCamera(double dt, {bool snap = false}) {
    final target = vm.Vector3(
      player.position.x + player.velocity.x * 0.25,
      1.0,
      player.position.y + player.velocity.y * 0.25,
    );
    if (snap) {
      _cameraFocus.setFrom(target);
    } else {
      _cameraFocus.add((target - _cameraFocus) * (1 - math.exp(-6 * dt)));
    }
    final offset = shake.update(dt);
    _cameraNode.lookAtFrom(
      _cameraFocus + _cameraOffset + offset.translation,
      _cameraFocus + offset.rotationEuler * 12,
    );
  }
}
