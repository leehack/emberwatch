import 'dart:math' as math;

import 'package:flutter/material.dart' show IconData, Icons;
import 'package:flutter_scene/scene.dart';
import 'package:vector_math/vector_math.dart' as vm;

import 'game.dart';
import 'player.dart';
import 'sfx.dart';

/// Run-wide multipliers that passives raise.
class Stats {
  double damage = 1;
  double cooldown = 1;
  double pickupRadius = 2.6;

  void reset() {
    damage = 1;
    cooldown = 1;
    pickupRadius = 2.6;
  }
}

/// Something a level-up can grant or improve.
abstract class Upgrade {
  Upgrade(this.name, this.icon, this.levels);

  final String name;
  final IconData icon;

  /// What each level adds, in order. Its length is the maximum level.
  final List<String> levels;

  int level = 0;
  bool get isWeapon => false;
  bool get maxed => level >= levels.length;
  String get nextDescription => levels[level];

  /// Called after [level] has been raised.
  void onLevel(Game game) {}

  void reset(Game game) => level = 0;
}

abstract class Weapon extends Upgrade {
  Weapon(super.name, super.icon, super.levels);

  @override
  bool get isWeapon => true;

  double timer = 0;

  @override
  void reset(Game game) {
    super.reset(game);
    timer = 0;
  }

  void update(Game game, double dt);
}

class _Passive extends Upgrade {
  _Passive(String name, IconData icon, String effect, int max, this.apply)
    : super(name, icon, List.filled(max, effect));

  final void Function(Game game) apply;

  @override
  void onLevel(Game game) => apply(game);
}

List<Upgrade> buildUpgrades(Scene scene) => [
  Carbine(),
  FragLauncher(),
  ArcCoil(),
  EmberWisps(scene),
  ShockPulse(),
  ...buildPassives(),
];

List<Upgrade> buildPassives() => [
  _Passive(
    'Field Rations',
    Icons.restaurant,
    '+20 max health, and heal 20',
    5,
    (g) {
      g.player.maxHealth += 20;
      g.player.heal(20);
    },
  ),
  _Passive(
    'Adrenaline',
    Icons.directions_run,
    '+7% movement speed',
    5,
    (g) => g.player.moveSpeed *= 1.07,
  ),
  _Passive(
    'Ember Lure',
    Icons.all_out,
    '+35% ember pickup range',
    5,
    (g) => g.stats.pickupRadius *= 1.35,
  ),
  _Passive(
    'Hollow Points',
    Icons.keyboard_double_arrow_up,
    '+12% damage',
    5,
    (g) => g.stats.damage += 0.12,
  ),
  _Passive(
    'Quick Hands',
    Icons.timer,
    'Weapons cycle 7% faster',
    5,
    (g) => g.stats.cooldown *= 0.93,
  ),
  _Passive(
    'Field Dressing',
    Icons.healing,
    'Regenerate +0.5 health per second',
    5,
    (g) => g.player.regen += 0.5,
  ),
  _Passive(
    'Ceramic Plates',
    Icons.shield,
    'Take 7% less damage',
    5,
    (g) => g.player.armor += 0.07,
  ),
];

/// Starting weapon: hitscan rounds at the nearest enemy.
class Carbine extends Weapon {
  Carbine()
    : super('Carbine', Icons.gps_fixed, const [
        'Fires at the nearest enemy',
        '+30% fire rate',
        'Rounds pierce one more enemy',
        '+40% damage',
        'Double tap: two rounds per trigger pull',
        'Pierce two more enemies, +25% damage',
      ]);

  static const double range = 9.5;
  int _queued = 0;
  double _burstTimer = 0;

  int _target = -1;

  /// Heading toward the enemy being engaged, or null. The player faces it.
  double? aim;

  double get _interval => (level >= 2 ? 0.37 : 0.48);
  double get _damage => 14.0 * (level >= 4 ? 1.4 : 1) * (level >= 6 ? 1.25 : 1);
  int get _pierce => 1 + (level >= 3 ? 1 : 0) + (level >= 6 ? 2 : 0);

  @override
  void reset(Game game) {
    super.reset(game);
    _queued = 0;
    aim = null;
  }

