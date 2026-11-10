import 'package:emberwatch/game/game.dart';
import 'package:emberwatch/gamepad.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gamepads/gamepads.dart';

void main() {
  late Game game;
  late GamepadInput pad;

  setUp(() {
    game = Game();
    pad = GamepadInput(game);
  });

  test('a stick resting off centre does not move the player', () {
    pad
      ..handle(axis: GamepadAxis.leftStickX, value: 0.12)
      ..handle(axis: GamepadAxis.leftStickY, value: -0.1);

    expect(pad.move.length, 0);
  });

  test('a full tilt gives full speed in that direction', () {
    pad.handle(axis: GamepadAxis.leftStickY, value: 1);

    expect(pad.move.x, 0);
    expect(pad.move.y, closeTo(1, 1e-9));
  });

  test('the D-pad moves and stops on release', () {
    pad.handle(button: GamepadButton.dpadLeft, value: 1);
    expect(pad.move.x, -1);

    pad.handle(button: GamepadButton.dpadLeft);
    expect(pad.move.x, 0);
  });

  test('Start pauses and A resumes', () {
    game.phase = GamePhase.playing;

    pad.handle(button: GamepadButton.start, value: 1);
    expect(game.phase, GamePhase.paused);

    pad.handle(button: GamepadButton.a, value: 1);
    expect(game.phase, GamePhase.playing);
  });

  test('a held button acts once', () {
    game.phase = GamePhase.playing;

    pad
      ..handle(button: GamepadButton.start, value: 1)
      ..handle(button: GamepadButton.start, value: 1);

    expect(game.phase, GamePhase.paused);
  });
}
