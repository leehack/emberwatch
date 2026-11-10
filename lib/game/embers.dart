import 'dart:math' as math;
import 'dart:typed_data';

import 'fx.dart';
import 'player.dart';

/// Experience motes dropped by kills, plus the occasional medkit. Drawn as
/// glow sprites, so there is no per-pickup node.
class Embers {
  static const int capacity = 900;
  static const int medkit = -1;

  int count = 0;
  final Float32List _x = Float32List(capacity);
  final Float32List _z = Float32List(capacity);
  final Float32List _age = Float32List(capacity);
  final Float32List _pull = Float32List(capacity);
  final Int16List _value = Int16List(capacity);

  void clear() => count = 0;

  /// Drops an ember worth [value], or a medkit for [medkit].
  void drop(double x, double z, int value) {
    if (count >= capacity) {
      // Full: fold the value into an existing ember rather than lose it.
      if (value > 0) _value[0] += value;
      return;
    }
    final i = count++;
    _x[i] = x;
    _z[i] = z;
    _age[i] = 0;
    _pull[i] = 0;
    _value[i] = value;
  }

  /// Pulls embers within [radius] toward the player. Returns the experience
  /// collected this frame.
  int update(double dt, Player player, double radius, Fx fx) {
    var gained = 0;
    final px = player.position.x, pz = player.position.y;
    var i = 0;
    while (i < count) {
      _age[i] += dt;
      final dx = px - _x[i], dz = pz - _z[i];
      final d = math.sqrt(dx * dx + dz * dz) + 1e-6;
      if (_pull[i] > 0 || d < radius) {
        _pull[i] += dt;
        final speed = 5 + _pull[i] * 28;
        _x[i] += dx / d * math.min(speed * dt, d);
        _z[i] += dz / d * math.min(speed * dt, d);
      }
      if (d < 0.55) {
        if (_value[i] == medkit) {
          player.heal(25);
        } else {
          gained += _value[i];
        }
        final last = --count;
        _x[i] = _x[last];
        _z[i] = _z[last];
        _age[i] = _age[last];
        _pull[i] = _pull[last];
        _value[i] = _value[last];
        continue;
      }
      final pulse = 0.85 + 0.15 * math.sin(_age[i] * 5 + i);
      final y = 0.22 + 0.05 * math.sin(_age[i] * 2.4 + i);
      final appear = math.min(1.0, _age[i] * 5);
      if (_value[i] == medkit) {
        fx
          ..glow(_x[i], y + 0.1, _z[i], 1.0 * pulse, 0.25, 2.4, 0.7)
          ..glow(_x[i], y + 0.1, _z[i], 0.3, 4, 9, 5);
      } else {
        final big = _value[i] >= 5;
        final size = (big ? 0.85 : 0.55) * pulse * appear;
        fx
          ..glow(_x[i], y, _z[i], size, 2.6, big ? 1.6 : 0.9, big ? 0.5 : 0.12)
          ..glow(_x[i], y, _z[i], size * 0.3, 10, 6.5, 2.2);
      }
      i++;
    }
    return gained;
  }
}