  @override
  void update(Game game, double dt) {
    final p = game.player.position;
    final swarm = game.swarm;
    _target = swarm.nearest(p.x, p.y, range);
    aim = _target < 0
        ? null
        : math.atan2(swarm.x[_target] - p.x, swarm.z[_target] - p.y);
    timer -= dt;
    _burstTimer -= dt;
    if (_target < 0) return;
    if (timer <= 0) {
      timer = _interval * game.stats.cooldown;
      _queued = level >= 5 ? 2 : 1;
      _burstTimer = 0;
    }
    if (_queued > 0 && _burstTimer <= 0) {
      _queued--;
      _burstTimer = 0.09;
      _fire(game);
    }
  }

  void _fire(Game game) {
    final swarm = game.swarm;
    final p = game.player.position;
    var dx = swarm.x[_target] - p.x, dz = swarm.z[_target] - p.y;
    final d = math.sqrt(dx * dx + dz * dz) + 1e-6;
    dx /= d;
    dz /= d;
    final muzzle = vm.Vector3(
      p.x + dx * 0.7,
      Player.chestHeight,
      p.y + dz * 0.7,
    );

    // Everything the round's path crosses, nearest first.
    final struck = <(double, int)>[];
    for (var i = 0; i < swarm.count; i++) {
      if (!swarm.isAlive(i)) continue;
      final ex = swarm.x[i] - p.x, ez = swarm.z[i] - p.y;
      final along = ex * dx + ez * dz;
      if (along < 0 || along > range + 3) continue;
      final across = (ex * dz - ez * dx).abs();
      if (across < swarm.radiusOf(i) + 0.12) struck.add((along, i));
    }
    struck.sort((a, b) => a.$1.compareTo(b.$1));
    var reach = range + 3;
    var remaining = _pierce;
    for (final (along, i) in struck) {
      swarm.damage(i, _damage * game.stats.damage, dx, dz, knockback: 2.5);
      if (--remaining == 0) {
        reach = along;
        break;
      }
    }
    final end = vm.Vector3(p.x + dx * reach, 0.35, p.y + dz * reach);
    game.fx
      ..muzzle(muzzle, vm.Vector3(dx, 0.1, dz))
      ..tracer(muzzle, end);
    game.sfx.play(Sound.shot);
    game.shake.addTrauma(0.05);
  }
}

class _Grenade {
  _Grenade(this.fromX, this.fromZ, this.toX, this.toZ);
  final double fromX, fromZ, toX, toZ;
  double t = 0;
}

/// Lobs explosives into the thickest part of the swarm.
class FragLauncher extends Weapon {
  FragLauncher()
    : super('Frag Launcher', Icons.flare, const [
        'Lobs a grenade into the swarm',
        '+25% blast radius',
        'Fires one more grenade',
        '+50% damage',
        'Reloads 25% faster',
        'One more grenade, +20% blast radius',
      ]);

  static const double _flight = 0.7;
  final List<_Grenade> _grenades = [];

  double get _radius => 3.0 * (level >= 2 ? 1.25 : 1) * (level >= 6 ? 1.2 : 1);
  double get _damage => 46.0 * (level >= 4 ? 1.5 : 1);
  int get _count => 1 + (level >= 3 ? 1 : 0) + (level >= 6 ? 1 : 0);

  @override
  void reset(Game game) {
    super.reset(game);
    _grenades.clear();
  }

  @override
  void update(Game game, double dt) {
    final swarm = game.swarm;
    final p = game.player.position;
    timer -= dt;
    if (timer <= 0 && swarm.alive > 0) {
      var fired = false;
      for (var n = 0; n < _count; n++) {
        final i = _densest(game);
        if (i < 0) break;
        _grenades.add(_Grenade(p.x, p.y, swarm.x[i], swarm.z[i]));
        fired = true;
      }
      if (fired) timer = (level >= 5 ? 2.5 : 3.3) * game.stats.cooldown;
    }

    _grenades.removeWhere((g) {
      g.t += dt / _flight;
      final t = math.min(g.t, 1.0);
      final x = g.fromX + (g.toX - g.fromX) * t;
      final z = g.fromZ + (g.toZ - g.fromZ) * t;
      final y = 1.2 * (1 - t) + 4 * t * (1 - t) * 3.2;
      game.fx
        ..glow(x, y, z, 0.5, 6, 2.2, 0.5)
        ..glow(x, y, z, 0.16, 10, 8, 5);
      if (g.t < 1) return false;
      _explode(game, g.toX, g.toZ);
      return true;
    });
  }

