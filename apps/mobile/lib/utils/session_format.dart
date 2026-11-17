String formatSessionTime(String? isoTimestamp, {DateTime? now}) {
  final parsed = isoTimestamp == null
      ? null
      : DateTime.tryParse(isoTimestamp);
  if (parsed == null) return '时间未知';

  final local = parsed.toLocal();
  final current = (now ?? DateTime.now()).toLocal();
  final day = DateTime(local.year, local.month, local.day);
  final today = DateTime(current.year, current.month, current.day);
  final time = '${_twoDigits(local.hour)}:${_twoDigits(local.minute)}';
  if (day == today) return '今天 $time';
  if (today.difference(day).inDays == 1) return '昨天 $time';
  return '${local.year}-${_twoDigits(local.month)}-${_twoDigits(local.day)} $time';
}

String formatSessionDuration(Object? durationSeconds) {
  if (durationSeconds is! num) return '';
  final total = durationSeconds.round().clamp(0, 24 * 60 * 60);
  if (total < 60) return '$total 秒';
  final minutes = total ~/ 60;
  final seconds = total % 60;
  return seconds == 0 ? '$minutes 分钟' : '$minutes 分 $seconds 秒';
}

String sessionStatusLabel(String status) {
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

String sessionViewLabel(String view) {
  return switch (view) {
    'front' => '正面',
    'rear' => '背面',
    'side_left' => '左侧',
    'side_right' => '右侧',
    'oblique_left' => '左斜侧',
    'oblique_right' => '右斜侧',
    _ => view,
  };
}

String _twoDigits(int value) => value.toString().padLeft(2, '0');
