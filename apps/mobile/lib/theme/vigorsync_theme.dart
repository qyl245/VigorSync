import 'package:material_ui/material_ui.dart';

@immutable
class VigorPalette extends ThemeExtension<VigorPalette> {
  const VigorPalette({
    required this.background,
    required this.backgroundTop,
    required this.surface,
    required this.elevated,
    required this.ink,
    required this.muted,
    required this.line,
    required this.accent,
    required this.highlight,
    required this.cyan,
    required this.signal,
    required this.buttonInk,
  });

  static const dark = VigorPalette(
    background: Color(0xFF090B0A),
    backgroundTop: Color(0xFF0C100D),
    surface: Color(0xFF121512),
    elevated: Color(0xFF191D19),
    ink: Color(0xFFF1F0E7),
    muted: Color(0xFF9EA39A),
    line: Color(0x2CF1F0E7),
    accent: Color(0xFFD9FF43),
    highlight: Color(0xFFD9FF43),
    cyan: Color(0xFF86E8FF),
    signal: Color(0xFFFF644D),
    buttonInk: Color(0xFF10140A),
  );

  static const light = VigorPalette(
    background: Color(0xFFF3F1E8),
    backgroundTop: Color(0xFFFCFBF5),
    surface: Color(0xFFFFFEF9),
    elevated: Color(0xFFE8E7DC),
    ink: Color(0xFF11160E),
    muted: Color(0xFF62695F),
    line: Color(0x2D11160E),
    accent: Color(0xFF526600),
    highlight: Color(0xFFD2F53D),
    cyan: Color(0xFF007383),
    signal: Color(0xFFC93D28),
    buttonInk: Color(0xFF11160E),
  );

  final Color background;
  final Color backgroundTop;
  final Color surface;
  final Color elevated;
  final Color ink;
  final Color muted;
  final Color line;
  final Color accent;
  final Color highlight;
  final Color cyan;
  final Color signal;
  final Color buttonInk;

  @override
  VigorPalette copyWith({
    Color? background,
    Color? backgroundTop,
    Color? surface,
    Color? elevated,
    Color? ink,
    Color? muted,
    Color? line,
    Color? accent,
    Color? highlight,
    Color? cyan,
    Color? signal,
    Color? buttonInk,
  }) {
    return VigorPalette(
      background: background ?? this.background,
      backgroundTop: backgroundTop ?? this.backgroundTop,
      surface: surface ?? this.surface,
      elevated: elevated ?? this.elevated,
      ink: ink ?? this.ink,
      muted: muted ?? this.muted,
      line: line ?? this.line,
      accent: accent ?? this.accent,
      highlight: highlight ?? this.highlight,
      cyan: cyan ?? this.cyan,
      signal: signal ?? this.signal,
      buttonInk: buttonInk ?? this.buttonInk,
    );
  }

  @override
  VigorPalette lerp(covariant VigorPalette? other, double t) {
    if (other == null) return this;
    return VigorPalette(
      background: Color.lerp(background, other.background, t)!,
      backgroundTop: Color.lerp(backgroundTop, other.backgroundTop, t)!,
      surface: Color.lerp(surface, other.surface, t)!,
      elevated: Color.lerp(elevated, other.elevated, t)!,
      ink: Color.lerp(ink, other.ink, t)!,
      muted: Color.lerp(muted, other.muted, t)!,
      line: Color.lerp(line, other.line, t)!,
      accent: Color.lerp(accent, other.accent, t)!,
      highlight: Color.lerp(highlight, other.highlight, t)!,
      cyan: Color.lerp(cyan, other.cyan, t)!,
      signal: Color.lerp(signal, other.signal, t)!,
      buttonInk: Color.lerp(buttonInk, other.buttonInk, t)!,
    );
  }
}

extension VigorThemeContext on BuildContext {
  VigorPalette get vigor => Theme.of(this).extension<VigorPalette>()!;
  bool get isDarkMode => Theme.of(this).brightness == Brightness.dark;
}

