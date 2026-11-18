import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:material_ui/material_ui.dart';

import '../../data/core_api_client.dart';
import '../../models/session.dart';
import '../../theme/vigorsync_theme.dart';

class LiveSessionPage extends StatefulWidget {
  const LiveSessionPage({super.key, required this.api, required this.initial});

  final CoreApiClient api;
  final SessionRecord initial;

  @override
  State<LiveSessionPage> createState() => _LiveSessionPageState();
}

class _LiveSessionPageState extends State<LiveSessionPage> {
  late SessionRecord _session;
  LiveMetrics _metrics = const LiveMetrics();
  WebSocket? _socket;
  StreamSubscription<dynamic>? _subscription;
  Timer? _reconnectTimer;
  bool _busy = false;
  bool _socketOnline = false;
  String? _latestAlert;
  bool _closing = false;

  String get _exerciseName => _session.exercise == 'squat' ? '深蹲' : '俯卧撑';
  String get _jointName => _session.exercise == 'squat' ? '膝关节角' : '肘关节角';
  bool get _isPreparing =>
      _session.status == 'created' || _session.status == 'preparing';
  bool get _canTogglePause =>
      _session.status == 'active' || _session.status == 'paused';

  @override
  void initState() {
    super.initState();
    _session = widget.initial;
    _connect();
  }

  @override
  void dispose() {
    _closing = true;
    _reconnectTimer?.cancel();
    _subscription?.cancel();
    _socket?.close();
    super.dispose();
  }

  Future<void> _connect() async {
    if (_closing) return;
    try {
      final socket = await widget.api.connectClientSocket();
      if (!mounted || _closing) {
        await socket.close();
        return;
      }
      _socket = socket;
      _socketOnline = true;
      _subscription = socket.listen(
        (message) {
          final decoded = jsonDecode(message as String);
          if (decoded is Map<String, dynamic>) {
            _handleMessage(decoded);
          }
        },
        onDone: _socketClosed,
        onError: (_) => _socketClosed(),
      );
      if (mounted) setState(() {});
    } catch (_) {
      _socketClosed();
    }
  }

  void _socketClosed() {
    _socketOnline = false;
    if (!mounted || _closing) return;
    setState(() {});
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(const Duration(milliseconds: 1400), _connect);
  }

  void _handleMessage(Map<String, dynamic> message) {
    final type = message['type'];
    final payload = message['payload'];
    if (type == 'session.deleted' &&
        message['session_id'] == _session.sessionId) {
      _finishCancelled();
      return;
    }
    if (payload is! Map<String, dynamic>) return;
    if (type == 'live.metrics') {
      setState(() => _metrics = LiveMetrics.fromJson(payload));
    } else if (type == 'session.state') {
      final status = payload['status'] as String?;
      if (status != null) {
        setState(() => _session = _session.copyWith(status: status));
      }
    } else if (type == 'form.alert') {
      setState(() => _latestAlert = payload['message'] as String?);
    } else if (type == 'session.snapshot' &&
        payload['session_id'] == _session.sessionId) {
      final status = payload['status'] as String?;
      if (status != null) {
        setState(() => _session = _session.copyWith(status: status));
      }
    }
  }

