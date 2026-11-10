import 'dart:async';
import 'dart:ui' as ui;

import 'package:flutter/foundation.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/scheduler.dart';
import 'package:flutter/widgets.dart';

import 'host_io.dart' if (dart.library.js_interop) 'host_web.dart' as host;

/// Scripted runs. Options come from environment variables on desktop and URL
/// query parameters on the web, both outside release builds only, and in
/// any build from `--dart-define=DEBUG_OPTIONS=AUTOPLAY=1,SKIP_TO=120`.
class DebugOptions {
  DebugOptions._(this._values);

  factory DebugOptions.read() {
    const defined = String.fromEnvironment('DEBUG_OPTIONS');
    return DebugOptions._({
      // A release build ignores its environment and URL, so only options
      // compiled into it apply.
      if (!kReleaseMode)
        for (final key in _keys) key: ?host.hostOptions()[key],
      for (final pair in defined.split(','))
        if (pair.split('=') case [final key, final value]) key: value,
    });
  }

  static const _keys = [
    'SHOT_DIR',
    'SHOT_TIMES',
    'AUTOPLAY',
    'SKIP_TO',
    'DISARM',
    'FORCE',
    'CAM_ZOOM',
    'QUALITY',
  ];

  final Map<String, String> _values;

  bool get enabled => _values.isNotEmpty;

  /// Directory that captured frames are written to (desktop only).
  String? get shotDirectory => _values['SHOT_DIR'];

  /// Drives the player with a bot.
  bool get autoplay => _values['AUTOPLAY'] == '1';

  /// Seconds into the night to start at.
  double get skipTo => double.tryParse(_values['SKIP_TO'] ?? '') ?? 0;

  /// Stops the weapons, to watch enemies approach.
  bool get disarmed => _values['DISARM'] == '1';

  /// `title` to stay on the title screen, or a state for `Game.debugForce`.
  String? get force => _values['FORCE'];

  /// Scales the camera's distance, to inspect models up close.
  double get zoom => double.tryParse(_values['CAM_ZOOM'] ?? '') ?? 1;

  /// `low`, `medium` or `high`, overriding the saved setting.
  String? get quality => _values['QUALITY'];

  /// Logs frame timings every two seconds. `late` counts frames that began
  /// more than one and a half refresh intervals after the previous one.
  void logFrameTimes() {
    var frames = 0, buildMicros = 0, rasterMicros = 0;
    var worstBuild = 0, worstGap = 0, late = 0, lastStart = 0;
    final refresh =
        1e6 /
        WidgetsBinding
            .instance
            .platformDispatcher
            .views
            .first
            .display
            .refreshRate;
    SchedulerBinding.instance.addTimingsCallback((timings) {
      for (final timing in timings) {
        frames++;
        final build = timing.buildDuration.inMicroseconds;
        buildMicros += build;
        rasterMicros += timing.rasterDuration.inMicroseconds;
        if (build > worstBuild) worstBuild = build;
        final start = timing.timestampInMicroseconds(ui.FramePhase.vsyncStart);
        final gap = lastStart == 0 ? 0 : start - lastStart;
        lastStart = start;
        if (gap > worstGap) worstGap = gap;
        if (gap > refresh * 1.5) late++;
      }
    });
    Timer.periodic(const Duration(seconds: 2), (_) {
      if (frames == 0) return;
      debugPrint(
        'frames: ${frames ~/ 2}/s  '
        'ui ${(buildMicros / frames / 1000).toStringAsFixed(1)} ms  '
        'raster ${(rasterMicros / frames / 1000).toStringAsFixed(1)} ms  '
        'worst ui ${(worstBuild / 1000).toStringAsFixed(1)} ms  '
        'worst gap ${(worstGap / 1000).toStringAsFixed(1)} ms  '
        'late $late',
      );
      frames = buildMicros = rasterMicros = 0;
      worstBuild = worstGap = late = 0;
    });
  }

  /// Saves the contents of [boundaryKey] at each of `SHOT_TIMES` (seconds
  /// from now), then exits.
  void scheduleShots(GlobalKey boundaryKey) {
    final dir = shotDirectory;
    if (dir == null) return;
    final times =
        (_values['SHOT_TIMES'] ?? '4').split(',').map(double.parse).toList()
          ..sort();
    for (final (index, time) in times.indexed) {
      Timer(Duration(milliseconds: (time * 1000).round()), () async {
        // The scene repaints every frame, so the boundary is only clean
        // right after a frame ends.
        await WidgetsBinding.instance.endOfFrame;
        final boundary =
            boundaryKey.currentContext!.findRenderObject()
                as RenderRepaintBoundary;
        final image = await boundary.toImage(pixelRatio: 1);
        final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
        host.saveFrame(
          '$dir/shot_${index.toString().padLeft(2, '0')}.png',
          bytes!.buffer.asUint8List(),
        );
        if (index == times.length - 1) host.quit();
      });
    }
  }
}
