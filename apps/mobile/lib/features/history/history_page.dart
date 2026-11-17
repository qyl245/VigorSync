import 'package:material_ui/material_ui.dart';

import '../../data/core_api_client.dart';
import '../../models/session.dart';
import '../../theme/vigorsync_theme.dart';
import '../../utils/session_format.dart';
import '../../widgets/exercise_glyph.dart';

class HistoryPage extends StatefulWidget {
  const HistoryPage({
    super.key,
    required this.api,
    required this.refreshToken,
    required this.isDarkMode,
    required this.onToggleTheme,
  });

  final CoreApiClient api;
  final int refreshToken;
  final bool isDarkMode;
  final VoidCallback onToggleTheme;

  @override
  State<HistoryPage> createState() => _HistoryPageState();
}

class _HistoryPageState extends State<HistoryPage> {
  Future<List<SessionRecord>>? _future;
  String? _deletingSessionId;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void didUpdateWidget(covariant HistoryPage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.refreshToken != widget.refreshToken) {
      _load();
    }
  }

  void _load() {
    _future = widget.api.listSessions();
  }

  Future<void> _deleteSession(SessionRecord session) async {
    if (session.isActive || session.status == 'stopping') return;
    final palette = context.vigor;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        backgroundColor: palette.elevated,
        title: const Text('永久删除这次训练？'),
        content: Text(
          '将删除本机视频、关键点、事件和摘要，并移除 Core 记录。'
          '此操作无法撤销。',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, false),
            child: const Text('取消'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: palette.signal,
              foregroundColor: palette.surface,
            ),
            onPressed: () => Navigator.pop(dialogContext, true),
            child: const Text('删除'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    setState(() => _deletingSessionId = session.sessionId);
    try {
      await widget.api.deleteSession(session.sessionId);
      if (!mounted) return;
      setState(() {
        _deletingSessionId = null;
        _load();
      });
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('训练记录及本机产物已删除')),
      );
    } on CoreApiException catch (error) {
      if (!mounted) return;
      setState(() => _deletingSessionId = null);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(error.message),
          backgroundColor: palette.signal,
        ),
      );
    } catch (error) {
      if (!mounted) return;
      setState(() => _deletingSessionId = null);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('$error'),
          backgroundColor: palette.signal,
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final palette = context.vigor;
    return SafeArea(
      child: RefreshIndicator(
        color: palette.accent,
        onRefresh: () async {
          setState(_load);
          await _future;
        },
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.fromLTRB(22, 22, 22, 30),
          children: [
            Row(
              children: [
                const BrandMark(compact: true),
                const SizedBox(width: 12),
                const Text(
                  'TRAINING ARCHIVE',
                  style: TextStyle(
                    fontWeight: FontWeight.w800,
                    letterSpacing: 1.8,
                    fontSize: 11,
                  ),
                ),
                const Spacer(),
                ThemeModeButton(
                  isDarkMode: widget.isDarkMode,
                  onPressed: widget.onToggleTheme,
                ),
              ],
            ),
            const SizedBox(height: 40),
            Text('训练轨迹', style: theme.textTheme.displayLarge),
            const SizedBox(height: 12),
            Text(
              '每一次训练都在本机留下视频、姿态和证据。',
              style: TextStyle(color: palette.muted, height: 1.5),
            ),
            const SizedBox(height: 26),
            FutureBuilder<List<SessionRecord>>(
              future: _future,
              builder: (context, snapshot) {
                if (snapshot.connectionState != ConnectionState.done) {
                  return Padding(
                    padding: const EdgeInsets.all(40),
                    child: Center(
                      child: CircularProgressIndicator(color: palette.accent),
                    ),
                  );
                }
                if (snapshot.hasError) {
                  return _HistoryMessage(
                    text: '无法加载历史：${snapshot.error}',
                    isError: true,
                  );
                }
                final sessions = snapshot.data ?? const [];
                if (sessions.isEmpty) {
                  return const _HistoryMessage(text: '还没有训练记录。');
                }
                return Column(
                  children: sessions
                      .map(
                        (session) => _HistoryCard(
                          session: session,
                          deleting: _deletingSessionId == session.sessionId,
                          onDelete: () => _deleteSession(session),
                        ),
                      )
                      .toList(),
                );
              },
            ),
          ],
        ),
      ),
    );
  }
}

