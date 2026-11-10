part of 'hud.dart';

class _Hud extends StatelessWidget {
  const _Hud({required this.game});

  final Game game;

  @override
  Widget build(BuildContext context) {
    final player = game.player;
    return IgnorePointer(
      child: SafeArea(
        child: Column(
          children: [
            _Bar(
              value: game.experience / game.experienceNeeded,
              height: 5,
              colors: const [_ember, _emberHot],
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 12, 20, 0),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Align(
                      alignment: Alignment.centerLeft,
                      child: _Stat(label: 'LEVEL', value: '${game.level}'),
                    ),
                  ),
                  Column(
                    children: [
                      Text(
                        _clock(game.time),
                        style: const TextStyle(
                          fontSize: 34,
                          fontWeight: FontWeight.w300,
                          letterSpacing: 3,
                          fontFeatures: _tabular,
                          shadows: [Shadow(blurRadius: 12)],
                        ),
                      ),
                      const SizedBox(height: 4),
                      SizedBox(
                        width: 150,
                        child: _Bar(
                          value: game.nightProgress,
                          height: 2,
                          colors: const [Color(0xFF5E7FB8), _emberHot],
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        'DAWN IN ${_clock(Director.nightLength - game.time)}',
                        style: const TextStyle(
                          color: _muted,
                          fontSize: 10,
                          letterSpacing: 2.4,
                          fontFeatures: _tabular,
                        ),
                      ),
                    ],
                  ),
                  Expanded(
                    child: Align(
                      alignment: Alignment.centerRight,
                      child: _Stat(
                        label: 'KILLS',
                        value: '${game.kills}',
                        alignEnd: true,
                      ),
                    ),
                  ),
                ],
              ),
            ),
            const Spacer(),
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 18),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  SizedBox(
                    width: 260,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          '${player.health.ceil()} / ${player.maxHealth.round()}',
                          style: const TextStyle(
                            fontSize: 12,
                            letterSpacing: 1.5,
                            fontFeatures: _tabular,
                          ),
                        ),
                        const SizedBox(height: 6),
                        _Bar(
                          value: player.health / player.maxHealth,
                          height: 8,
                          colors: const [Color(0xFF8E1B12), Color(0xFFE25B45)],
                        ),
                      ],
                    ),
                  ),
                  const Spacer(),
                  Wrap(
                    spacing: 6,
                    children: [
                      for (final upgrade in game.owned) _Slot(upgrade: upgrade),
                    ],
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat({
    required this.label,
    required this.value,
    this.alignEnd = false,
  });

  final String label, value;
  final bool alignEnd;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: alignEnd
          ? CrossAxisAlignment.end
          : CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: const TextStyle(
            color: _muted,
            fontSize: 10,
            letterSpacing: 2.4,
          ),
        ),
        Text(
          value,
          style: const TextStyle(
            fontSize: 22,
            fontWeight: FontWeight.w500,
            fontFeatures: _tabular,
            shadows: [Shadow(blurRadius: 10)],
          ),
        ),
      ],
    );
  }
}

class _Bar extends StatelessWidget {
  const _Bar({required this.value, required this.height, required this.colors});

  final double value, height;
  final List<Color> colors;

  @override
  Widget build(BuildContext context) {
    return Container(
      height: height,
      width: double.infinity,
      color: const Color(0x66000000),
      child: FractionallySizedBox(
        alignment: Alignment.centerLeft,
        widthFactor: value.clamp(0.0, 1.0),
        child: DecoratedBox(
          decoration: BoxDecoration(gradient: LinearGradient(colors: colors)),
        ),
      ),
    );
  }
}

class _Slot extends StatelessWidget {
  const _Slot({required this.upgrade});

  final Upgrade upgrade;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 40,
      padding: const EdgeInsets.symmetric(vertical: 5),
      decoration: BoxDecoration(
        color: _panel,
        border: Border.all(color: upgrade.isWeapon ? _ember : _line),
      ),
      child: Column(
        children: [
          Icon(
            upgrade.icon,
            size: 18,
            color: upgrade.isWeapon ? _emberHot : _bone,
          ),
          const SizedBox(height: 3),
          Text(
            upgrade.maxed ? 'MAX' : '${upgrade.level}',
            style: const TextStyle(
              fontSize: 9,
              letterSpacing: 1,
              fontFeatures: _tabular,
            ),
          ),
        ],
      ),
    );
  }
}
