import 'package:flutter/foundation.dart';

/// How much the renderer and the simulation are allowed to spend.
enum Quality {
  low('Low', swarmDensity: 0.55),
  medium('Medium', swarmDensity: 0.8),
  high('High', swarmDensity: 1);

  const Quality(this.label, {required this.swarmDensity});

  final String label;

  /// Fraction of the full enemy cap that may be alive at once.
  final double swarmDensity;

  Quality get next => values[(index + 1) % values.length];

  static Quality get platformDefault {
    final phone =
        defaultTargetPlatform == TargetPlatform.iOS ||
        defaultTargetPlatform == TargetPlatform.android;
    // The web backend is WebGL2, a tier below the native one on any device.
    if (kIsWeb) return phone ? low : medium;
    return phone ? medium : high;
  }
}