  /// Samples a few enemies in range and returns the most crowded one.
  int _densest(Game game) {
    final swarm = game.swarm;
    final p = game.player.position;
    var best = -1, bestScore = -1;
    for (var s = 0; s < 8; s++) {
      final i = game.rng.nextInt(swarm.count);
      if (!swarm.isAlive(i)) continue;
      final dx = swarm.x[i] - p.x, dz = swarm.z[i] - p.y;
      if (dx * dx + dz * dz > 10 * 10) continue;
      var score = 0;
      swarm.forEachInRadius(swarm.x[i], swarm.z[i], _radius, (_) => score++);
      if (score > bestScore) {
        bestScore = score;
        best = i;
      }
    }
    return best;
  }

  void _explode(Game game, double x, double z) {
    final swarm = game.swarm;
    final radius = _radius;
    swarm.forEachInRadius(x, z, radius, (i) {
      var dx = swarm.x[i] - x, dz = swarm.z[i] - z;
      final d = math.sqrt(dx * dx + dz * dz) + 1e-6;
      dx /= d;
      dz /= d;
      // Full damage at the center, 40% at the rim.
      final falloff = 1 - 0.6 * math.min(1.0, d / radius);
      swarm.damage(
        i,
        _damage * falloff * game.stats.damage,
        dx,
        dz,
        knockback: 9,
      );
    });
    game.fx.explosion(vm.Vector3(x, 0.2, z), radius);
    game.sfx.play(Sound.explosion);
    game.shake.addTrauma(0.45);
  }
}

/// Chain lightning that jumps between nearby enemies.
class ArcCoil extends Weapon {
  ArcCoil()
    : super('Arc Coil', Icons.bolt, const [
        'Lightning arcs between three enemies',
        'Arcs to two more enemies',
        '+40% damage',
        'Recharges 25% faster',
        'Arcs to three more enemies',
        'Two more arcs, +30% damage',
      ]);

  int get _chains =>
      3 + (level >= 2 ? 2 : 0) + (level >= 5 ? 3 : 0) + (level >= 6 ? 2 : 0);
  double get _damage => 20.0 * (level >= 3 ? 1.4 : 1) * (level >= 6 ? 1.3 : 1);

  @override
  void update(Game game, double dt) {
    timer -= dt;
    if (timer > 0) return;
    final swarm = game.swarm;
    final p = game.player.position;
    var current = swarm.nearest(p.x, p.y, 8.5);
    if (current < 0) return;
    timer = (level >= 4 ? 1.9 : 2.5) * game.stats.cooldown;

    game.sfx.play(Sound.zap);
    final visited = <int>{};
    var from = vm.Vector3(p.x, Player.chestHeight, p.y);
    for (var n = 0; n < _chains && current >= 0; n++) {
      visited.add(current);
      final to = vm.Vector3(
        swarm.x[current],
        0.35 * swarm.typeOf(current).scale,
        swarm.z[current],
      );
      final dx = to.x - from.x, dz = to.z - from.z;
      final d = math.sqrt(dx * dx + dz * dz) + 1e-6;
      game.fx.bolt(from, to);
      final hit = current;
      current = swarm.nearest(to.x, to.z, 5.5, exclude: visited);
      swarm.damage(
        hit,
        _damage * game.stats.damage,
        dx / d,
        dz / d,
        knockback: 1.5,
      );
      from = to;
    }
    game.shake.addTrauma(0.12);
  }
}

/// Burning motes that circle the player and sear what they touch.
class EmberWisps extends Weapon {
  EmberWisps(Scene scene)
    : super('Ember Wisps', Icons.local_fire_department, const [
        'Two burning wisps circle you',
        'One more wisp',
        '+50% damage',
        'One more wisp, wider orbit',
        'Wisps orbit 30% faster, +30% damage',
        'Two more wisps',
      ]) {
    for (var i = 0; i < _maxLights; i++) {
      final light = PointLight(
        color: vm.Vector3(1, 0.5, 0.16),
        intensity: 0,
        range: 7,
      );
      _lights.add(light);
      _nodes.add(
        Node(name: 'wisp_$i')..addComponent(PointLightComponent(light)),
      );
      scene.add(_nodes.last);
    }
  }

