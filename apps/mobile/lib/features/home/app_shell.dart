import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:material_ui/material_ui.dart';

import '../../data/core_api_client.dart';
import '../../data/core_rediscovery.dart';
import '../../models/session.dart';
import '../../theme/vigorsync_theme.dart';
import '../dashboard/dashboard_page.dart';
import '../history/history_page.dart';
import '../profile/profile_page.dart';
import '../session/live_session_page.dart';
import 'home_page.dart';

class AppShell extends StatefulWidget {
  const AppShell({
    super.key,
    required this.credentials,
    required this.isDarkMode,
    required this.onToggleTheme,
    required this.onLogout,
    required this.onCredentialsUpdated,
    required this.onCoreUnavailable,
  });

  final CoreCredentials credentials;
  final bool isDarkMode;
  final VoidCallback onToggleTheme;
  final Future<void> Function() onLogout;
  final Future<void> Function(CoreCredentials credentials) onCredentialsUpdated;
  final Future<void> Function() onCoreUnavailable;

  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  static const _focusPages = ['home', 'training', 'history', 'profile'];
  static const _maxDiscoveryRounds = 3;

  late CoreApiClient _api;
  final CoreRediscoveryService _recovery = const CoreRediscoveryService();
  WebSocket? _socket;
  StreamSubscription<dynamic>? _socketSubscription;
  Timer? _reconnectTimer;
  Future<void> _themeSync = Future<void>.value();
  int _connectionFailures = 0;
  bool _disconnectHandled = false;
  bool _fallingBackToPairing = false;
  bool _rediscovering = false;
  int _discoveryRounds = 0;
  bool _socketConnected = false;
  int _index = 0;
  int _refreshToken = 0;

  @override
  void initState() {
    super.initState();
    _api = CoreApiClient(widget.credentials);
    _connectEvents();
    _syncFocus();
    _queueMirrorThemeSync();
  }

