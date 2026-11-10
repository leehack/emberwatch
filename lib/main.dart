import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_scene/kit.dart';
import 'package:flutter_scene/scene.dart';

import 'debug/capture.dart';
import 'game/game.dart';
import 'game/quality.dart';
import 'gamepad.dart';
import 'ui/hud.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  if (!kIsWeb && isTouchDevice) {
    SystemChrome.setPreferredOrientations([
      DeviceOrientation.landscapeLeft,
      DeviceOrientation.landscapeRight,
    ]);
    SystemChrome.setEnabledSystemUIMode(SystemUiMode.immersiveSticky);
  }
  runApp(const EmberwatchApp());
}

class EmberwatchApp extends StatelessWidget {
  const EmberwatchApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Emberwatch',
      debugShowCheckedModeBanner: false,
      theme: ThemeData.dark(useMaterial3: true),
      home: const GameView(),
    );
  }
}

class GameView extends StatefulWidget {
  const GameView({super.key});

  @override
  State<GameView> createState() => _GameViewState();
}

class _GameViewState extends State<GameView> {
  final Game game = Game();
  late final GamepadInput _gamepad = GamepadInput(game);
  final DebugOptions _debug = DebugOptions.read();
  final GlobalKey _boundary = GlobalKey();
  final FocusNode _focus = FocusNode();
  late final AppLifecycleListener _lifecycle;
  Offset _stick = Offset.zero;

  @override
  void initState() {
    super.initState();
    // Losing focus or going to the background pauses the run. A scripted
    // run keeps going, since its window is rarely focused.
    _lifecycle = AppLifecycleListener(
      onInactive: () {
        if (!_debug.enabled) game.pause();
      },
    );
    game.load().then((_) {
      if (!mounted) return;
      _gamepad.listen();
      setState(() {});
      if (_debug.enabled) _runScript();
    });
  }

  void _runScript() {
    _debug
      ..logFrameTimes()
      ..scheduleShots(_boundary);
    final quality = Quality.values.where((q) => q.name == _debug.quality);
    if (quality.isNotEmpty) game.setQuality(quality.first);
    if (_debug.force == 'title') return;
    void startWhenReady() {
      if (game.phase != GamePhase.title) return;
      game
        ..removeListener(startWhenReady)
        ..autoplay = _debug.autoplay
        ..cameraZoom = _debug.zoom
        ..debugDisarmed = _debug.disarmed
        ..start();
      if (_debug.skipTo > 0) game.debugSkipTo(_debug.skipTo);
      if (_debug.force case final state?) game.debugForce(state);
    }

    game.addListener(startWhenReady);
  }

  @override
  void dispose() {
    _lifecycle.dispose();
    _gamepad.dispose();
    _focus.dispose();
    game.sfx.dispose();
    game.dispose();
    super.dispose();
  }

  KeyEventResult _onKey(FocusNode node, KeyEvent event) {
    if (event is! KeyDownEvent) return KeyEventResult.ignored;
    final key = event.logicalKey;
    final phase = game.phase;
    if (key == LogicalKeyboardKey.escape || key == LogicalKeyboardKey.keyP) {
      game.togglePause();
    } else if (key == LogicalKeyboardKey.enter ||
        key == LogicalKeyboardKey.space) {
      if (phase == GamePhase.paused) {
        game.togglePause();
      } else if (phase == GamePhase.title ||
          phase == GamePhase.dead ||
          phase == GamePhase.victory) {
        game.start();
      }
    } else if (key == LogicalKeyboardKey.digit1) {
      game.choose(0);
    } else if (key == LogicalKeyboardKey.digit2) {
      game.choose(1);
    } else if (key == LogicalKeyboardKey.digit3) {
      game.choose(2);
    } else {
      return KeyEventResult.ignored;
    }
    return KeyEventResult.handled;
  }

  void _tick(double dt) {
    final keys = HardwareKeyboard.instance.logicalKeysPressed;
    bool down(LogicalKeyboardKey a, LogicalKeyboardKey b) =>
        keys.contains(a) || keys.contains(b);
    final pad = _gamepad.move;
    var x = _stick.dx + pad.x, y = _stick.dy + pad.y;
    if (down(LogicalKeyboardKey.keyD, LogicalKeyboardKey.arrowRight)) x += 1;
    if (down(LogicalKeyboardKey.keyA, LogicalKeyboardKey.arrowLeft)) x -= 1;
    if (down(LogicalKeyboardKey.keyW, LogicalKeyboardKey.arrowUp)) y += 1;
    if (down(LogicalKeyboardKey.keyS, LogicalKeyboardKey.arrowDown)) y -= 1;
    game.moveInput.setValues(x, y);
    game.tick(dt);
  }

  @override
  Widget build(BuildContext context) {
    if (game.phase == GamePhase.loading) {
      return const Scaffold(body: LoadingCover());
    }
    return Scaffold(
      backgroundColor: Colors.black,
      body: Focus(
        focusNode: _focus,
        autofocus: true,
        onKeyEvent: _onKey,
        child: RepaintBoundary(
          key: _boundary,
          child: Stack(
            fit: StackFit.expand,
            children: [
              SceneView(game.scene, onTick: (elapsed, dt) => _tick(dt)),
              GameOverlay(game: game),
              if (isTouchDevice)
                ListenableBuilder(
                  listenable: game,
                  builder: (context, _) {
                    if (game.phase != GamePhase.playing) {
                      _stick = Offset.zero;
                      return const SizedBox.shrink();
                    }
                    return Align(
                      alignment: Alignment.bottomLeft,
                      child: SafeArea(
                        minimum: const EdgeInsets.fromLTRB(36, 0, 0, 64),
                        child: VirtualJoystick(
                          // Screen space is Y-down; the game wants Y toward
                          // the top.
                          onChanged: (d) => _stick = Offset(d.x, -d.y),
                        ),
                      ),
                    );
                  },
                ),
            ],
          ),
        ),
      ),
    );
  }
}
