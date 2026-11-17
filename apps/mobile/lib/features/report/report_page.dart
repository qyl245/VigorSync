import 'package:flutter/services.dart';
import 'package:material_ui/material_ui.dart';

import '../../data/core_api_client.dart';
import '../../models/session.dart';
import '../../theme/vigorsync_theme.dart';
import '../../utils/session_format.dart';
import '../../widgets/exercise_glyph.dart';

const _nativeChannel = MethodChannel('com.vigorsync.mobile/native');

class SessionReportPage extends StatefulWidget {
  const SessionReportPage({
    super.key,
    required this.api,
    required this.session,
  });

  final CoreApiClient api;
  final SessionRecord session;

  @override
  State<SessionReportPage> createState() => _SessionReportPageState();
}

class _SessionReportPageState extends State<SessionReportPage> {
  Map<String, dynamic>? _report;
  String? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final report = await widget.api.getReport(widget.session.sessionId);
      if (mounted) setState(() => _report = report);
    } on CoreApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _openDetailedReport() async {
    final value = _report?['report_url'] as String?;
    if (value == null) return;
    final uri = Uri.parse(widget.api.resolveUrl(value));
    try {
      await _nativeChannel.invokeMethod<bool>('openUrl', uri.toString());
    } on PlatformException {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('无法打开完整复盘报告')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final palette = context.vigor;
    final summary =
        (_report?['summary'] as Map<String, dynamic>?) ??
        widget.session.analysisSummary ??
        const <String, dynamic>{};
    final issues = (summary['issues'] as List<dynamic>? ?? const [])
        .whereType<Map<String, dynamic>>()
        .toList();
    return Scaffold(
      body: VigorPage(
        child: SafeArea(
          child: RefreshIndicator(
            color: palette.accent,
            onRefresh: _load,
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.fromLTRB(20, 14, 20, 28),
              children: [
                Row(
                  children: [
                    IconButton(
                      onPressed: () => Navigator.of(context).pop(),
                      icon: const Icon(Icons.arrow_back),
                    ),
                    const SizedBox(width: 4),
                    ExerciseGlyph(
                      exercise: widget.session.exercise,
                      color: palette.cyan,
                      size: 30,
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            'OFFLINE REVIEW',
                            style: theme.textTheme.labelSmall?.copyWith(
                              color: palette.accent,
                              letterSpacing: 1.4,
                            ),
                          ),
                          Text(
                            widget.session.exercise == 'squat'
                                ? '深蹲复盘'
                                : '俯卧撑复盘',
                            style: theme.textTheme.titleLarge,
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 24),
                if (_loading)
                  Padding(
                    padding: const EdgeInsets.all(36),
                    child: Center(
                      child: CircularProgressIndicator(color: palette.accent),
                    ),
                  )
                else if (_error != null)
                  _ReportMessage(text: _error!, isError: true)
                else ...[
                  _QualityBanner(
                    quality: summary['quality_status'] as String? ?? 'unknown',
                  ),
                  const SizedBox(height: 14),
                  Row(
                    children: [
                      Expanded(
                        child: _ReportMetric(
                          label: '离线计数',
                          value: '${summary['rep_count'] ?? 0}',
                          accent: palette.accent,
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: _ReportMetric(
                          label: '实时计数',
                          value: '${widget.session.liveSummary?['reps'] ?? '--'}',
                          accent: palette.cyan,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  Row(
                    children: [
                      Expanded(
                        child: _ReportMetric(
                          label: '问题',
                          value: '${summary['active_issue_count'] ?? 0}',
                          accent: palette.signal,
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: _ReportMetric(
                          label: '平均置信度',
                          value: _confidence(
                            summary['mean_confidence'] as num?,
                          ),
                          accent: palette.accent,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 24),
                  Text('主要发现', style: theme.textTheme.titleLarge),
                  const SizedBox(height: 12),
                  if (issues.isEmpty)
                    const _ReportMessage(
                      text: '本次没有达到展示门槛的可靠问题。',
                    )
                  else
                    ...issues.map((issue) => _IssueCard(issue)),
                  const SizedBox(height: 18),
                  FilledButton.icon(
                    onPressed: _openDetailedReport,
                    icon: const Icon(Icons.open_in_new),
                    label: const Text('打开完整证据报告'),
                  ),
                  const SizedBox(height: 14),
                  Text(
                    '${formatSessionTime(widget.session.startedAt ?? widget.session.createdAt)}'
                    ' · ${sessionViewLabel(widget.session.view)}',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: palette.muted, fontSize: 11),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _QualityBanner extends StatelessWidget {
  const _QualityBanner({required this.quality});

  final String quality;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    final rejected = quality == 'reject';
    final color = rejected ? palette.signal : palette.accent;
    final text = switch (quality) {
      'pass' => '质量通过，可以查看动作指标与证据。',
      'limited' => '质量有限，部分指标只作为诊断参考。',
      'reject' => '拍摄质量未通过，系统不会给出动作纠正结论。',
      _ => '质量状态未知。',
    };
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.1),
        border: Border(left: BorderSide(color: color, width: 3)),
      ),
      child: Text(text, style: TextStyle(color: color, height: 1.5)),
    );
  }
}

class _ReportMetric extends StatelessWidget {
  const _ReportMetric({
    required this.label,
    required this.value,
    required this.accent,
  });

  final String label;
  final String value;
  final Color accent;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: palette.surface.withValues(alpha: 0.92),
        border: Border.all(color: palette.line),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: TextStyle(color: palette.muted, fontSize: 11)),
          const SizedBox(height: 8),
          Text(
            value,
            style: TextStyle(
              color: accent,
              fontSize: 30,
              fontWeight: FontWeight.w800,
              fontFeatures: const [FontFeature.tabularFigures()],
            ),
          ),
        ],
      ),
    );
  }
}

class _IssueCard extends StatelessWidget {
  const _IssueCard(this.issue);

  final Map<String, dynamic> issue;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    final severity = issue['severity'] as String? ?? 'low';
    final accent = severity == 'high' ? palette.signal : palette.cyan;
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: palette.surface.withValues(alpha: 0.92),
        border: Border.all(color: palette.line),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  issue['title'] as String? ?? '动作提示',
                  style: const TextStyle(fontWeight: FontWeight.w800),
                ),
              ),
              Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: 8,
                  vertical: 4,
                ),
                decoration: BoxDecoration(
                  color: accent.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(999),
                ),
                child: Text(
                  severity,
                  style: TextStyle(color: accent, fontSize: 9),
                ),
              ),
            ],
          ),
          if ((issue['detail'] as String?)?.isNotEmpty ?? false) ...[
            const SizedBox(height: 8),
            Text(
              issue['detail'] as String,
              style: TextStyle(color: palette.muted, height: 1.5),
            ),
          ],
          if ((issue['cue'] as String?)?.isNotEmpty ?? false) ...[
            const SizedBox(height: 10),
            Text(
              issue['cue'] as String,
              style: TextStyle(color: palette.accent, height: 1.4),
            ),
          ],
        ],
      ),
    );
  }
}

class _ReportMessage extends StatelessWidget {
  const _ReportMessage({required this.text, this.isError = false});

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
        style: TextStyle(color: isError ? palette.signal : palette.muted),
      ),
    );
  }
}

String _confidence(num? value) {
  if (value == null) return '--';
  return '${(value.toDouble() * 100).round()}%';
}
