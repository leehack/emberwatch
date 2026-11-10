import 'dart:math' as math;

import 'package:emberwatch/game/player.dart';
import 'package:emberwatch/game/world.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vector_math/vector_math.dart';

void main() {
  late Player player;
  late World world;

  void run(double seconds, Vector2 move, {double? face}) {
    for (var t = 0.0; t < seconds; t += 1 / 60) {
      player.update(1 / 60, move, face, world);
    }
  }

  setUp(() {
    player = Player();
    world = World();
  });

  test('armor reduces damage and health stops at zero', () {
    player.armor = 0.25;

    expect(player.hurt(40), 30);
    expect(player.health, 70);

    player.hurt(1000);
    expect(player.health, 0);
    expect(player.alive, isFalse);
  });

  test('healing stops at max health', () {
    player
      ..hurt(30)
      ..heal(500);

    expect(player.health, player.maxHealth);
  });

  test('reaches full speed along the stick and turns to face its heading', () {
    run(1, Vector2(1, 0));

    expect(player.velocity.x, closeTo(player.moveSpeed, 0.01));
    expect(player.velocity.y, closeTo(0, 0.01));
    expect(player.position.x, greaterThan(3.5));
    expect(player.yaw, closeTo(math.pi / 2, 0.01));
  });

  test('faces the given angle while moving another way', () {
    run(1, Vector2(1, 0), face: math.pi);

    expect(player.position.x, greaterThan(3.5));
    expect(wrapAngle(player.yaw - math.pi).abs(), lessThan(0.01));
  });

  test('cannot leave the arena', () {
    player.position.setValues(World.arenaRadius - 3, 0);

    run(3, Vector2(1, 0));

    expect(player.position.length, closeTo(World.arenaRadius - 1, 1e-6));
  });

  test('regenerates while alive', () {
    player
      ..regen = 2
      ..hurt(50);

    run(1, Vector2.zero());

    expect(player.health, closeTo(52, 0.1));
  });

  test('wrapAngle keeps angles within half a turn', () {
    expect(wrapAngle(3 * math.pi + 0.2), closeTo(-math.pi + 0.2, 1e-9));
    expect(wrapAngle(-3 * math.pi - 0.2), closeTo(math.pi - 0.2, 1e-9));
  });
}
