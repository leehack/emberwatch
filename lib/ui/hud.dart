import 'dart:math' as math;
import 'dart:ui' show FontFeature, ImageFilter;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../game/arsenal.dart';
import '../game/director.dart';
import '../game/game.dart';

part 'hud_level_up.dart';
part 'hud_menus.dart';
part 'hud_play.dart';

const _ember = Color(0xFFFF8A3D);
const _emberHot = Color(0xFFFFD08A);
const _bone = Color(0xFFEDE6DA);
const _muted = Color(0xFF9A948A);
const _panel = Color(0xCC0B0D10);
const _line = Color(0x33EDE6DA);
const _blood = Color(0xFFC0392B);

const _tabular = [FontFeature.tabularFigures()];

/// Whether the player is on a phone or tablet, native or in a browser.
bool get isTouchDevice =>
    defaultTargetPlatform == TargetPlatform.iOS ||
    defaultTargetPlatform == TargetPlatform.android;

String get _confirm => isTouchDevice ? 'Tap' : 'Enter';

/// Shown while assets load and render pipelines compile.
class LoadingCover extends StatelessWidget {
  const LoadingCover({super.key});

  @override
  Widget build(BuildContext context) {
    return const ColoredBox(
      color: Colors.black,
      child: Center(
        child: Text(
          'EMBERWATCH',
          style: TextStyle(
            color: _muted,
            fontSize: 14,
            letterSpacing: 8,
            decoration: TextDecoration.none,
          ),
        ),
      ),
    );
  }
}

String _clock(double seconds) {
  final s = seconds.floor();
  return '${(s ~/ 60).toString().padLeft(2, '0')}:'
      '${(s % 60).toString().padLeft(2, '0')}';
}

/// Everything drawn over the 3D view: the in-run HUD and the modal screens.
class GameOverlay extends StatelessWidget {
  const GameOverlay({super.key, required this.game});

  final Game game;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: game,
      builder: (context, _) {
        final phase = game.phase;
        final inRun = phase.index > GamePhase.title.index;
        return DefaultTextStyle(
          style: const TextStyle(
            color: _bone,
            fontSize: 14,
            letterSpacing: 0.4,
            decoration: TextDecoration.none,
          ),
          child: Stack(
            fit: StackFit.expand,
            children: [
              if (inRun) _HurtVignette(game: game),
              if (inRun) _Hud(game: game),
              if (phase == GamePhase.playing)
                SafeArea(
                  child: Align(
                    alignment: Alignment.topRight,
                    child: Padding(
                      padding: const EdgeInsets.only(top: 76, right: 14),
                      child: _Chip(
                        onTap: game.togglePause,
                        child: const Icon(Icons.pause, size: 18, color: _bone),
                      ),
                    ),
                  ),
                ),
              if (phase == GamePhase.warming) const LoadingCover(),
              if (phase == GamePhase.title) _TitleScreen(game: game),
              if (phase == GamePhase.levelUp) _LevelUp(game: game),
              if (phase == GamePhase.paused)
                _Banner(
                  title: 'PAUSED',
                  lines: const [],
                  action: '$_confirm to resume',
                  footer: _SettingsRow(game: game),
                  onTap: game.togglePause,
                ),
              if (phase == GamePhase.dead)
                _Banner(
                  title: 'THE NIGHT TOOK YOU',
                  accent: _blood,
                  lines: _summary(game),
                  action: '$_confirm to try again',
                  onTap: game.start,
                ),
              if (phase == GamePhase.victory)
                _Banner(
                  title: 'DAWN',
                  accent: _emberHot,
                  lines: _summary(game),
                  action: '$_confirm to stand watch again',
                  onTap: game.start,
                ),
            ],
          ),
        );
      },
    );
  }

  static List<String> _summary(Game game) => [
    'Survived ${_clock(game.time)}',
    '${game.kills} kills  ·  level ${game.level}',
    if (game.newBest) 'New best',
  ];
}

class _HurtVignette extends StatelessWidget {
  const _HurtVignette({required this.game});

  final Game game;

  @override
  Widget build(BuildContext context) {
    final player = game.player;
    final low = 1 - (player.health / player.maxHealth / 0.35).clamp(0.0, 1.0);
    final strength = math.max(player.hurtFlash * 0.55, low * 0.4);
    if (strength <= 0.01) return const SizedBox.shrink();
    return IgnorePointer(
      child: DecoratedBox(
        decoration: BoxDecoration(
          gradient: RadialGradient(
            radius: 0.95,
            colors: [
              Colors.transparent,
              _blood.withValues(alpha: strength),
            ],
            stops: const [0.55, 1],
          ),
        ),
      ),
    );
  }
}
