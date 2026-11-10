import 'dart:math' as math;

import 'package:emberwatch/game/director.dart';
import 'package:emberwatch/game/embers.dart';
import 'package:emberwatch/game/fx.dart';
import 'package:emberwatch/game/player.dart';
import 'package:emberwatch/game/swarm.dart';
import 'package:emberwatch/game/world.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  late Swarm swarm;
  late Player player;
  late World world;

  setUp(() {
    swarm = Swarm();
    player = Player();
    world = World();
  });

  group('Swarm', () {
    test('lethal damage reports a kill and stops targeting the enemy', () {
      swarm
        ..spawn(EnemyType.stalker, 5, 0, 1)
        ..spawn(EnemyType.stalker, 8, 0, 1);

      swarm.damage(0, 1000, 1, 0);

      expect(swarm.alive, 1);
      expect(swarm.kills.single.type, EnemyType.stalker);
      expect(swarm.nearest(0, 0, 50), 1);
    });

    test('forEachInRadius visits only enemies overlapping the circle', () {
      swarm
        ..spawn(EnemyType.stalker, 10, 10, 1)
        ..spawn(EnemyType.stalker, 30, 30, 1)
        ..update(0, player, world);

      final found = <int>[];
      swarm.forEachInRadius(10, 10, 2, found.add);

      expect(found, [0]);
    });

    test('enemies spawned on one spot are pushed apart', () {
      swarm
        ..spawn(EnemyType.stalker, 20, 0.001, 1)
        ..spawn(EnemyType.stalker, 20, 0, 1);

      for (var i = 0; i < 30; i++) {
        swarm.update(1 / 60, player, world);
      }

      final gap = math.sqrt(
        math.pow(swarm.x[0] - swarm.x[1], 2) +
            math.pow(swarm.z[0] - swarm.z[1], 2),
      );
      expect(gap, greaterThan(EnemyType.stalker.radius));
    });

    test('an enemy touching the player deals damage over time', () {
      swarm.spawn(EnemyType.brute, 0.3, 0, 1);

      for (var i = 0; i < 60; i++) {
        swarm.update(1 / 60, player, world);
      }

      expect(player.health, closeTo(100 - EnemyType.brute.damage, 2));
    });
  });

  group('Embers', () {
    test('collects embers in range and leaves distant ones', () {
      final embers = Embers()
        ..drop(1, 0, 3)
        ..drop(40, 0, 5);

      var gained = 0;
      for (var i = 0; i < 120; i++) {
        gained += embers.update(1 / 60, player, 2.6, _NoFx());
      }

      expect(gained, 3);
      expect(embers.count, 1);
    });

    test('a medkit heals instead of granting experience', () {
      player.health = 50;
      final embers = Embers()..drop(0.2, 0, Embers.medkit);

      final gained = embers.update(1 / 60, player, 2.6, _NoFx());

      expect(gained, 0);
      expect(player.health, 75);
    });
  });

  test('Director ramps the swarm and sends a Matriarch on schedule', () {
    final director = Director();
    final rng = math.Random(1);
    var matriarchs = 0;

    for (var t = 0.0; t < 160; t += 0.1) {
      if (director.update(0.1, t, swarm, player, rng)) matriarchs++;
    }

    expect(matriarchs, 1);
    expect(swarm.alive, greaterThan(100));
    expect(swarm.alive, lessThanOrEqualTo(Swarm.capacity));
  });
}

/// Embers only push glow sprites; the test does not draw them.
class _NoFx extends Fx {
  @override
  void glow(
    double x,
    double y,
    double z,
    double size,
    double r,
    double g,
    double b,
  ) {}
}