ThemeData buildVigorTheme([Brightness brightness = Brightness.dark]) {
  final palette = brightness == Brightness.dark
      ? VigorPalette.dark
      : VigorPalette.light;
  final scheme = brightness == Brightness.dark
      ? ColorScheme.dark(
          primary: palette.accent,
          onPrimary: palette.buttonInk,
          secondary: palette.cyan,
          onSecondary: palette.background,
          error: palette.signal,
          surface: palette.surface,
          onSurface: palette.ink,
          outline: palette.line,
        )
      : ColorScheme.light(
          primary: palette.accent,
          onPrimary: palette.surface,
          secondary: palette.cyan,
          onSecondary: palette.surface,
          error: palette.signal,
          surface: palette.surface,
          onSurface: palette.ink,
          outline: palette.line,
        );
  final base = ThemeData(
    brightness: brightness,
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: palette.background,
    fontFamily: 'sans-serif',
    extensions: [palette],
  );
  return base.copyWith(
    textTheme: base.textTheme.copyWith(
      displayLarge: base.textTheme.displayLarge?.copyWith(
        fontSize: 54,
        fontWeight: FontWeight.w700,
        letterSpacing: -2.4,
        color: palette.ink,
      ),
      headlineLarge: base.textTheme.headlineLarge?.copyWith(
        fontSize: 32,
        fontWeight: FontWeight.w700,
        letterSpacing: -1.2,
        color: palette.ink,
      ),
      titleLarge: base.textTheme.titleLarge?.copyWith(
        fontWeight: FontWeight.w700,
        color: palette.ink,
      ),
      labelLarge: base.textTheme.labelLarge?.copyWith(
        letterSpacing: 1.0,
        fontWeight: FontWeight.w700,
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: palette.elevated,
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(8),
        borderSide: BorderSide(color: palette.line),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(8),
        borderSide: BorderSide(color: palette.line),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(8),
        borderSide: BorderSide(color: palette.accent),
      ),
      labelStyle: TextStyle(color: palette.muted),
      hintStyle: TextStyle(color: palette.muted),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: palette.highlight,
        foregroundColor: palette.buttonInk,
        minimumSize: const Size.fromHeight(54),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
        textStyle: const TextStyle(fontWeight: FontWeight.w800),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: palette.ink,
        minimumSize: const Size.fromHeight(52),
        side: BorderSide(color: palette.line),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      ),
    ),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: palette.surface,
      indicatorColor: palette.accent.withValues(alpha: 0.16),
      labelTextStyle: WidgetStatePropertyAll(
        base.textTheme.labelMedium?.copyWith(
          color: palette.ink,
          letterSpacing: 0.8,
        ),
      ),
    ),
    dividerTheme: DividerThemeData(color: palette.line),
    snackBarTheme: SnackBarThemeData(
      backgroundColor: palette.elevated,
      contentTextStyle: TextStyle(color: palette.ink),
    ),
  );
}

class VigorPage extends StatelessWidget {
  const VigorPage({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    return DecoratedBox(
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [palette.backgroundTop, palette.background],
        ),
      ),
      child: CustomPaint(
        painter: _GridPainter(palette.accent),
        child: child,
      ),
    );
  }
}

class _GridPainter extends CustomPainter {
  const _GridPainter(this.color);

  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color.withValues(alpha: 0.055)
      ..strokeWidth = 1;
    const gap = 44.0;
    for (double x = 0; x < size.width; x += gap) {
      canvas.drawLine(Offset(x, 0), Offset(x, size.height), paint);
    }
    for (double y = 0; y < size.height; y += gap) {
      canvas.drawLine(Offset(0, y), Offset(size.width, y), paint);
    }
  }

  @override
  bool shouldRepaint(covariant _GridPainter oldDelegate) {
    return oldDelegate.color != color;
  }
}

class BrandMark extends StatelessWidget {
  const BrandMark({super.key, this.compact = false});

  final bool compact;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    return Container(
      width: compact ? 42 : 52,
      height: compact ? 32 : 40,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        border: Border.all(color: palette.accent.withValues(alpha: 0.7)),
        borderRadius: BorderRadius.circular(3),
      ),
      child: Text(
        'V/S',
        style: TextStyle(
          color: palette.accent,
          fontSize: compact ? 11 : 13,
          fontWeight: FontWeight.w900,
          letterSpacing: 0.8,
        ),
      ),
    );
  }
}

class ThemeModeButton extends StatelessWidget {
  const ThemeModeButton({
    super.key,
    required this.isDarkMode,
    required this.onPressed,
  });

  final bool isDarkMode;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    return IconButton(
      onPressed: onPressed,
      tooltip: isDarkMode ? '切换浅色模式' : '切换深色模式',
      color: palette.accent,
      icon: Icon(
        isDarkMode ? Icons.light_mode_outlined : Icons.dark_mode_outlined,
      ),
    );
  }
}
