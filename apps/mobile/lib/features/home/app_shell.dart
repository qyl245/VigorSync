import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:material_ui/material_ui.dart';

import '../../data/core_api_client.dart';
import '../../models/session.dart';
import '../../theme/vigorsync_theme.dart';
import '../history/history_page.dart';
import 'home_page.dart';

class AppShell extends StatefulWidget {
  const AppShell({
    super.key,
    required this.credentials,
    required this.isDarkMode,
    required this.onToggleTheme,
    required this.onLogout,
  });

  final CoreCredentials credentials;
  final bool isDarkMode;
  final VoidCallback onToggleTheme;
  final Future<void> Function() onLogout;

  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  late final CoreApiClient _api;
  WebSocket? _socket;
  StreamSubscription<dynamic>? _socketSubscription;
  Timer? _reconnectTimer;
  int _index = 0;
  int _refreshToken = 0;

  @override
  void initState() {
    super.initState();
    _api = CoreApiClient(widget.credentials);
    _connectEvents();
  }

  @override
  void dispose() {
    _reconnectTimer?.cancel();
    _socketSubscription?.cancel();
    _socket?.close();
    _api.close();
    super.dispose();
  }

  Future<void> _connectEvents() async {
    try {
      final socket = await _api.connectClientSocket();
      if (!mounted) {
        await socket.close();
        return;
      }
      _socket = socket;
      _socketSubscription = socket.listen(
        (message) {
          final decoded = jsonDecode(message as String);
          if (decoded is! Map<String, dynamic>) return;
          final type = decoded['type'];
          if (type == 'session.snapshot' ||
              type == 'session.state' ||
              type == 'session.deleted' ||
              type == 'report.progress' ||
              type == 'report.ready') {
            _refresh();
          }
        },
        onDone: _reconnectEvents,
        onError: (_) => _reconnectEvents(),
      );
    } catch (_) {
      _reconnectEvents();
    }
  }

  void _reconnectEvents() {
    if (!mounted) return;
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(const Duration(seconds: 2), _connectEvents);
  }

  void _refresh() => setState(() => _refreshToken++);

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: VigorPage(
        child: IndexedStack(
          index: _index,
          children: [
            HomePage(
              api: _api,
              refreshToken: _refreshToken,
              isDarkMode: widget.isDarkMode,
              onToggleTheme: widget.onToggleTheme,
              onChanged: _refresh,
              onLogout: widget.onLogout,
            ),
            HistoryPage(
              api: _api,
              refreshToken: _refreshToken,
              isDarkMode: widget.isDarkMode,
              onToggleTheme: widget.onToggleTheme,
            ),
          ],
        ),
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (value) => setState(() => _index = value),
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.bolt_outlined),
            selectedIcon: Icon(Icons.bolt),
            label: '训练',
          ),
          NavigationDestination(
            icon: Icon(Icons.view_timeline_outlined),
            selectedIcon: Icon(Icons.view_timeline),
            label: '历史',
          ),
        ],
      ),
    );
  }
}
