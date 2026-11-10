import 'dart:math' as math;

import 'player.dart';
import 'swarm.dart';

/// Decides what spawns and when, ramping pressure over the night.
class Director {
  /// Seconds the player must survive to win.
  static const double nightLength = 600;
  static const double _matriarchEvery = 150;
  static const double _spawnDistance = 21;

  double _budget = 0;
  double _waveTimer = 25;
  double _nextMatriarch = _matriarchEvery;

  void reset() {
    _budget = 0;
    _waveTimer = 25;
    _nextMatriarch = _matriarchEvery;
  }

  /// Jumps the schedule to [time], with a Matriarch arriving soon after,
  /// for debugging later stages.
  void skipTo(double time) => _nextMatriarch = time + 15;

  /// Spawns what is due, keeping at most [density] of the full cap alive.
  /// Returns true when a Matriarch arrives.
  bool update(
    double dt,
    double time,
    Swarm swarm,
    Player player,
    math.Random rng, {
    double density = 1,
  }) {
    final healthScale = 1 + time / 150;
    final cap = math.min(60 + time * 0.75, 400.0) * density;
    final rate = math.min(1.6 + time / 20, 16.0);
    _budget += rate * dt;
    while (_budget >= 1) {
      _budget -= 1;
      if (swarm.alive >= cap) continue;
      final (x, z) = _around(player, rng, rng.nextDouble() * math.pi * 2);
      swarm.spawn(_pick(time, rng), x, z, healthScale);
    }

    // A pack rushing in from one side.
    _waveTimer -= dt;
    if (_waveTimer <= 0) {
      _waveTimer = 22;
      final heading = rng.nextDouble() * math.pi * 2;
      final size = (math.min(12 + time / 12, 48) * density).round();
      for (var i = 0; i < size; i++) {
        final (x, z) = _around(
          player,
          rng,
          heading + (rng.nextDouble() - 0.5) * 0.9,
        );
        swarm.spawn(EnemyType.skitterer, x, z, healthScale);
      }
    }

    if (time < _nextMatriarch) return false;
    _nextMatriarch += _matriarchEvery;
    final (x, z) = _around(player, rng, rng.nextDouble() * math.pi * 2);
    swarm.spawn(EnemyType.matriarch, x, z, 1 + time / 300);
    return true;
  }

  (double, double) _around(Player player, math.Random rng, double angle) {
    final d = _spawnDistance + rng.nextDouble() * 6;
    return (
      player.position.x + math.cos(angle) * d,
      player.position.y + math.sin(angle) * d,
    );
  }

  EnemyType _pick(double time, math.Random rng) {
    final brute = time < 50 ? 0.0 : math.min(0.04 + time / 2400, 0.2);
    final skitterer = math.min(0.2 + time / 600, 0.5);
    final roll = rng.nextDouble();
    if (roll < brute) return EnemyType.brute;
    if (roll < brute + skitterer) return EnemyType.skitterer;
    return EnemyType.stalker;
  }
}
