import 'dart:async';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:material_ui/material_ui.dart';

import 'package:vigorsync_mobile/data/core_api_client.dart';
import 'package:vigorsync_mobile/data/lan_discovery.dart';
import 'package:vigorsync_mobile/features/dashboard/dashboard_page.dart';
import 'package:vigorsync_mobile/features/pairing/pairing_page.dart';
import 'package:vigorsync_mobile/features/profile/profile_page.dart';
import 'package:vigorsync_mobile/models/session.dart';
import 'package:vigorsync_mobile/theme/vigorsync_theme.dart';
import 'package:vigorsync_mobile/utils/session_format.dart';
import 'package:vigorsync_mobile/widgets/exercise_glyph.dart';

class _DashboardApi extends CoreApiClient {
  _DashboardApi(this.plans)
    : super(
        const CoreCredentials(
          baseUrl: 'http://127.0.0.1:8000',
          token: 'test-token',
        ),
      );

  final List<Map<String, dynamic>> plans;

  @override
  Future<List<Map<String, dynamic>>> getTrend({int days = 7}) async => [];

  @override
  Future<Map<String, dynamic>> getTodos({String? date}) async => {
    'items': <dynamic>[],
    'completed': 0,
    'total': 0,
  };

  @override
  Future<List<Map<String, dynamic>>> getWeeklyPlans() async => plans;
}

class _ProfileApi extends CoreApiClient {
  _ProfileApi({this.offline = false})
    : super(
        const CoreCredentials(
          baseUrl: 'http://192.168.1.12:8011',
          token: 'test-token',
        ),
      );

  final bool offline;

  @override
  Future<Map<String, dynamic>> getMirrorLayout() async {
    if (offline) throw const SocketException('offline');
    return {
      'modules': [
        {'id': 'weather', 'size': '1x1', 'column': 'left', 'order': 0},
        {'id': 'trend', 'size': '1x1', 'column': 'right', 'order': 0},
        {'id': 'todo', 'size': '1x1', 'column': 'left', 'order': 1},
        {'id': 'weekly_plan', 'size': '1x2', 'column': 'right', 'order': 1},
      ],
    };
  }

  @override
  Future<List<Map<String, dynamic>>> listPairedDevices() async {
    if (offline) throw const SocketException('offline');
    return [
      {
        'device_name': 'Xiaomi 15',
        'platform': 'android',
        'created_at': '2026-11-18T09:00:00Z',
      },
    ];
  }

  @override
  Future<Map<String, dynamic>> updateMirrorLayout(
    List<Map<String, dynamic>> modules,
  ) async => {'modules': modules};
}

