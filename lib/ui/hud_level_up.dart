part of 'hud.dart';

class _LevelUp extends StatelessWidget {
  const _LevelUp({required this.game});

  final Game game;

  @override
  Widget build(BuildContext context) {
    return _Scrim(
      // Three cards are wider and taller than a phone in landscape.
      child: FittedBox(
        fit: BoxFit.scaleDown,
        child: Padding(padding: const EdgeInsets.all(20), child: _cards()),
      ),
    );
  }

  Widget _cards() {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(
          'LEVEL ${game.level}',
          style: const TextStyle(
            color: _emberHot,
            fontSize: 13,
            letterSpacing: 6,
          ),
        ),
        const SizedBox(height: 8),
        const Text(
          'Choose one',
          style: TextStyle(fontSize: 26, fontWeight: FontWeight.w300),
        ),
        const SizedBox(height: 26),
        Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            for (final (index, upgrade) in game.offers.indexed)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 7),
                child: _OfferCard(
                  index: index,
                  upgrade: upgrade,
                  onTap: () => game.choose(index),
                ),
              ),
          ],
        ),
      ],
    );
  }
}

class _OfferCard extends StatefulWidget {
  const _OfferCard({
    required this.index,
    required this.upgrade,
    required this.onTap,
  });

  final int index;
  final Upgrade upgrade;
  final VoidCallback onTap;

  @override
  State<_OfferCard> createState() => _OfferCardState();
}

class _OfferCardState extends State<_OfferCard> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    final upgrade = widget.upgrade;
    final isNew = upgrade.level == 0;
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      child: GestureDetector(
        onTap: widget.onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 120),
          width: 230,
          height: 250,
          padding: const EdgeInsets.all(18),
          decoration: BoxDecoration(
            color: _hovered ? const Color(0xF0181410) : _panel,
            border: Border.all(color: _hovered ? _emberHot : _line),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Icon(upgrade.icon, color: _emberHot, size: 30),
                  const Spacer(),
                  Text(
                    isNew
                        ? 'NEW'
                        : 'LV ${upgrade.level} → ${upgrade.level + 1}',
                    style: TextStyle(
                      color: isNew ? _ember : _muted,
                      fontSize: 11,
                      letterSpacing: 2,
                      fontFeatures: _tabular,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 18),
              Text(
                upgrade.name,
                style: const TextStyle(
                  fontSize: 20,
                  fontWeight: FontWeight.w500,
                ),
              ),
              const SizedBox(height: 4),
              Text(
                upgrade.isWeapon ? 'WEAPON' : 'PASSIVE',
                style: const TextStyle(
                  color: _muted,
                  fontSize: 10,
                  letterSpacing: 2.4,
                ),
              ),
              const SizedBox(height: 14),
              Expanded(
                child: Text(
                  upgrade.nextDescription,
                  style: const TextStyle(fontSize: 14, height: 1.4),
                ),
              ),
              if (!isTouchDevice)
                Text(
                  'PRESS ${widget.index + 1}',
                  style: const TextStyle(
                    color: _muted,
                    fontSize: 10,
                    letterSpacing: 2.4,
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}
