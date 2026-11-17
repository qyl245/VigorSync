import 'package:material_ui/material_ui.dart';

class ExerciseGlyph extends StatelessWidget {
  const ExerciseGlyph({
    super.key,
    required this.exercise,
    required this.color,
    this.size = 30,
  });

  final String exercise;
  final Color color;
  final double size;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: exercise == 'squat' ? '深蹲动作' : '俯卧撑动作',
      image: true,
      child: CustomPaint(
        size: Size.square(size),
        painter: _ExerciseGlyphPainter(exercise: exercise, color: color),
      ),
    );
  }
}

class _ExerciseGlyphPainter extends CustomPainter {
  const _ExerciseGlyphPainter({required this.exercise, required this.color});

  final String exercise;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final unit = size.shortestSide;
    final stroke = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = unit * 0.075
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;
    final head = Paint()
      ..color = color
      ..style = PaintingStyle.fill;

    if (exercise == 'squat') {
      _paintSquat(canvas, unit, stroke, head);
    } else {
      _paintPushup(canvas, unit, stroke, head);
    }
  }

  void _paintSquat(Canvas canvas, double unit, Paint stroke, Paint head) {
    final body = Path()
      ..moveTo(unit * 0.48, unit * 0.31)
      ..lineTo(unit * 0.40, unit * 0.56)
      ..lineTo(unit * 0.69, unit * 0.65)
      ..lineTo(unit * 0.63, unit * 0.89);
    canvas.drawPath(body, stroke);
    canvas.drawCircle(Offset(unit * 0.50, unit * 0.17), unit * 0.09, head);

    final arms = Path()
      ..moveTo(unit * 0.47, unit * 0.36)
      ..lineTo(unit * 0.72, unit * 0.43)
      ..moveTo(unit * 0.46, unit * 0.36)
      ..lineTo(unit * 0.72, unit * 0.65);
    canvas.drawPath(arms, stroke);

    final backFoot = Path()
      ..moveTo(unit * 0.63, unit * 0.89)
      ..lineTo(unit * 0.76, unit * 0.89);
    canvas.drawPath(backFoot, stroke);
  }

  void _paintPushup(Canvas canvas, double unit, Paint stroke, Paint head) {
    final ground = Paint()
      ..color = color.withValues(alpha: 0.35)
      ..strokeWidth = unit * 0.035
      ..strokeCap = StrokeCap.round;
    canvas.drawLine(
      Offset(unit * 0.10, unit * 0.89),
      Offset(unit * 0.90, unit * 0.89),
      ground,
    );

    canvas.drawCircle(Offset(unit * 0.17, unit * 0.42), unit * 0.09, head);
    final body = Path()
      ..moveTo(unit * 0.28, unit * 0.49)
      ..lineTo(unit * 0.58, unit * 0.59)
      ..lineTo(unit * 0.82, unit * 0.78);
    canvas.drawPath(body, stroke);

    final arm = Path()
      ..moveTo(unit * 0.31, unit * 0.49)
      ..lineTo(unit * 0.40, unit * 0.73)
      ..lineTo(unit * 0.30, unit * 0.85);
    canvas.drawPath(arm, stroke);

    final foot = Path()
      ..moveTo(unit * 0.82, unit * 0.78)
      ..lineTo(unit * 0.90, unit * 0.86);
    canvas.drawPath(foot, stroke);
  }

  @override
  bool shouldRepaint(covariant _ExerciseGlyphPainter oldDelegate) {
    return oldDelegate.exercise != exercise || oldDelegate.color != color;
  }
}