Future<void> _pumpDashboard(
  WidgetTester tester,
  List<Map<String, dynamic>> plans, {
  bool connected = true,
}) async {
  tester.view.physicalSize = const Size(390, 844);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);

  await tester.pumpWidget(
    MaterialApp(
      theme: buildVigorTheme(),
      home: Scaffold(
        body: DashboardPage(
          api: _DashboardApi(plans),
          refreshToken: 0,
          connected: connected,
          onStartTraining: ({
            required exercise,
            required view,
            required targetReps,
          }) async {},
        ),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  test('core connectivity and auth failures return to pairing', () {
    expect(shouldReturnToPairing(SocketException('offline')), isTrue);
    expect(shouldReturnToPairing(TimeoutException('timeout')), isTrue);
    expect(
      shouldReturnToPairing(const CoreApiException('expired', statusCode: 401)),
      isTrue,
    );
    expect(
      shouldReturnToPairing(
        const CoreApiException('forbidden', statusCode: 403),
      ),
      isTrue,
    );
    expect(
      shouldReturnToPairing(
        const CoreApiException('server error', statusCode: 500),
      ),
      isFalse,
    );
    expect(shouldReturnToPairing(FormatException('bad payload')), isFalse);
  });
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

  testWidgets('dashboard disables both arrows with one plan item', (
    tester,
  ) async {
    final weekday = DateTime.now().weekday;
    await _pumpDashboard(tester, [
      {
        'id': 'plan-1',
        'weekday': weekday,
        'exercise': 'squat',
        'view': 'front',
        'sets': 4,
        'target_reps': 12,
        'sort_order': 0,
      },
    ]);

    expect(find.text('今日计划'), findsOneWidget);
    expect(find.text('1 / 1'), findsOneWidget);
    expect(find.text('天气'), findsNothing);

    final back = tester.widget<IconButton>(
      find.widgetWithIcon(IconButton, Icons.arrow_back_rounded),
    );
    final forward = tester.widget<IconButton>(
      find.widgetWithIcon(IconButton, Icons.arrow_forward_rounded),
    );
    expect(back.onPressed, isNull);
    expect(forward.onPressed, isNull);

    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('dashboard switches between today plan items', (tester) async {
    final weekday = DateTime.now().weekday;
    await _pumpDashboard(tester, [
      {
        'id': 'plan-1',
        'weekday': weekday,
        'exercise': 'squat',
        'view': 'front',
        'sets': 4,
        'target_reps': 12,
        'sort_order': 0,
      },
      {
        'id': 'plan-2',
        'weekday': weekday,
        'exercise': 'pushup',
        'view': 'front',
        'sets': 3,
        'target_reps': 15,
        'sort_order': 1,
      },
    ]);

    expect(find.text('1 / 2'), findsOneWidget);
    expect(find.text('深蹲'), findsOneWidget);

    await tester.tap(
      find.widgetWithIcon(IconButton, Icons.arrow_forward_rounded),
    );
    await tester.pumpAndSettle();

    expect(find.text('2 / 2'), findsOneWidget);
    expect(find.text('俯卧撑'), findsOneWidget);

    final back = tester.widget<IconButton>(
      find.widgetWithIcon(IconButton, Icons.arrow_back_rounded),
    );
    final forward = tester.widget<IconButton>(
      find.widgetWithIcon(IconButton, Icons.arrow_forward_rounded),
    );
    expect(back.onPressed, isNotNull);
    expect(forward.onPressed, isNull);

    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('dashboard requires the realtime socket for synced status', (
    tester,
  ) async {
    final weekday = DateTime.now().weekday;
    await _pumpDashboard(tester, [
      {
        'id': 'plan-1',
        'weekday': weekday,
        'exercise': 'squat',
        'view': 'front',
        'sets': 4,
        'target_reps': 12,
        'sort_order': 0,
      },
    ], connected: false);

    expect(find.text('未连接'), findsOneWidget);
    expect(find.text('已同步'), findsNothing);

    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('profile page shows settings overview and layout entry', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    var toggled = false;

    await tester.pumpWidget(
      MaterialApp(
        theme: buildVigorTheme(),
        home: Scaffold(
          body: ProfilePage(
            api: _ProfileApi(),
            refreshToken: 0,
            connected: true,
            isDarkMode: true,
            onToggleTheme: () => toggled = true,
            onLogout: () async {},
            onReconnect: () async => false,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('设备与偏好'), findsOneWidget);
    expect(find.text('4 个模块 · 左 2 / 右 2'), findsOneWidget);
    expect(find.text('深色模式'), findsOneWidget);
    expect(find.text('已配对设备'), findsOneWidget);

    await tester.tap(find.byType(Switch));
    await tester.pump();
    expect(toggled, isTrue);

    await tester.tap(find.text('编辑布局'));
    await tester.pumpAndSettle();
    expect(find.text('编辑镜端布局'), findsOneWidget);
    expect(find.text('卡片大小'), findsWidgets);

    await tester.tap(find.byTooltip('关闭'));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(find.text('解除当前配对'), 260);
    expect(find.text('解除当前配对'), findsOneWidget);

    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('profile page exposes manual LAN reconnect when offline', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    var reconnectRequested = false;

    await tester.pumpWidget(
      MaterialApp(
        theme: buildVigorTheme(),
        home: Scaffold(
          body: ProfilePage(
            api: _ProfileApi(offline: true),
            refreshToken: 0,
            connected: false,
            isDarkMode: true,
            onToggleTheme: () {},
            onLogout: () async {},
            onReconnect: () async {
              reconnectRequested = true;
              return false;
            },
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('当前离线'), findsOneWidget);
    expect(find.text('自动连接'), findsOneWidget);
    await tester.tap(find.text('自动连接'));
    await tester.pumpAndSettle();
    expect(reconnectRequested, isTrue);

    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets(
    'profile remains offline when REST works but realtime socket is down',
    (tester) async {
      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(
        MaterialApp(
          theme: buildVigorTheme(),
          home: Scaffold(
            body: ProfilePage(
              api: _ProfileApi(),
              refreshToken: 0,
              connected: false,
              isDarkMode: true,
              onToggleTheme: () {},
              onLogout: () async {},
              onReconnect: () async => false,
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('当前离线'), findsOneWidget);
      expect(find.text('已连接'), findsNothing);
      expect(find.text('实时通道未建立，请重新连接'), findsOneWidget);
      expect(find.text('无法读取镜端布局'), findsOneWidget);

      await tester.pumpWidget(const SizedBox.shrink());
    },
  );

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

  test('LAN discovery response is validated and normalized', () {
    final discovered = parseDiscoveryResponse(
      '{"service":"vigorsync-core","version":1,"core_url":"http://192.168.1.20:8011/","device_name":"客厅镜端","hostname":"mirror-pc"}',
    );

    expect(discovered?.coreUrl, 'http://192.168.1.20:8011');
    expect(discovered?.deviceName, '客厅镜端');
    expect(discovered?.hostname, 'mirror-pc');
    expect(parseDiscoveryResponse('not-json'), isNull);
    expect(
      parseDiscoveryResponse(
        '{"service":"other","core_url":"http://192.168.1.20:8011"}',
      ),
      isNull,
    );
    expect(
      parseDiscoveryResponse(
        '{"service":"vigorsync-core","core_url":"ftp://192.168.1.20:8011"}',
      ),
      isNull,
    );
  });

  test('session timestamps render in local time', () {
    final local = DateTime.parse('2026-11-16T14:31:45.373Z').toLocal();
    final formatted = formatSessionTime(
      '2026-11-16T14:31:45.373Z',
      now: DateTime(local.year, local.month, local.day, 23),
    );

    expect(formatted, startsWith('今天 '));
    expect(formatted, isNot(contains('Z')));
    expect(formatSessionDuration(65), '1 分 5 秒');
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