  static const int _maxLights = 4;
  final List<PointLight> _lights = [];
  final List<Node> _nodes = [];
  double _angle = 0;

  int get _wisps =>
      2 + (level >= 2 ? 1 : 0) + (level >= 4 ? 1 : 0) + (level >= 6 ? 2 : 0);
  double get _orbit => level >= 4 ? 3.0 : 2.5;
  double get _damage => 10.0 * (level >= 3 ? 1.5 : 1) * (level >= 5 ? 1.3 : 1);

  @override
  void reset(Game game) {
    super.reset(game);
    for (final light in _lights) {
      light.intensity = 0;
    }
  }

  @override
  void update(Game game, double dt) {
    final swarm = game.swarm;
    final p = game.player.position;
    _angle += (level >= 5 ? 3.4 : 2.6) * dt;
    for (var w = 0; w < _wisps; w++) {
      final a = _angle + w * math.pi * 2 / _wisps;
      final x = p.x + math.cos(a) * _orbit, z = p.y + math.sin(a) * _orbit;
      final y = 0.9 + math.sin(a * 2 + w) * 0.15;
      game.fx
        ..glow(x, y, z, 1.1, 3.2, 1.1, 0.2)
        ..glow(x, y, z, 0.3, 12, 8, 3.5);
      if (game.rng.nextDouble() < dt * 30) {
        game.fx.sparks(vm.Vector3(x, y, z), 1, 1.6, 6, 2.6, 0.6, life: 0.5);
      }
      if (w < _maxLights) {
        _lights[w].intensity = 22;
        _nodes[w].position = vm.Vector3(x, y, z);
      }

      swarm.forEachInRadius(x, z, 0.45, (i) {
        if (swarm.contactCooldown[i] > 0) return;
        swarm.contactCooldown[i] = 0.45;
        var dx = swarm.x[i] - p.x, dz = swarm.z[i] - p.y;
        final d = math.sqrt(dx * dx + dz * dz) + 1e-6;
        dx /= d;
        dz /= d;
        swarm.damage(i, _damage * game.stats.damage, dx, dz, knockback: 4);
        game.fx.sparks(vm.Vector3(x, y, z), 5, 5, 8, 3.5, 0.8);
      });
    }
  }
}

/// A periodic blast that throws back everything close.
class ShockPulse extends Weapon {
  ShockPulse()
    : super('Shock Pulse', Icons.waves, const [
        'A shockwave hurls nearby enemies back',
        '+25% radius',
        'Recharges 20% faster',
        'Double damage',
        '+25% radius',
        'Recharges 25% faster, +50% knockback',
      ]);

  double get _radius => 4.6 * (level >= 2 ? 1.25 : 1) * (level >= 5 ? 1.25 : 1);

  @override
  void update(Game game, double dt) {
    timer -= dt;
    if (timer > 0) return;
    final swarm = game.swarm;
    final p = game.player.position;
    if (swarm.nearest(p.x, p.y, _radius) < 0) return;
    timer =
        6.0 *
        (level >= 3 ? 0.8 : 1) *
        (level >= 6 ? 0.75 : 1) *
        game.stats.cooldown;
    swarm.forEachInRadius(p.x, p.y, _radius, (i) {
      var dx = swarm.x[i] - p.x, dz = swarm.z[i] - p.y;
      final d = math.sqrt(dx * dx + dz * dz) + 1e-6;
      dx /= d;
      dz /= d;
      swarm.damage(
        i,
        (level >= 4 ? 32.0 : 16.0) * game.stats.damage,
        dx,
        dz,
        knockback: level >= 6 ? 21 : 14,
      );
    });
    game.sfx.play(Sound.pulse);
    game.fx
      ..ring(p.x, p.y, _radius, 1.6, 3.2, 6)
      ..flash(vm.Vector3(p.x, 1, p.y), 0.5, 0.75, 1, 160, 0.3, range: 14);
    game.shake.addTrauma(0.3);
  }
}
