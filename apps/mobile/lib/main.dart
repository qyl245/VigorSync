import 'package:material_ui/material_ui.dart';

import 'data/core_api_client.dart';
import 'data/core_rediscovery.dart';
import 'data/theme_store.dart';
import 'data/token_store.dart';
import 'features/home/app_shell.dart';
import 'features/pairing/pairing_page.dart';
import 'models/session.dart';
import 'theme/vigorsync_theme.dart';

void main() => runApp(const VigorSyncApp());

class VigorSyncApp extends StatefulWidget {
  const VigorSyncApp({super.key});

  @override
  State<VigorSyncApp> createState() => _VigorSyncAppState();
}

class _VigorSyncAppState extends State<VigorSyncApp> {
  final TokenStore _tokenStore = TokenStore();
  final ThemeStore _themeStore = ThemeStore();
  CoreCredentials? _credentials;
  bool _loading = true;
  bool _darkMode = true;
  bool _returningToPairing = false;
  String? _pairingNotice;
  String? _error;

  @override
  void initState() {
    super.initState();
    _restoreSession();
    _restoreTheme();
  }

  Future<void> _restoreTheme() async {
    try {
      final darkMode = await _themeStore.readDarkMode();
      if (mounted) setState(() => _darkMode = darkMode);
    } catch (_) {
      // Keep the default dark theme when local settings cannot be read.
    }
  }

  Future<void> _restoreSession() async {
    try {
      var credentials = await _tokenStore.read();
      String? pairingNotice;
      if (credentials != null) {
        final recovered = await const CoreRediscoveryService().recover(
          credentials,
        );
        if (recovered != null) {
          credentials = recovered.credentials;
          await _tokenStore.save(credentials);
        } else {
          credentials = null;
          pairingNotice = '未在局域网找到已配对镜端，请确认镜端服务已启动。';
        }
      }
      if (mounted) {
        setState(() {
          _credentials = credentials;
          _pairingNotice = pairingNotice;
          _error = null;
          _loading = false;
        });
      }
    } catch (error) {
      if (mounted) {
        setState(() {
          _error = '无法恢复 Core 会话：$error';
          _loading = false;
        });
      }
    }
  }

  Future<void> _paired(CoreCredentials credentials) async {
    await _tokenStore.save(credentials);
    if (mounted) {
      setState(() {
        _credentials = credentials;
        _pairingNotice = null;
        _error = null;
      });
    }
  }

  Future<void> _logout() async {
    final credentials = _credentials;
    if (credentials != null) {
      final api = CoreApiClient(credentials);
      try {
        await api.revokeCurrentPairing().timeout(const Duration(seconds: 3));
      } catch (_) {
        // Local unpair must still work when the Core is unavailable.
      } finally {
        api.close();
      }
    }
    await _tokenStore.clear();
    if (mounted) {
      setState(() {
        _credentials = null;
        _pairingNotice = null;
      });
    }
  }

  Future<void> _credentialsUpdated(CoreCredentials credentials) async {
    await _tokenStore.save(credentials);
    if (mounted) {
      setState(() => _credentials = credentials);
    }
  }

  Future<void> _handleCoreUnavailable() async {
    if (_credentials == null || _returningToPairing) return;
    _returningToPairing = true;
    if (mounted) {
      setState(() {
        _credentials = null;
        _pairingNotice = '与镜端的连接已断开，且未在局域网中发现已配对设备。';
        _error = null;
      });
    }
    _returningToPairing = false;
  }

  Future<void> _toggleTheme() async {
    final darkMode = !_darkMode;
    setState(() => _darkMode = darkMode);
    try {
      await _themeStore.saveDarkMode(darkMode);
    } catch (_) {
      // The current selection still applies for this session.
    }
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      key: ValueKey(_credentials == null ? 'pairing' : 'in-app'),
      title: 'VigorSync',
      debugShowCheckedModeBanner: false,
      theme: buildVigorTheme(Brightness.light),
      darkTheme: buildVigorTheme(),
      themeMode: _darkMode ? ThemeMode.dark : ThemeMode.light,
      home: _loading
          ? const _BootScreen()
          : _error != null
          ? _ErrorScreen(message: _error!)
          : _credentials == null
          ? PairingPage(
              onPaired: _paired,
              isDarkMode: _darkMode,
              onToggleTheme: _toggleTheme,
              initialMessage: _pairingNotice,
            )
          : AppShell(
              credentials: _credentials!,
              isDarkMode: _darkMode,
              onToggleTheme: _toggleTheme,
              onLogout: _logout,
              onCredentialsUpdated: _credentialsUpdated,
              onCoreUnavailable: _handleCoreUnavailable,
            ),
    );
  }
}

class _BootScreen extends StatelessWidget {
  const _BootScreen();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: VigorPage(
        child: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const BrandMark(),
              const SizedBox(height: 22),
              CircularProgressIndicator(color: context.vigor.accent),
            ],
          ),
        ),
      ),
    );
  }
}

class _ErrorScreen extends StatelessWidget {
  const _ErrorScreen({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: VigorPage(
        child: Center(
          child: Padding(
            padding: const EdgeInsets.all(28),
            child: Text(message, textAlign: TextAlign.center),
          ),
        ),
      ),
    );
  }
}
