import 'package:emberwatch/game/quality.dart';
import 'package:emberwatch/settings.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('a first launch gets the platform defaults', () async {
    SharedPreferences.setMockInitialValues({});

    final settings = await Settings.load();

    expect(settings.quality, Quality.platformDefault);
    expect(settings.muted, isFalse);
    expect(settings.bestTime, 0);
  });

  test('saved choices come back on the next load', () async {
    SharedPreferences.setMockInitialValues({});
    final settings = await Settings.load();

    settings
      ..quality = Quality.low
      ..muted = true
      ..save();
    final reloaded = await Settings.load();

    expect(reloaded.quality, Quality.low);
    expect(reloaded.muted, isTrue);
  });

  test('an unknown saved quality falls back to the default', () async {
    SharedPreferences.setMockInitialValues({'quality': 'ultra'});

    final settings = await Settings.load();

    expect(settings.quality, Quality.platformDefault);
  });

  test('only a longer run replaces the best time', () async {
    SharedPreferences.setMockInitialValues({'bestTime': 120.0});
    final settings = await Settings.load();

    expect(settings.recordRun(90), isFalse);
    expect(settings.recordRun(200), isTrue);

    expect((await Settings.load()).bestTime, 200);
  });

  test('quality cycles through every tier', () {
    expect(Quality.low.next, Quality.medium);
    expect(Quality.high.next, Quality.low);
  });
}
