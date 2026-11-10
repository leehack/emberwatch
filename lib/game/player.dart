import 'dart:math' as math;

import 'package:flutter_scene/scene.dart';
import 'package:vector_math/vector_math.dart' as vm;

import 'quality.dart';
import 'world.dart';

double wrapAngle(double a) {
  while (a > math.pi) {
    a -= 2 * math.pi;
  }
  while (a < -math.pi) {
    a += 2 * math.pi;
  }
  return a;
}

class Player {
  static const double radius = 0.38;
  static const double chestHeight = 1.25;

  final Node root = Node(name: 'player');
  final Node _body = Node(name: 'player_body');
  final Node _lightNode = Node(name: 'flashlight');
  final SpotLight flashlight = SpotLight(
    color: vm.Vector3(1.0, 0.9, 0.74),
    intensity: 420,
    range: 26,
    innerConeAngle: 0.22,
    outerConeAngle: 0.62,
    castsShadow: true,
    shadowMapResolution: 2048,
    // Past the carbine's muzzle, so the trooper's own weapon and arms do not
    // shadow the beam.
    shadowNear: 1.1,
    shadowSoftness: 1.5,
  );

  // Null until the model loads, so the simulation can run without it.
  AnimationClip? _idle, _run, _runBack;
  double _backpedal = 0;

  final vm.Vector2 position = vm.Vector2.zero();
  final vm.Vector2 velocity = vm.Vector2.zero();
  final vm.Vector2 _resolved = vm.Vector2.zero();
  double yaw = 0;

  double maxHealth = 100;
  double health = 100;
  double moveSpeed = 4.4;
  double regen = 0;
  double armor = 0;
  double hurtFlash = 0;

  bool get alive => health > 0;
  vm.Vector2 get forward => vm.Vector2(math.sin(yaw), math.cos(yaw));

  Future<void> load() async {
    final model = await loadScene('assets/models/trooper.glb');
    // The importer's handedness flip already turns the model to face +Z.
    _body.add(model);
    _idle = model.createAnimationClip(model.findAnimationByName('Idle')!)
      ..loop = true
      ..play();
    _run = model.createAnimationClip(model.findAnimationByName('Run')!)
      ..loop = true
      ..weight = 0
      ..play();
    _runBack = model.createAnimationClip(model.findAnimationByName('RunBack')!)
      ..loop = true
      ..weight = 0
      ..play();

    _lightNode
      ..position = vm.Vector3(0, 1.55, 0)
      ..addComponent(SpotLightComponent(flashlight));
    // A warm lantern keeps the player readable outside the flashlight beam.
    final lantern = Node(name: 'lantern')
      ..position = vm.Vector3(0, 2.8, 0)
      ..addComponent(
        PointLightComponent(
          PointLight(
            color: vm.Vector3(1.0, 0.6, 0.28),
            intensity: 3.2,
            range: 8,
          ),
        ),
      );
    root
      ..add(_body)
      ..add(_lightNode)
      ..add(lantern);
  }

  void applyQuality(Quality quality) {
    flashlight
      ..castsShadow = quality != Quality.low
      ..shadowMapResolution = quality == Quality.high ? 2048 : 1024;
  }

  void reset() {
    position.setZero();
    velocity.setZero();
    yaw = 0;
    health = maxHealth;
    hurtFlash = 0;
    _backpedal = 0;
  }

  /// Applies [amount] of damage after armor. Returns what was dealt.
  double hurt(double amount) {
    final dealt = math.max(amount * (1 - armor), 0.0);
    health = math.max(health - dealt, 0);
    // Scaled by the hit, so chip damage tints the screen less than a bite.
    hurtFlash = math.min(1, hurtFlash + dealt * 0.3);
    return dealt;
  }

  void heal(double amount) => health = math.min(health + amount, maxHealth);

  /// Moves by [move] (unit-length at full tilt, +Y is world +Z) and turns
  /// toward [faceAngle], or the heading when there is nothing to face.
  void update(double dt, vm.Vector2 move, double? faceAngle, World world) {
    final target = move * moveSpeed;
    final blend = 1 - math.exp(-14 * dt);
    velocity.setValues(
      velocity.x + (target.x - velocity.x) * blend,
      velocity.y + (target.y - velocity.y) * blend,
    );
    var x = position.x + velocity.x * dt;
    var z = position.y + velocity.y * dt;
    final d = math.sqrt(x * x + z * z);
    const limit = World.arenaRadius - 1;
    if (d > limit) {
      x *= limit / d;
      z *= limit / d;
    }
    world.resolve(x, z, radius, _resolved);
    position.setFrom(_resolved);

    final speed = velocity.length;
    final desired =
        faceAngle ?? (speed > 0.4 ? math.atan2(velocity.x, velocity.y) : yaw);
    yaw = wrapAngle(yaw + wrapAngle(desired - yaw) * (1 - math.exp(-12 * dt)));

    final runWeight = (speed / moveSpeed).clamp(0.0, 1.0);
    // Retreating from the target it faces, the stride becomes a backpedal.
    final retreating = velocity.dot(forward) < -0.3 * speed;
    _backpedal +=
        ((retreating ? 1 : 0) - _backpedal) * (1 - math.exp(-10 * dt));
    _idle?.weight = 1 - runWeight;
    _run
      ?..weight = runWeight * (1 - _backpedal)
      ..playbackTimeScale = 0.75 + 0.45 * runWeight;
    // The backpedal clip is paced for a slower step than the run.
    _runBack
      ?..weight = runWeight * _backpedal
      ..playbackTimeScale = 0.85 + 0.45 * runWeight;

    if (alive && regen > 0) heal(regen * dt);
    hurtFlash = math.max(0, hurtFlash - dt * 3);

    root.position = vm.Vector3(position.x, 0, position.y);
    _body.rotation = vm.Quaternion.axisAngle(vm.Vector3(0, 1, 0), yaw);
    flashlight.direction = vm.Vector3(math.sin(yaw), -0.14, math.cos(yaw));
  }
}
