import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'game/quality.dart';

/// Player preferences and the best run, kept across launches.
class Settings {
  Settings._(this._store);

  /// In-memory settings, for when the platform store is unavailable.
  Settings.transient() : _store = null;

  final SharedPreferences? _store;

  Quality quality = Quality.platformDefault;
  bool muted = false;

  /// Longest survival in seconds.
  double bestTime = 0;

  static Future<Settings> load() async {
    try {
      final store = await SharedPreferences.getInstance();
      final settings = Settings._(store);
      final quality = store.getString('quality');
      settings
        ..quality = Quality.values.firstWhere(
          (q) => q.name == quality,
          orElse: () => Quality.platformDefault,
        )
        ..muted = store.getBool('muted') ?? false
        ..bestTime = store.getDouble('bestTime') ?? 0;
      return settings;
    } on Object catch (error) {
      debugPrint('Settings are not persisted: $error');
      return Settings.transient();
    }
  }

  void save() {
    _store
      ?..setString('quality', quality.name)
      ..setBool('muted', muted)
      ..setDouble('bestTime', bestTime);
  }

  /// Records a finished run. Returns true if it is a new best.
  bool recordRun(double seconds) {
    if (seconds <= bestTime) return false;
    bestTime = seconds;
    save();
    return true;
  }
}
