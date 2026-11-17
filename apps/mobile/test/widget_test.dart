import 'package:flutter_test/flutter_test.dart';
import 'package:material_ui/material_ui.dart';

import 'package:vigorsync_mobile/features/pairing/pairing_page.dart';
import 'package:vigorsync_mobile/models/session.dart';
import 'package:vigorsync_mobile/theme/vigorsync_theme.dart';
import 'package:vigorsync_mobile/utils/session_format.dart';
import 'package:vigorsync_mobile/widgets/exercise_glyph.dart';

void main() {
  testWidgets('pairing page renders the mobile entry flow', (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      MaterialApp(
        theme: buildVigorTheme(),
        home: PairingPage(
          onPaired: (_) async {},
          isDarkMode: true,
          onToggleTheme: () {},
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('连接你的\n训练空间'), findsOneWidget);
    expect(find.text('CORE ADDRESS'), findsOneWidget);
    expect(find.text('PAIRING CODE'), findsOneWidget);
    expect(find.text('连接 VigorSync  →'), findsOneWidget);
  });

  test('session parses live summary', () {
    final session = SessionRecord.fromJson({
      'session_id': 's_test123',
      'status': 'analyzing',
      'exercise': 'squat',
      'view': 'front',
      'target_reps': 12,
      'created_at': '2026-11-16T00:00:00Z',
      'started_at': null,
      'ended_at': null,
      'job_id': null,
      'report_url': null,
      'live_summary': {
        'reps': 10,
        'confidence': 0.9,
        'duration_s': 20,
        'alert_counts': <String, int>{},
      },
    });

    expect(session.sessionId, 's_test123');
    expect(session.liveSummary?['reps'], 10);
    expect(session.isActive, isFalse);
  });

  test('light and dark themes expose matching palettes', () {
    final light = buildVigorTheme(Brightness.light);
    final dark = buildVigorTheme(Brightness.dark);

    expect(light.brightness, Brightness.light);
    expect(dark.brightness, Brightness.dark);
    expect(light.extension<VigorPalette>(), VigorPalette.light);
    expect(dark.extension<VigorPalette>(), VigorPalette.dark);
  });

  test('session timestamps render in local time', () {
    final local = DateTime.parse('2026-11-16T14:31:45.373Z').toLocal();
    final formatted = formatSessionTime(
      '2026-11-16T14:31:45.373Z',
      now: DateTime(local.year, local.month, local.day, 23),
    );

    expect(formatted, startsWith('今天 '));
    expect(formatted, isNot(contains('Z')));
    expect(
      formatSessionDuration(65),
      '1 分 5 秒',
    );
    expect(sessionStatusLabel('analyzing'), '分析中');
    expect(sessionViewLabel('side_right'), '右侧');
  });

  testWidgets('exercise glyphs identify squat and pushup', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: buildVigorTheme(),
        home: const Row(
          children: [
            ExerciseGlyph(exercise: 'squat', color: Colors.blue),
            ExerciseGlyph(exercise: 'pushup', color: Colors.blue),
          ],
        ),
      ),
    );

    expect(find.bySemanticsLabel('深蹲动作'), findsOneWidget);
    expect(find.bySemanticsLabel('俯卧撑动作'), findsOneWidget);
  });
}
