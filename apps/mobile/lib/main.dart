import 'package:material_ui/material_ui.dart';

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
      final credentials = await _tokenStore.read();
      if (mounted) {
        setState(() {
          _credentials = credentials;
          _loading = false;
        });
      }
    } catch (error) {
      if (mounted) {
        setState(() {
          _error = '无法读取安全存储：$error';
          _loading = false;
        });
      }
    }
  }

  Future<void> _paired(CoreCredentials credentials) async {
    await _tokenStore.save(credentials);
    if (mounted) {
      setState(() => _credentials = credentials);
    }
  }

  Future<void> _logout() async {
    await _tokenStore.clear();
    if (mounted) {
      setState(() => _credentials = null);
    }
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
            )
          : AppShell(
              credentials: _credentials!,
              isDarkMode: _darkMode,
              onToggleTheme: _toggleTheme,
              onLogout: _logout,
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
