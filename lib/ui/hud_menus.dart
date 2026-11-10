part of 'hud.dart';

class _Scrim extends StatelessWidget {
  const _Scrim({required this.child, this.onTap});

  final Widget child;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: onTap,
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 6, sigmaY: 6),
        child: ColoredBox(
          color: const Color(0x99050608),
          child: Center(child: child),
        ),
      ),
    );
  }
}

class _Banner extends StatelessWidget {
  const _Banner({
    required this.title,
    required this.lines,
    required this.onTap,
    this.action,
    this.footer,
    this.accent = _bone,
  });

  final String title;
  final List<String> lines;
  final String? action;
  final Widget? footer;
  final Color accent;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return _Scrim(
      onTap: onTap,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            title,
            textAlign: TextAlign.center,
            style: TextStyle(
              color: accent,
              fontSize: 44,
              fontWeight: FontWeight.w200,
              letterSpacing: 12,
            ),
          ),
          const SizedBox(height: 18),
          for (final line in lines)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text(
                line,
                style: const TextStyle(fontSize: 16, fontFeatures: _tabular),
              ),
            ),
          if (action != null) ...[
            const SizedBox(height: 34),
            Text(
              action!.toUpperCase(),
              style: const TextStyle(
                color: _muted,
                fontSize: 11,
                letterSpacing: 3,
              ),
            ),
          ],
          if (footer != null) ...[const SizedBox(height: 30), footer!],
        ],
      ),
    );
  }
}

/// A small bordered button.
class _Chip extends StatelessWidget {
  const _Chip({required this.onTap, required this.child});

  final VoidCallback onTap;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      child: GestureDetector(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
          decoration: BoxDecoration(
            color: _panel,
            border: Border.all(color: _line),
          ),
          child: child,
        ),
      ),
    );
  }
}

class _SettingsRow extends StatelessWidget {
  const _SettingsRow({required this.game});

  final Game game;

  @override
  Widget build(BuildContext context) {
    final settings = game.settings;
    const style = TextStyle(fontSize: 11, letterSpacing: 2.4);
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        _Chip(
          onTap: () => game.setQuality(settings.quality.next),
          child: Text(
            'QUALITY  ${settings.quality.label.toUpperCase()}',
            style: style,
          ),
        ),
        const SizedBox(width: 10),
        _Chip(
          onTap: game.toggleMute,
          child: Text('SOUND  ${settings.muted ? 'OFF' : 'ON'}', style: style),
        ),
      ],
    );
  }
}

class _TitleScreen extends StatelessWidget {
  const _TitleScreen({required this.game});

  final Game game;

  @override
  Widget build(BuildContext context) {
    final best = game.settings.bestTime;
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: game.start,
      child: DecoratedBox(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [Color(0xB3050608), Color(0x33050608), Color(0xCC050608)],
          ),
        ),
        child: Column(
          children: [
            const Spacer(flex: 3),
            const Text(
              'EMBERWATCH',
              style: TextStyle(
                fontSize: 64,
                fontWeight: FontWeight.w200,
                letterSpacing: 22,
                shadows: [Shadow(color: _ember, blurRadius: 40)],
              ),
            ),
            const SizedBox(height: 14),
            const Text(
              'Hold the clearing until dawn.',
              style: TextStyle(color: _muted, fontSize: 16, letterSpacing: 1.5),
            ),
            if (best > 0) ...[
              const SizedBox(height: 14),
              Text(
                'BEST  ${_clock(best)}',
                style: const TextStyle(
                  color: _emberHot,
                  fontSize: 11,
                  letterSpacing: 3,
                  fontFeatures: _tabular,
                ),
              ),
            ],
            const Spacer(flex: 8),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 26, vertical: 12),
              decoration: BoxDecoration(border: Border.all(color: _ember)),
              child: Text(
                '${isTouchDevice ? 'TAP' : 'PRESS ENTER'} TO BEGIN',
                style: const TextStyle(
                  color: _emberHot,
                  fontSize: 13,
                  letterSpacing: 4,
                ),
              ),
            ),
            const SizedBox(height: 22),
            Text(
              isTouchDevice
                  ? 'Drag the stick to move  ·  weapons fire on their own'
                  : 'WASD or arrows to move  ·  weapons fire on their own  ·  '
                        'Esc pauses',
              style: const TextStyle(color: _muted, fontSize: 12),
            ),
            const SizedBox(height: 18),
            _SettingsRow(game: game),
            const Spacer(flex: 2),
          ],
        ),
      ),
    );
  }
}
