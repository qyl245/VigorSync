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
  int _index = 0;
  int _refreshToken = 0;

  @override
  void initState() {
    super.initState();
    _api = CoreApiClient(widget.credentials);
  }

  @override
  void dispose() {
    _api.close();
    super.dispose();
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
