import 'dart:async';
import 'dart:io';

import 'package:material_ui/material_ui.dart';

import '../../data/core_api_client.dart';
import '../../models/session.dart';
import '../../theme/vigorsync_theme.dart';
import '../../utils/session_format.dart';
import '../../widgets/exercise_glyph.dart';
import '../session/live_session_page.dart';

class HomePage extends StatefulWidget {
  const HomePage({
    super.key,
    required this.api,
    required this.refreshToken,
    required this.isDarkMode,
    required this.onToggleTheme,
    required this.onChanged,
    required this.onLogout,
  });

  final CoreApiClient api;
  final int refreshToken;
  final bool isDarkMode;
  final VoidCallback onToggleTheme;
  final VoidCallback onChanged;
  final Future<void> Function() onLogout;

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  String _exercise = 'squat';
  String _view = 'front';
  int _targetReps = 12;
  bool _starting = false;
  String _liveStatus = '检查连接';
  List<SessionRecord> _sessions = const [];
  Timer? _configSyncTimer;

  @override
  void initState() {
    super.initState();
    _refresh();
    WidgetsBinding.instance.addPostFrameCallback((_) => _syncConfig());
  }

  @override
  void dispose() {
    _configSyncTimer?.cancel();
    super.dispose();
  }

  @override
  void didUpdateWidget(covariant HomePage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.refreshToken != widget.refreshToken) {
      _refresh();
    }
  }

  Future<void> _refresh() async {
    try {
      final values = await Future.wait([
        widget.api.health(),
        widget.api.listSessions(),
      ]);
      if (mounted) {
        setState(() {
          _liveStatus = values[0] as String;
          _sessions = values[1] as List<SessionRecord>;
        });
      }
    } catch (_) {
      if (mounted) {
        setState(() => _liveStatus = '离线');
      }
    }
  }

  Future<void> _startTraining() async {
    setState(() => _starting = true);
    try {
      _configSyncTimer?.cancel();
      final started = await widget.api.startNewSession(
        exercise: _exercise,
        view: _view,
        targetReps: _targetReps,
      );
      if (!mounted) return;
      await Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => LiveSessionPage(api: widget.api, initial: started),
        ),
      );
      widget.onChanged();
      _refresh();
    } on SocketException {
      _showError('无法连接 Core API');
    } on CoreApiException catch (error) {
      _showError(error.message);
    } catch (error) {
      _showError('$error');
    } finally {
      if (mounted) setState(() => _starting = false);
    }
  }

  void _scheduleConfigSync() {
    _configSyncTimer?.cancel();
    _configSyncTimer = Timer(const Duration(milliseconds: 120), _syncConfig);
  }

  Future<void> _syncConfig() async {
    try {
      await widget.api.updateSessionConfig(
        exercise: _exercise,
        view: _view,
        targetReps: _targetReps,
      );
    } catch (_) {
      // The next selection, page visit, or training start retries the draft.
    }
  }

  void _showError(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message), backgroundColor: context.vigor.signal),
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final palette = context.vigor;
    final activeSessions = _sessions.where((session) => session.isActive);
    final active = activeSessions.isEmpty ? null : activeSessions.first;
    return SafeArea(
      child: RefreshIndicator(
        onRefresh: _refresh,
        color: palette.accent,
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.fromLTRB(22, 22, 22, 30),
          children: [
            Row(
              children: [
                const BrandMark(compact: true),
                const SizedBox(width: 12),
                const Expanded(
                  child: Text(
                    'VIGORSYNC',
                    style: TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w800,
                      letterSpacing: 2.1,
                    ),
                  ),
                ),
                _StatusPill(status: _liveStatus),
                ThemeModeButton(
                  isDarkMode: widget.isDarkMode,
                  onPressed: widget.onToggleTheme,
                ),
                PopupMenuButton<String>(
                  color: palette.elevated,
                  onSelected: (value) {
                    if (value == 'logout') widget.onLogout();
                  },
                  itemBuilder: (_) => const [
                    PopupMenuItem(value: 'logout', child: Text('解除配对')),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 46),
            Text('今天，练得\n更明白一点。', style: theme.textTheme.displayLarge),
            const SizedBox(height: 18),
            Text(
              '手机负责控制与复盘，镜端负责实时捕捉。',
              style: TextStyle(color: palette.muted, height: 1.6),
            ),
            const SizedBox(height: 30),
            Container(
              padding: const EdgeInsets.all(20),
              decoration: BoxDecoration(
                color: palette.surface.withValues(alpha: 0.92),
                border: Border.all(color: palette.line),
                borderRadius: BorderRadius.circular(14),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Row(
                    children: [
                      Text(
                        'NEW SESSION',
                        style: theme.textTheme.labelSmall?.copyWith(
                          color: palette.accent,
                          letterSpacing: 1.3,
                        ),
                      ),
                      const Spacer(),
                      Text(
                        '本机实时',
                        style: TextStyle(color: palette.muted, fontSize: 11),
                      ),
                    ],
                  ),
                  const SizedBox(height: 20),
                  SegmentedButton<String>(
                    segments: const [
                      ButtonSegment(value: 'squat', label: Text('深蹲')),
                      ButtonSegment(value: 'pushup', label: Text('俯卧撑')),
                    ],
                    selected: {_exercise},
                    onSelectionChanged: (value) {
                      setState(() => _exercise = value.first);
                      _scheduleConfigSync();
                    },
                    style: ButtonStyle(
                      visualDensity: VisualDensity.compact,
                      side: WidgetStatePropertyAll(
                        BorderSide(color: palette.line),
                      ),
                    ),
                  ),
                  const SizedBox(height: 14),
                  DropdownButtonFormField<String>(
                    initialValue: _view,
                    decoration: const InputDecoration(labelText: '镜端机位'),
                    dropdownColor: palette.elevated,
                    items: const [
                      DropdownMenuItem(value: 'front', child: Text('正面')),
                      DropdownMenuItem(value: 'rear', child: Text('背面')),
                      DropdownMenuItem(value: 'side_left', child: Text('左侧')),
                      DropdownMenuItem(value: 'side_right', child: Text('右侧')),
                      DropdownMenuItem(
                        value: 'oblique_left',
                        child: Text('左斜侧'),
                      ),
                      DropdownMenuItem(
                        value: 'oblique_right',
                        child: Text('右斜侧'),
                      ),
                    ],
                    onChanged: (value) {
                      if (value != null) {
                        setState(() => _view = value);
                        _scheduleConfigSync();
                      }
                    },
                  ),
                  const SizedBox(height: 18),
                  _TargetSelector(
                    value: _targetReps,
                    onChanged: (value) {
                      setState(() => _targetReps = value);
                      _scheduleConfigSync();
                    },
                  ),
                  const SizedBox(height: 22),
                  FilledButton(
                    onPressed: _starting ? null : _startTraining,
                    child: _starting
                        ? SizedBox(
                            width: 22,
                            height: 22,
                            child: CircularProgressIndicator(
                              strokeWidth: 2,
                              color: palette.buttonInk,
                            ),
                          )
                        : const Text('启动镜端训练  →'),
                  ),
                ],
              ),
            ),
            if (active != null) ...[
              const SizedBox(height: 14),
              OutlinedButton.icon(
                onPressed: () => Navigator.of(context).push(
                  MaterialPageRoute<void>(
                    builder: (_) =>
                        LiveSessionPage(api: widget.api, initial: active),
                  ),
                ),
                icon: const Icon(Icons.sensors),
                label: const Text('继续查看当前训练'),
              ),
            ],
            const SizedBox(height: 30),
            Row(
              children: [
                Text('最近训练', style: theme.textTheme.titleLarge),
                const Spacer(),
                Text(
                  '${_sessions.length} 条',
                  style: TextStyle(
                    color: palette.muted,
                    fontSize: 12,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            if (_sessions.isEmpty)
              const _EmptyHint('完成第一组训练后，记录会出现在这里。')
            else
              ..._sessions.take(3).map(_SessionTile.new),
          ],
        ),
      ),
    );
  }
}

class _TargetSelector extends StatelessWidget {
  const _TargetSelector({required this.value, required this.onChanged});

  final int value;
  final ValueChanged<int> onChanged;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    return Row(
      children: [
        Text(
          '目标次数',
          style: TextStyle(color: palette.muted, fontSize: 13),
        ),
        const Spacer(),
        IconButton(
          onPressed: value > 1 ? () => onChanged(value - 1) : null,
          icon: const Icon(Icons.remove_circle_outline),
        ),
        Text(
          '$value',
          style: const TextStyle(
            fontSize: 30,
            fontWeight: FontWeight.w800,
            fontFeatures: [FontFeature.tabularFigures()],
          ),
        ),
        IconButton(
          onPressed: value < 500 ? () => onChanged(value + 1) : null,
          icon: const Icon(Icons.add_circle_outline),
        ),
      ],
    );
  }
}

class _StatusPill extends StatelessWidget {
  const _StatusPill({required this.status});

  final String status;

  @override
  Widget build(BuildContext context) {
    final online = status == 'ok';
    final palette = context.vigor;
    return Container(
      margin: const EdgeInsets.only(left: 8),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
      decoration: BoxDecoration(
        border: Border.all(
          color: online ? palette.accent : palette.signal,
        ),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        online ? '已连接' : status,
        style: TextStyle(
          color: online ? palette.accent : palette.signal,
          fontSize: 10,
          fontWeight: FontWeight.w700,
        ),
      ),
    );
  }
}

class _SessionTile extends StatelessWidget {
  const _SessionTile(this.session);

  final SessionRecord session;

  @override
  Widget build(BuildContext context) {
    final reps = (session.liveSummary?['reps'] as num?)?.toInt();
    final palette = context.vigor;
    return Container(
      margin: const EdgeInsets.only(bottom: 9),
      padding: const EdgeInsets.symmetric(horizontal: 15, vertical: 13),
      decoration: BoxDecoration(
        border: Border.all(color: palette.line),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        children: [
          ExerciseGlyph(
            exercise: session.exercise,
            color: palette.cyan,
            size: 30,
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  session.exercise == 'squat' ? '深蹲' : '俯卧撑',
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
                const SizedBox(height: 3),
                Text(
                  '${formatSessionTime(session.startedAt ?? session.createdAt)}'
                  ' · ${sessionStatusLabel(session.status)}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    color: palette.muted,
                    fontSize: 10,
                  ),
                ),
              ],
            ),
          ),
          Text(
            reps == null ? '--' : '$reps 次',
            style: TextStyle(
              color: palette.accent,
              fontWeight: FontWeight.w700,
            ),
          ),
        ],
      ),
    );
  }
}

class _EmptyHint extends StatelessWidget {
  const _EmptyHint(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    return Container(
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        border: Border.all(color: palette.line),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Text(
        text,
        style: TextStyle(color: palette.muted, height: 1.5),
      ),
    );
  }
}
