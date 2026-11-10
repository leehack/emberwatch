import 'dart:math' as math;

import 'package:flutter/foundation.dart';
import 'package:flutter_scene/audio.dart';
import 'package:flutter_scene/scene.dart';
import 'package:flutter_scene_soloud/flutter_scene_soloud.dart';

enum Sound {
  shot(0.32, 0.05),
  explosion(0.8, 0.12),
  zap(0.4, 0.1),
  squish(0.3, 0.045),
  ember(0.22, 0.04),
  levelup(0.55, 0.5),
  hurt(0.6, 0.3),
  pulse(0.6, 0.2),
  growl(0.9, 1);

  const Sound(this.volume, this.minInterval);

  final double volume;

  /// Seconds that must pass before the same sound may play again, so a
  /// crowd dying at once does not stack into one loud burst.
  final double minInterval;
}

/// One-shot effects and the looping night ambience. Stays silent if the
/// audio backend cannot start.
class Sfx {
  /// A late-game burst sums to about 1.75 times full scale; this keeps it
  /// under the mixer's clipping point.
  static const double _headroom = 0.6;

  final math.Random _rng = math.Random(3);
  final Map<Sound, AudioClip> _clips = {};
  final Map<Sound, double> _lastPlayed = {};
  AudioEngine? _engine;
  AudioVoice? _ambience;
  double _clock = 0;
  bool _muted = false;

  set muted(bool value) {
    _muted = value;
    _engine?.masterVolume = value ? 0 : _headroom;
  }

  Future<void> load(Scene scene) async {
    try {
      final engine = SoloudAudioEngine();
      scene.root.addComponent(engine);
      for (final sound in Sound.values) {
        _clips[sound] = await engine.loadClip('assets/audio/${sound.name}.wav');
      }
      _ambience =
          engine.createVoice(await engine.loadClip('assets/audio/ambience.wav'))
            ..looping = true
            ..setPositional(false)
            ..volume = 0.5
            ..start();
      _engine = engine;
      muted = _muted;
    } on Object catch (error) {
      debugPrint('Audio disabled: $error');
    }
  }

  void update(double dt) => _clock += dt;

  void play(Sound sound, {double volume = 1, double pitch = 1}) {
    final engine = _engine;
    if (engine == null) return;
    final last = _lastPlayed[sound];
    if (last != null && _clock - last < sound.minInterval) return;
    _lastPlayed[sound] = _clock;
    engine.playOneShot(
      _clips[sound]!,
      volume: sound.volume * volume,
      pitch: pitch * (0.94 + _rng.nextDouble() * 0.12),
    );
  }

  void dispose() => _ambience?.stop();
}
