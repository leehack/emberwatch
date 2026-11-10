import 'package:emberwatch/game/arsenal.dart';
import 'package:emberwatch/game/game.dart';
import 'package:emberwatch/game/swarm.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  late Game game;

  setUp(() => game = Game());

  Upgrade upgrade(String name) =>
      buildPassives().firstWhere((u) => u.name == name);

  void raise(Upgrade upgrade, [int levels = 1]) {
    for (var i = 0; i < levels; i++) {
      upgrade
        ..level += 1
        ..onLevel(game);
    }
  }

  group('Carbine', () {
    late Carbine carbine;

    setUp(() => carbine = Carbine()..level = 1);

    test('aims at and hits the nearest enemy in range', () {
      game.swarm
        ..spawn(EnemyType.brute, 0, 6, 1)
        ..spawn(EnemyType.brute, 5, 0, 1);

      carbine.update(game, 0.016);

      expect(carbine.aim, closeTo(1.5708, 1e-3));
      expect(game.swarm.hits.single.x, 5);
      expect(game.swarm.hits.single.amount, 14);
    });

    test('holds fire when nothing is in range', () {
      game.swarm.spawn(EnemyType.brute, 0, Carbine.range + 5, 1);

      carbine.update(game, 0.016);

      expect(carbine.aim, isNull);
      expect(game.swarm.hits, isEmpty);
    });

    test('a round stops at the first enemy until it gains pierce', () {
      game.swarm
        ..spawn(EnemyType.brute, 0, 5, 1)
        ..spawn(EnemyType.brute, 0, 7, 1);

      carbine.update(game, 0.016);
      expect(game.swarm.hits, hasLength(1));

      game.swarm.hits.clear();
      carbine
        ..level = 3
        ..timer = 0
        ..update(game, 0.016);
      expect(game.swarm.hits, hasLength(2));
    });

    test('double tap fires a second round a moment later', () {
      carbine.level = 5;
      game.swarm.spawn(EnemyType.brute, 0, 5, 1);

      carbine.update(game, 0.016);
      expect(game.swarm.hits, hasLength(1));

      carbine.update(game, 0.1);
      expect(game.swarm.hits, hasLength(2));
    });

    test('damage follows the run-wide multiplier', () {
      game.stats.damage = 1.5;
      game.swarm.spawn(EnemyType.brute, 0, 5, 1);

      carbine.update(game, 0.016);

      expect(game.swarm.hits.single.amount, 21);
    });
  });

  group('Passives', () {
    test('Hollow Points adds 12% damage per level', () {
      raise(upgrade('Hollow Points'), 3);

      expect(game.stats.damage, closeTo(1.36, 1e-9));
    });

    test('Field Rations raises max health and heals', () {
      game.player.hurt(50);

      raise(upgrade('Field Rations'));

      expect(game.player.maxHealth, 120);
      expect(game.player.health, 70);
    });

    test('Ceramic Plates cuts damage taken', () {
      raise(upgrade('Ceramic Plates'), 2);

      expect(game.player.hurt(100), closeTo(86, 1e-9));
    });

    test('Quick Hands shortens weapon cooldowns', () {
      raise(upgrade('Quick Hands'), 2);

      expect(game.stats.cooldown, closeTo(0.93 * 0.93, 1e-9));
    });

    test('every upgrade reports maxed at its last level', () {
      for (final u in [Carbine(), FragLauncher(), ...buildPassives()]) {
        u.level = u.levels.length - 1;
        expect(u.maxed, isFalse, reason: u.name);
        u.level++;
        expect(u.maxed, isTrue, reason: u.name);
      }
    });
  });

  test('each level needs more experience than the last', () {
    var previous = 0;
    for (game.level = 1; game.level <= 40; game.level++) {
      expect(game.experienceNeeded, greaterThan(previous));
      previous = game.experienceNeeded;
    }
  });
}