class _HistoryCard extends StatelessWidget {
  const _HistoryCard({
    required this.session,
    required this.deleting,
    required this.onDelete,
  });

  final SessionRecord session;
  final bool deleting;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    final summary = session.liveSummary;
    final reps = (summary?['reps'] as num?)?.toInt();
    final confidence = (summary?['confidence'] as num?)?.toDouble();
    final duration = formatSessionDuration(summary?['duration_s']);
    final startedAt = formatSessionTime(
      session.startedAt ?? session.createdAt,
    );
    final palette = context.vigor;
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(17),
      decoration: BoxDecoration(
        color: palette.surface.withValues(alpha: 0.92),
        border: Border.all(color: palette.line),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              ExerciseGlyph(
                exercise: session.exercise,
                color: palette.cyan,
                size: 30,
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  session.exercise == 'squat' ? '深蹲' : '俯卧撑',
                  style: const TextStyle(
                    fontSize: 17,
                    fontWeight: FontWeight.w800,
                  ),
                ),
              ),
              _HistoryStatus(status: session.status),
              const SizedBox(width: 2),
              if (deleting)
                SizedBox(
                  width: 36,
                  height: 36,
                  child: Padding(
                    padding: const EdgeInsets.all(9),
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      color: palette.accent,
                    ),
                  ),
                )
              else
                IconButton(
                  onPressed:
                      session.isActive || session.status == 'stopping'
                      ? null
                      : onDelete,
                  tooltip: '删除训练记录',
                  visualDensity: VisualDensity.compact,
                  icon: Icon(
                    Icons.delete_outline,
                    size: 20,
                    color: session.isActive || session.status == 'stopping'
                        ? palette.muted
                        : palette.signal,
                  ),
                ),
            ],
          ),
          const SizedBox(height: 18),
          Row(
            children: [
              _HistoryValue(
                label: '完成次数',
                value: reps == null ? '--' : '$reps',
              ),
              _HistoryValue(
                label: '置信度',
                value: confidence == null
                    ? '--'
                    : '${(confidence * 100).round()}%',
              ),
              _HistoryValue(label: '目标', value: '${session.targetReps}'),
            ],
          ),
          const SizedBox(height: 13),
          Text(
            [
              startedAt,
              sessionViewLabel(session.view),
              if (duration.isNotEmpty) duration,
            ].join('  ·  '),
            style: TextStyle(color: palette.muted, fontSize: 10),
          ),
        ],
      ),
    );
  }
}

class _HistoryValue extends StatelessWidget {
  const _HistoryValue({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    return Expanded(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            value,
            style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w800),
          ),
          Text(
            label,
            style: TextStyle(color: palette.muted, fontSize: 10),
          ),
        ],
      ),
    );
  }
}

class _HistoryStatus extends StatelessWidget {
  const _HistoryStatus({required this.status});

  final String status;

  @override
  Widget build(BuildContext context) {
    final active = status == 'analyzing' || status == 'active';
    final palette = context.vigor;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 5),
      decoration: BoxDecoration(
        color: (active ? palette.accent : palette.muted).withValues(
          alpha: 0.1,
        ),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        sessionStatusLabel(status),
        style: TextStyle(
          color: active ? palette.accent : palette.muted,
          fontSize: 9,
          fontWeight: FontWeight.w700,
        ),
      ),
    );
  }
}

class _HistoryMessage extends StatelessWidget {
  const _HistoryMessage({required this.text, this.isError = false});

  final String text;
  final bool isError;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    return Container(
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        border: Border.all(
          color: isError ? palette.signal : palette.line,
        ),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Text(
        text,
        style: TextStyle(
          color: isError ? palette.signal : palette.muted,
        ),
      ),
    );
  }
}