  @override
  void didUpdateWidget(covariant AppShell oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.isDarkMode != widget.isDarkMode) {
      _queueMirrorThemeSync();
    }
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
    if (!mounted || _fallingBackToPairing || _socket != null) return;
    try {
      final socket = await _api.connectClientSocket();
      if (!mounted) {
        await socket.close();
        return;
      }
      _connectionFailures = 0;
      _disconnectHandled = false;
      _discoveryRounds = 0;
      _socket = socket;
      setState(() => _socketConnected = true);
      _queueMirrorThemeSync();
      _socketSubscription = socket.listen(
        (message) {
          try {
            final decoded = jsonDecode(message as String);
            if (decoded is! Map<String, dynamic>) return;
            final type = decoded['type'];
            if (type == 'session.snapshot' ||
                type == 'session.state' ||
                type == 'session.deleted' ||
                type == 'report.progress' ||
                type == 'report.ready' ||
                type == 'todo.updated' ||
                type == 'weekly_plan.updated') {
              _refresh();
            }
          } catch (_) {
            // Ignore malformed events; the socket itself is still healthy.
          }
        },
        onDone: () => _handleSocketDisconnect(socket),
        onError: (_) => _handleSocketDisconnect(socket),
      );
    } catch (_) {
      if (mounted && _socket == null && _socketConnected) {
        setState(() => _socketConnected = false);
      }
      _scheduleReconnect();
    }
  }

  void _handleSocketDisconnect([WebSocket? source]) {
    if (source != null && _socket != source) return;
    if (!mounted || _fallingBackToPairing || _disconnectHandled) return;
    _disconnectHandled = true;
    _socketSubscription?.cancel();
    _socketSubscription = null;
    _socket?.close();
    _socket = null;
    if (mounted) setState(() => _socketConnected = false);
    _scheduleReconnect();
  }

  void _scheduleReconnect() {
    if (!mounted || _fallingBackToPairing || _rediscovering) return;
    _connectionFailures++;
    if (_connectionFailures >= 2) {
      _reconnectTimer?.cancel();
      unawaited(_recoverConnection());
      return;
    }
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(const Duration(seconds: 2), () {
      _disconnectHandled = false;
      _connectEvents();
    });
  }

  Future<bool> _recoverConnection({bool manual = false}) async {
    if (!mounted || _rediscovering || _fallingBackToPairing) return false;
    _rediscovering = true;
    if (manual) _discoveryRounds = 0;
    final rounds = manual ? 1 : _maxDiscoveryRounds;
    try {
      _showReconnectMessage('正在局域网搜索已配对镜端…');
      while (mounted && _discoveryRounds < rounds) {
        _discoveryRounds++;
        final recovered = await _recovery.recover(
          _api.credentials,
          discoveryTimeout: const Duration(seconds: 2),
        );
        if (!mounted) return false;
        if (recovered != null) {
          await _switchCore(recovered.credentials);
          if (mounted) {
            _showReconnectMessage('已自动连接 ${recovered.deviceName}');
          }
          return true;
        }
        if (_discoveryRounds < rounds) {
          await Future<void>.delayed(const Duration(seconds: 2));
        }
      }
      if (!manual && mounted) {
        _fallingBackToPairing = true;
        unawaited(widget.onCoreUnavailable());
      }
      return false;
    } finally {
      _rediscovering = false;
    }
  }

  Future<void> _switchCore(CoreCredentials credentials) async {
    _reconnectTimer?.cancel();
    final nextApi = CoreApiClient(credentials);
    final previousApi = _api;
    _api = nextApi;
    _connectionFailures = 0;
    _disconnectHandled = false;
    _discoveryRounds = 0;
    _fallingBackToPairing = false;
    _socketConnected = false;
    if (mounted) {
      setState(() => _refreshToken++);
    } else {
      nextApi.close();
      return;
    }
    previousApi.close();
    await widget.onCredentialsUpdated(credentials);
    _syncFocus();
    unawaited(_connectEvents());
  }

  void _showReconnectMessage(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(content: Text(message), duration: const Duration(seconds: 2)),
      );
  }

  void _refresh() => setState(() => _refreshToken++);

  void _syncFocus() {
    unawaited(_api.updateMirrorFocus(_focusPages[_index]).catchError((_) {}));
  }

  void _queueMirrorThemeSync() {
    final api = _api;
    final mode = widget.isDarkMode ? 'dark' : 'light';
    _themeSync = _themeSync.then((_) async {
      try {
        await api.updateMirrorTheme(mode);
      } catch (_) {
        // Reconnect and websocket attachment retry with the current mode.
      }
    });
  }

  Future<void> _startPlannedTraining({
    required String exercise,
    required String view,
    required int targetReps,
  }) async {
    setState(() => _index = 1);
    _syncFocus();
    try {
      final started = await _api.startNewSession(
        exercise: exercise,
        view: view,
        targetReps: targetReps,
      );
      if (!mounted) return;
      await Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => LiveSessionPage(api: _api, initial: started),
        ),
      );
      _refresh();
    } on SocketException {
      _showStartError('无法连接 Core API');
    } on CoreApiException catch (error) {
      _showStartError(error.message);
    } catch (error) {
      _showStartError('$error');
    }
  }

  void _showStartError(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message), backgroundColor: context.vigor.signal),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: VigorPage(
        child: IndexedStack(
          index: _index,
          children: [
            DashboardPage(
              api: _api,
              refreshToken: _refreshToken,
              connected: _socketConnected,
              onStartTraining: _startPlannedTraining,
            ),
            HomePage(
              api: _api,
              refreshToken: _refreshToken,
              connected: _socketConnected,
              onChanged: _refresh,
            ),
            HistoryPage(api: _api, refreshToken: _refreshToken),
            ProfilePage(
              api: _api,
              refreshToken: _refreshToken,
              connected: _socketConnected,
              isDarkMode: widget.isDarkMode,
              onToggleTheme: widget.onToggleTheme,
              onLogout: widget.onLogout,
              onReconnect: () => _recoverConnection(manual: true),
            ),
          ],
        ),
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (value) {
          setState(() => _index = value);
          _syncFocus();
        },
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.dashboard_outlined),
            selectedIcon: Icon(Icons.dashboard),
            label: '首页',
          ),
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
          NavigationDestination(
            icon: Icon(Icons.person_outline),
            selectedIcon: Icon(Icons.person),
            label: '我的',
          ),
        ],
      ),
    );
  }
}
