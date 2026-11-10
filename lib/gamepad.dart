import 'dart:async';

import 'package:gamepads/gamepads.dart';
import 'package:vector_math/vector_math.dart';

import 'game/game.dart';

/// Controller input: the left stick or D-pad moves, Start pauses, A starts
/// and resumes, and X, A and B pick the left, middle and right upgrade.
class GamepadInput {
  GamepadInput(this.game);

  final Game game;
  StreamSubscription<NormalizedGamepadEvent>? _events;
  double _stickX = 0, _stickY = 0;
  final Set<GamepadButton> _held = {};

  // Worn sticks rest a little off centre.
  static const double _deadZone = 0.2;

  void listen() {
    // A platform without controller support reports it on the stream.
    _events = Gamepads.normalizedEvents.listen(
      (e) => handle(button: e.button, axis: e.axis, value: e.value),
      onError: (Object _) {},
    );
  }

  void dispose() => _events?.cancel();

  /// Stick and D-pad combined, +Y up, each component within -1..1.
  Vector2 get move {
    double pad(GamepadButton negative, GamepadButton positive) =>
        (_held.contains(positive) ? 1.0 : 0.0) -
        (_held.contains(negative) ? 1.0 : 0.0);
    final stick = Vector2(_stickX, _stickY);
    final tilt = stick.length;
    if (tilt < _deadZone) {
      stick.setZero();
    } else {
      stick.scale(((tilt - _deadZone) / (1 - _deadZone)).clamp(0, 1) / tilt);
    }
    return stick
      ..x += pad(GamepadButton.dpadLeft, GamepadButton.dpadRight)
      ..y += pad(GamepadButton.dpadDown, GamepadButton.dpadUp)
      ..clampScalar(-1, 1);
  }

  void handle({GamepadButton? button, GamepadAxis? axis, double value = 0}) {
    switch (axis) {
      case GamepadAxis.leftStickX:
        _stickX = value;
      case GamepadAxis.leftStickY:
        _stickY = value;
      default:
    }
    if (button == null) return;
    if (value < 0.5) {
      _held.remove(button);
    } else if (_held.add(button)) {
      _press(button);
    }
  }

  void _press(GamepadButton button) {
    final phase = game.phase;
    if (button == GamepadButton.start) {
      game.togglePause();
    } else if (phase == GamePhase.levelUp) {
      const picks = [GamepadButton.x, GamepadButton.a, GamepadButton.b];
      final pick = picks.indexOf(button);
      if (pick >= 0) game.choose(pick);
    } else if (button == GamepadButton.a) {
      if (phase == GamePhase.paused) {
        game.togglePause();
      } else if (phase == GamePhase.title ||
          phase == GamePhase.dead ||
          phase == GamePhase.victory) {
        game.start();
      }
    }
  }
}