  Future<void> _togglePause() async {
    if (_busy || !_canTogglePause) return;
    setState(() => _busy = true);
    try {
      final updated = _metrics.paused || _session.status == 'paused'
          ? await widget.api.resumeSession(_session.sessionId)
          : await widget.api.pauseSession(_session.sessionId);
      if (mounted) setState(() => _session = updated);
    } on CoreApiException catch (error) {
      _showError(error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _cancelStart() async {
    if (_busy || !_isPreparing) return;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        backgroundColor: context.vigor.elevated,
        title: const Text('取消本次启动？'),
        content: const Text('Live Engine 将停止，本次不会保存，历史与趋势也不会新增。'),
        actions: [
          TextButton(
            autofocus: true,
            onPressed: () => Navigator.pop(context, false),
            child: const Text('继续准备'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: context.vigor.signal,
              foregroundColor: context.vigor.surface,
            ),
            onPressed: () => Navigator.pop(context, true),
            child: const Text('取消启动'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    if (!_isPreparing) {
      _showError('训练已经开始，请使用结束并保存。');
      return;
    }
    setState(() => _busy = true);
    try {
      await widget.api.cancelSession(_session.sessionId);
      _finishCancelled();
    } on CoreApiException catch (error) {
      _showError(error.message);
    } finally {
      if (mounted && !_closing) setState(() => _busy = false);
    }
  }

  void _finishCancelled() {
    if (!mounted || _closing) return;
    _closing = true;
    Navigator.of(context).pop();
  }

  Widget _buildSessionActions() {
    if (_isPreparing) {
      return Row(
        children: [
          Expanded(
            child: OutlinedButton(onPressed: null, child: const Text('准备中…')),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: OutlinedButton(
              onPressed: _busy ? null : _cancelStart,
              child: Text(_busy ? '正在取消…' : '取消启动'),
            ),
          ),
        ],
      );
    }
    if (_canTogglePause) {
      return Row(
        children: [
          Expanded(
            child: OutlinedButton(
              onPressed: _busy ? null : _togglePause,
              child: Text(
                _metrics.paused || _session.status == 'paused' ? '继续训练' : '暂停',
              ),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: FilledButton(
              style: FilledButton.styleFrom(
                backgroundColor: context.vigor.signal,
                foregroundColor: context.vigor.surface,
              ),
              onPressed: _busy ? null : _stop,
              child: const Text('结束训练'),
            ),
          ),
        ],
      );
    }
    if (_session.status == 'stopping') {
      return SizedBox(
        width: double.infinity,
        child: OutlinedButton(onPressed: null, child: const Text('保存中…')),
      );
    }
    if (_session.status == 'analyzing') {
      return SizedBox(
        width: double.infinity,
        child: OutlinedButton(onPressed: null, child: const Text('分析中…')),
      );
    }
    return SizedBox(
      width: double.infinity,
      child: OutlinedButton(
        onPressed: () => Navigator.of(context).pop(),
        child: const Text('返回主页'),
      ),
    );
  }

  Future<void> _stop() async {
    if (_busy || !_canTogglePause) return;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        backgroundColor: context.vigor.elevated,
        title: const Text('结束并保存本次训练？'),
        content: const Text('将停止计数，保存视频、关键点和训练摘要，并开始离线分析。确认期间训练仍在继续。'),
        actions: [
          TextButton(
            autofocus: true,
            onPressed: () => Navigator.pop(context, false),
            child: const Text('继续训练'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: context.vigor.signal,
              foregroundColor: context.vigor.surface,
            ),
            onPressed: () => Navigator.pop(context, true),
            child: const Text('结束并保存'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    if (!_canTogglePause) return;
    setState(() => _busy = true);
    try {
      final updated = await widget.api.stopSession(_session.sessionId);
      if (!mounted) return;
      await showDialog<void>(
        context: context,
        barrierDismissible: false,
        builder: (_) => AlertDialog(
          backgroundColor: context.vigor.elevated,
          title: const Text('训练已结束'),
          content: Text(
            '完成 ${_metrics.reps} 次，训练数据已保存。\n'
            '状态：${_statusLabel(updated.status)}',
          ),
          actions: [
            FilledButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('返回主页'),
            ),
          ],
        ),
      );
      try {
        await widget.api.dismissMirrorSession(_session.sessionId);
      } on CoreApiException catch (error) {
        _showError(error.message);
      }
      if (mounted) Navigator.of(context).pop();
    } on CoreApiException catch (error) {
      _showError(error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
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
    final compact = MediaQuery.sizeOf(context).height < 720;
    final progress = (_metrics.reps / _session.targetReps).clamp(0.0, 1.0);
    final targetHit = _metrics.reps >= _session.targetReps;
    return Scaffold(
      body: VigorPage(
        child: SafeArea(
          child: Padding(
            padding: EdgeInsets.fromLTRB(
              20,
              compact ? 10 : 18,
              20,
              compact ? 10 : 18,
            ),
            child: Column(
              children: [
                Row(
                  children: [
                    IconButton(
                      onPressed: () => Navigator.of(context).pop(),
                      icon: const Icon(Icons.close),
                    ),
                    const SizedBox(width: 4),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            'LIVE SESSION',
                            style: theme.textTheme.labelSmall?.copyWith(
                              color: palette.accent,
                              letterSpacing: 1.4,
                            ),
                          ),
                          Text(
                            _exerciseName,
                            style: theme.textTheme.titleLarge,
                          ),
                        ],
                      ),
                    ),
                    _ConnectionPill(online: _socketOnline),
                  ],
                ),
                const Spacer(),
                Text(
                  _formatDuration(_metrics.elapsedSeconds),
                  style: TextStyle(
                    color: palette.muted,
                    fontSize: 18,
                    fontWeight: FontWeight.w600,
                    letterSpacing: 2,
                    fontFeatures: [FontFeature.tabularFigures()],
                  ),
                ),
                SizedBox(height: compact ? 10 : 22),
                SizedBox(
                  width: compact ? 210 : 260,
                  height: compact ? 210 : 260,
                  child: Stack(
                    alignment: Alignment.center,
                    children: [
                      SizedBox(
                        width: compact ? 210 : 260,
                        height: compact ? 210 : 260,
                        child: CircularProgressIndicator(
                          value: progress,
                          strokeWidth: compact ? 8 : 10,
                          backgroundColor: palette.line,
                          color: targetHit ? palette.signal : palette.accent,
                          strokeCap: StrokeCap.square,
                        ),
                      ),
                      Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Text(
                            '${_metrics.reps}',
                            style: TextStyle(
                              fontSize: compact ? 88 : 112,
                              height: 0.85,
                              fontWeight: FontWeight.w800,
                              letterSpacing: -7,
                              fontFeatures: const [
                                FontFeature.tabularFigures(),
                              ],
                            ),
                          ),
                          const SizedBox(height: 8),
                          Text(
                            '目标 ${_session.targetReps} 次',
                            style: TextStyle(
                              color: palette.muted,
                              fontSize: 12,
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
                SizedBox(height: compact ? 12 : 20),
                Row(
                  children: [
                    Expanded(
                      child: _TelemetryCard(
                        label: _jointName,
                        value: _metrics.jointAngle == null
                            ? '--'
                            : '${_metrics.jointAngle!.round()}°',
                        accent: palette.cyan,
                        compact: compact,
                      ),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: _TelemetryCard(
                        label: '动作阶段',
                        value: _phaseLabel(_metrics.phase),
                        accent: palette.accent,
                        compact: compact,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 10),
                Row(
                  children: [
                    Expanded(
                      child: _SmallMetric(
                        label: '置信度',
                        value: _metrics.confidence == 0
                            ? '--'
                            : '${(_metrics.confidence * 100).round()}%',
                        compact: compact,
                      ),
                    ),
                    Expanded(
                      child: _SmallMetric(
                        label: '推理帧率',
                        value: _metrics.fps == 0
                            ? '--'
                            : '${_metrics.fps.toStringAsFixed(1)} FPS',
                        compact: compact,
                      ),
                    ),
                    Expanded(
                      child: _SmallMetric(
                        label: '会话',
                        value: _statusLabel(_session.status),
                        compact: compact,
                      ),
                    ),
                  ],
                ),
                if (_latestAlert != null) ...[
                  const SizedBox(height: 12),
                  Container(
                    width: double.infinity,
                    padding: const EdgeInsets.all(13),
                    decoration: BoxDecoration(
                      color: palette.signal.withValues(alpha: 0.12),
                      border: Border(
                        left: BorderSide(color: palette.signal, width: 3),
                      ),
                    ),
                    child: Text(
                      _latestAlert!,
                      style: const TextStyle(fontWeight: FontWeight.w600),
                    ),
                  ),
                ],
                const Spacer(),
                Text(
                  compact ? '原始画面仅保留在镜端' : '原始画面保留在镜端，本页仅接收低频训练状态。',
                  style: TextStyle(color: palette.muted, fontSize: 10),
                ),
                SizedBox(height: compact ? 8 : 12),
                _buildSessionActions(),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _ConnectionPill extends StatelessWidget {
  const _ConnectionPill({required this.online});

  final bool online;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
      decoration: BoxDecoration(
        border: Border.all(color: online ? palette.accent : palette.signal),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        online ? '实时连接' : '重连中',
        style: TextStyle(
          color: online ? palette.accent : palette.signal,
          fontSize: 10,
          fontWeight: FontWeight.w700,
        ),
      ),
    );
  }
}

class _TelemetryCard extends StatelessWidget {
  const _TelemetryCard({
    required this.label,
    required this.value,
    required this.accent,
    required this.compact,
  });

  final String label;
  final String value;
  final Color accent;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    return Container(
      padding: EdgeInsets.all(compact ? 12 : 16),
      decoration: BoxDecoration(
        color: palette.surface.withValues(alpha: 0.9),
        border: Border(top: BorderSide(color: accent, width: 2)),
        borderRadius: BorderRadius.circular(9),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: TextStyle(color: palette.muted, fontSize: 11)),
          const SizedBox(height: 5),
          Text(
            value,
            style: TextStyle(
              color: accent,
              fontSize: compact ? 21 : 25,
              fontWeight: FontWeight.w800,
              fontFeatures: const [FontFeature.tabularFigures()],
            ),
          ),
        ],
      ),
    );
  }
}

class _SmallMetric extends StatelessWidget {
  const _SmallMetric({
    required this.label,
    required this.value,
    required this.compact,
  });

  final String label;
  final String value;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    return Padding(
      padding: EdgeInsets.symmetric(horizontal: 6, vertical: compact ? 4 : 8),
      child: Column(
        children: [
          Text(
            value,
            style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 3),
          Text(label, style: TextStyle(color: palette.muted, fontSize: 9)),
        ],
      ),
    );
  }
}

String _formatDuration(double seconds) {
  final total = seconds.floor();
  final minutes = total ~/ 60;
  final remainder = total % 60;
  return '${minutes.toString().padLeft(2, '0')}:${remainder.toString().padLeft(2, '0')}';
}

String _phaseLabel(String phase) {
  return switch (phase) {
    'up' => '起始',
    'down' => '底部',
    'transition' => '移动中',
    _ => '等待姿态',
  };
}

String _statusLabel(String status) {
  return switch (status) {
    'created' => '已创建',
    'preparing' => '准备中',
    'active' => '训练中',
    'paused' => '已暂停',
    'stopping' => '保存中',
    'analyzing' => '分析中',
    'completed' => '已完成',
    'failed' => '失败',
    'cancelled' => '已取消',
    _ => status,
  };
}
