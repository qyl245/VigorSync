import 'dart:async';

import 'package:material_ui/material_ui.dart';

import '../../data/core_api_client.dart';
import '../../theme/vigorsync_theme.dart';
import '../../widgets/exercise_glyph.dart';

typedef StartPlannedTraining = Future<void> Function({
  required String exercise,
  required String view,
  required int targetReps,
});

const _exerciseLabels = {'squat': '深蹲', 'pushup': '俯卧撑'};

const _viewLabels = {
  'front': '正面',
  'rear': '背面',
  'oblique_left': '左斜',
  'oblique_right': '右斜',
  'side_left': '左侧',
  'side_right': '右侧',
  'unknown': '未知',
};

const _weekdayLabels = ['一', '二', '三', '四', '五', '六', '日'];

bool _canStartPlan(Map<String, dynamic> item) {
  final sets = (item['sets'] as num?)?.toInt();
  final reps = (item['target_reps'] as num?)?.toInt();
  return item['exercise'] != null &&
      item['view'] != null &&
      sets != null &&
      sets > 0 &&
      reps != null &&
      reps > 0;
}

class DashboardPage extends StatefulWidget {
  const DashboardPage({
    super.key,
    required this.api,
    required this.refreshToken,
    required this.connected,
    required this.onStartTraining,
  });

  final CoreApiClient api;
  final int refreshToken;
  final bool connected;
  final StartPlannedTraining onStartTraining;

  @override
  State<DashboardPage> createState() => _DashboardPageState();
}

class _DashboardPageState extends State<DashboardPage> {
  List<Map<String, dynamic>> _trend = const [];
  Map<String, dynamic>? _todos;
  List<Map<String, dynamic>> _plans = const [];
  bool _offline = false;
  int _currentPlanIndex = 0;
  Timer? _ticker;
  final TextEditingController _todoController = TextEditingController();

  @override
  void initState() {
    super.initState();
    _refresh();
    _ticker = Timer.periodic(const Duration(minutes: 5), (_) => _refresh());
  }

  @override
  void dispose() {
    _ticker?.cancel();
    _todoController.dispose();
    super.dispose();
  }

  @override
  void didUpdateWidget(covariant DashboardPage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.refreshToken != widget.refreshToken) {
      _refresh();
    }
  }

  Future<void> _refresh() async {
    try {
      final values = await Future.wait([
        widget.api.getTrend(),
        widget.api.getTodos(),
        widget.api.getWeeklyPlans(),
      ]);
      if (!mounted) return;
      setState(() {
        _trend = values[0] as List<Map<String, dynamic>>;
        _todos = values[1] as Map<String, dynamic>;
        _plans = values[2] as List<Map<String, dynamic>>;
        final todayCount = _plans
            .where(
              (item) =>
                  (item['weekday'] as num?)?.toInt() == DateTime.now().weekday,
            )
            .length;
        if (_currentPlanIndex >= todayCount) {
          _currentPlanIndex = todayCount > 0 ? todayCount - 1 : 0;
        }
        _offline = false;
      });
    } catch (_) {
      if (mounted) setState(() => _offline = true);
    }
  }

  Future<void> _addTodo() async {
    final title = _todoController.text.trim();
    if (title.isEmpty) return;
    try {
      await widget.api.createTodo(title);
      _todoController.clear();
      await _refresh();
    } catch (error) {
      _showError('无法添加待办：$error');
    }
  }

  Future<void> _toggleTodo(Map<String, dynamic> item) async {
    try {
      await widget.api.updateTodo(
        '${item['id']}',
        completed: item['completed'] != true,
      );
      await _refresh();
    } catch (error) {
      _showError('无法更新待办：$error');
    }
  }

  Future<void> _editTodo(Map<String, dynamic> item) async {
    final controller = TextEditingController(text: '${item['title'] ?? ''}');
    final title = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('编辑待办'),
        content: TextField(
          controller: controller,
          autofocus: true,
          maxLength: 200,
          decoration: const InputDecoration(hintText: '待办内容'),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, controller.text.trim()),
            child: const Text('保存'),
          ),
        ],
      ),
    );
    controller.dispose();
    if (title == null || title.isEmpty) return;
    try {
      await widget.api.updateTodo('${item['id']}', title: title);
      await _refresh();
    } catch (error) {
      _showError('无法修改待办：$error');
    }
  }

  Future<void> _deleteTodo(Map<String, dynamic> item) async {
    try {
      await widget.api.deleteTodo('${item['id']}');
      await _refresh();
    } catch (error) {
      _showError('无法删除待办：$error');
    }
  }

  List<Map<String, dynamic>> get _todayPlans {
    final weekday = DateTime.now().weekday;
    final items = _plans
        .where((item) => (item['weekday'] as num?)?.toInt() == weekday)
        .toList();
    items.sort(
      (a, b) => ((a['sort_order'] as num?) ?? 0).compareTo(
        (b['sort_order'] as num?) ?? 0,
      ),
    );
    return items;
  }

  int get _safePlanIndex {
    final length = _todayPlans.length;
    if (length == 0) return 0;
    return _currentPlanIndex >= length ? length - 1 : _currentPlanIndex;
  }

  Future<void> _openWeeklyPlanEditor() async {
    final selectedPlan = _todayPlans.isEmpty
        ? null
        : _todayPlans[_safePlanIndex];
    final selectedId = selectedPlan?['id'];
    final updated = await Navigator.of(context)
        .push<List<Map<String, dynamic>>>(
          MaterialPageRoute(
            builder: (_) =>
                _WeeklyPlanEditorPage(api: widget.api, initialPlans: _plans),
          ),
        );
    if (!mounted || updated == null) return;
    setState(() {
      _plans = updated;
      final today = _todayPlans;
      final preservedIndex = selectedId == null
          ? -1
          : today.indexWhere((item) => item['id'] == selectedId);
      _currentPlanIndex = preservedIndex >= 0 ? preservedIndex : 0;
      if (_currentPlanIndex >= today.length && today.isNotEmpty) {
        _currentPlanIndex = today.length - 1;
      }
    });
  }

  Future<void> _startPlan(Map<String, dynamic> item) async {
    final exercise = item['exercise'] as String?;
    final view = item['view'] as String?;
    final reps = (item['target_reps'] as num?)?.toInt();
    final sets = (item['sets'] as num?)?.toInt();
    if (exercise == null ||
        view == null ||
        reps == null ||
        sets == null ||
        sets < 1) {
      return;
    }
    await widget.onStartTraining(
      exercise: exercise,
      view: view,
      targetReps: reps,
    );
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
    final now = DateTime.now();
    final greeting = now.hour < 12
        ? '早上好'
        : now.hour < 18
        ? '下午好'
        : '晚上好';
    final dateLine =
        '${now.month}月${now.day}日 · 周${_weekdayLabels[now.weekday - 1]}';
    final unavailable = !widget.connected || _offline;
    final statusColor = unavailable ? palette.signal : palette.accent;
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
                Container(
                  width: 7,
                  height: 7,
                  decoration: BoxDecoration(
                    color: statusColor,
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 6),
                Text(
                  !widget.connected
                      ? '未连接'
                      : _offline
                      ? '离线'
                      : '已同步',
                  style: TextStyle(color: statusColor, fontSize: 11),
                ),
              ],
            ),
            const SizedBox(height: 20),
            Text(
              dateLine,
              style: TextStyle(color: palette.muted, fontSize: 11),
            ),
            const SizedBox(height: 3),
            Text(
              greeting,
              style: theme.textTheme.headlineLarge?.copyWith(fontSize: 30),
            ),
            const SizedBox(height: 18),
            _TodayPlanCard(
              plans: _todayPlans,
              selectedIndex: _safePlanIndex,
              offline: unavailable,
              onSelectIndex: (index) {
                setState(() => _currentPlanIndex = index);
              },
              onStart: _startPlan,
              onAdjust: () => unawaited(_openWeeklyPlanEditor()),
            ),
            const SizedBox(height: 14),
            _TrendCard(days: _trend, offline: unavailable),
            const SizedBox(height: 14),
            _TodoCard(
              data: _todos,
              offline: unavailable,
              controller: _todoController,
              onAdd: _addTodo,
              onToggle: _toggleTodo,
              onEdit: _editTodo,
              onDelete: _deleteTodo,
            ),
          ],
        ),
      ),
    );
  }
}

class _TodayPlanCard extends StatelessWidget {
  const _TodayPlanCard({
    required this.plans,
    required this.selectedIndex,
    required this.offline,
    required this.onSelectIndex,
    required this.onStart,
    required this.onAdjust,
  });

  final List<Map<String, dynamic>> plans;
  final int selectedIndex;
  final bool offline;
  final ValueChanged<int> onSelectIndex;
  final Future<void> Function(Map<String, dynamic>) onStart;
  final VoidCallback onAdjust;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    final hasPlan = plans.isNotEmpty;
    final item = hasPlan ? plans[selectedIndex] : null;
    final exercise = '${item?['exercise'] ?? ''}';
    final exerciseLabel = _exerciseLabels[exercise] ?? '训练';
    final viewLabel = _viewLabels[item?['view']];
    final sets = (item?['sets'] as num?)?.toInt();
    final reps = (item?['target_reps'] as num?)?.toInt();
    final note = '${item?['note'] ?? ''}'.trim();
    final detail = [
      if (sets != null) '$sets 组',
      if (reps != null) '每组 $reps 次',
    ].join(' × ');
    final canStart = item != null && _canStartPlan(item);
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: palette.surface.withValues(alpha: 0.96),
        border: Border.all(color: palette.accent.withValues(alpha: 0.42)),
        borderRadius: BorderRadius.circular(18),
        boxShadow: [
          BoxShadow(
            color: palette.accent.withValues(alpha: 0.055),
            blurRadius: 24,
            spreadRadius: 2,
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Text(
                '今日计划',
                style: TextStyle(
                  color: palette.muted,
                  fontSize: 11,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 1.4,
                ),
              ),
              const Spacer(),
              if (hasPlan)
                Text(
                  '${selectedIndex + 1} / ${plans.length}',
                  style: TextStyle(
                    color: palette.muted,
                    fontSize: 10,
                    fontWeight: FontWeight.w700,
                    fontFeatures: const [FontFeature.tabularFigures()],
                  ),
                ),
            ],
          ),
          const SizedBox(height: 16),
          if (!hasPlan) ...[
            Container(
              padding: const EdgeInsets.symmetric(vertical: 18),
              decoration: BoxDecoration(
                color: palette.elevated.withValues(alpha: 0.48),
                borderRadius: BorderRadius.circular(12),
              ),
              child: Column(
                children: [
                  Icon(
                    offline ? Icons.cloud_off_outlined : Icons.event_note,
                    color: palette.muted,
                    size: 24,
                  ),
                  const SizedBox(height: 8),
                  Text(
                    offline ? '恢复连接后显示今日计划' : '今天还没有训练安排',
                    style: TextStyle(color: palette.muted, fontSize: 12),
                  ),
                ],
              ),
            ),
          ] else ...[
            Row(
              crossAxisAlignment: CrossAxisAlignment.center,
              children: [
                ExerciseGlyph(
                  exercise: exercise,
                  color: palette.accent,
                  size: 48,
                ),
                const SizedBox(width: 14),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Flexible(
                            child: Text(
                              exerciseLabel,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                fontSize: 26,
                                fontWeight: FontWeight.w800,
                                height: 1.05,
                              ),
                            ),
                          ),
                          if (viewLabel != null) ...[
                            const SizedBox(width: 8),
                            Container(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 7,
                                vertical: 3,
                              ),
                              decoration: BoxDecoration(
                                color: palette.elevated,
                                borderRadius: BorderRadius.circular(99),
                              ),
                              child: Text(
                                viewLabel,
                                style: TextStyle(
                                  color: palette.muted,
                                  fontSize: 10,
                                ),
                              ),
                            ),
                          ],
                        ],
                      ),
                      if (detail.isNotEmpty) ...[
                        const SizedBox(height: 6),
                        Text(
                          detail,
                          style: TextStyle(
                            color: palette.ink,
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ],
                      if (note.isNotEmpty) ...[
                        const SizedBox(height: 5),
                        Text(
                          note,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            color: palette.muted,
                            fontSize: 11,
                            height: 1.35,
                          ),
                        ),
                      ],
                    ],
                  ),
                ),
              ],
            ),
            if (!canStart) ...[
              const SizedBox(height: 10),
              Text(
                '请补充视角、组数和次数后再开始训练',
                style: TextStyle(color: palette.signal, fontSize: 10),
              ),
            ],
          ],
          const SizedBox(height: 16),
          if (canStart)
            FilledButton.icon(
              onPressed: () => onStart(item),
              icon: const Icon(Icons.play_arrow_rounded, size: 20),
              label: const Text('开始训练'),
            )
          else
            OutlinedButton.icon(
              onPressed: offline ? null : onAdjust,
              icon: const Icon(Icons.tune, size: 17),
              label: Text(hasPlan ? '补全训练信息' : '调整本周计划'),
            ),
          const SizedBox(height: 8),
          Row(
            children: [
              _PlanArrowButton(
                tooltip: '上一项',
                icon: Icons.arrow_back_rounded,
                onPressed: hasPlan && selectedIndex > 0
                    ? () => onSelectIndex(selectedIndex - 1)
                    : null,
              ),
              const SizedBox(width: 6),
              _PlanArrowButton(
                tooltip: '下一项',
                icon: Icons.arrow_forward_rounded,
                onPressed: hasPlan && selectedIndex < plans.length - 1
                    ? () => onSelectIndex(selectedIndex + 1)
                    : null,
              ),
              const Spacer(),
              TextButton(
                onPressed: offline ? null : onAdjust,
                child: const Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text('调整本周计划'),
                    SizedBox(width: 2),
                    Icon(Icons.arrow_forward_ios, size: 12),
                  ],
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _PlanArrowButton extends StatelessWidget {
  const _PlanArrowButton({
    required this.tooltip,
    required this.icon,
    required this.onPressed,
  });

  final String tooltip;
  final IconData icon;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    return IconButton(
      onPressed: onPressed,
      tooltip: tooltip,
      icon: Icon(icon, size: 18),
      style: IconButton.styleFrom(
        minimumSize: const Size(42, 42),
        foregroundColor: palette.ink,
        disabledForegroundColor: palette.muted.withValues(alpha: 0.35),
        side: BorderSide(color: palette.line),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
      ),
    );
  }
}

class _ModuleCard extends StatelessWidget {
  const _ModuleCard({required this.title, required this.child});

  final String title;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: palette.surface.withValues(alpha: 0.92),
        border: Border.all(color: palette.line),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            title,
            style: TextStyle(
              color: palette.muted,
              fontSize: 10,
              fontWeight: FontWeight.w700,
              letterSpacing: 1.4,
            ),
          ),
          const SizedBox(height: 12),
          child,
        ],
      ),
    );
  }
}

class _TrendCard extends StatelessWidget {
  const _TrendCard({required this.days, required this.offline});

  final List<Map<String, dynamic>> days;
  final bool offline;

  static const _dayLabels = ['日', '一', '二', '三', '四', '五', '六'];

  String _dayLabel(Object? date, String todayKey) {
    if (date == todayKey) return '今天';
    final parsed = DateTime.tryParse('${date}T00:00:00');
    if (parsed == null) return '--';
    return _dayLabels[parsed.weekday % 7];
  }

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    final items = days.length > 7 ? days.sublist(days.length - 7) : days;
    final now = DateTime.now();
    final todayKey =
        '${now.year}-${now.month.toString().padLeft(2, '0')}-${now.day.toString().padLeft(2, '0')}';
    final maxMinutes = items.fold<double>(
      1,
      (value, day) => ((day['duration_s'] as num?) ?? 0) / 60 > value
          ? ((day['duration_s'] as num?) ?? 0) / 60
          : value,
    );
    final totalSessions = items.fold<int>(
      0,
      (value, day) => value + ((day['sessions'] as num?) ?? 0).toInt(),
    );
    final totalMinutes = items.fold<double>(
      0,
      (value, day) => value + ((day['duration_s'] as num?) ?? 0) / 60,
    );
    return _ModuleCard(
      title: '近 7 天训练趋势',
      child: Column(
        children: [
          if (items.isEmpty)
            Container(
              height: 86,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: palette.elevated.withValues(alpha: 0.45),
                borderRadius: BorderRadius.circular(10),
              ),
              child: Text(
                offline ? '离线，恢复连接后自动更新' : '还没有训练记录',
                style: TextStyle(color: palette.muted, fontSize: 11),
              ),
            )
          else ...[
            SizedBox(
              height: 72,
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  for (final day in items)
                    Expanded(
                      child: Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 3),
                        child: Align(
                          alignment: Alignment.bottomCenter,
                          child: Container(
                            height:
                                4 +
                                64 *
                                    ((((day['duration_s'] as num?) ?? 0) / 60) /
                                            maxMinutes)
                                        .clamp(0.0, 1.0),
                            decoration: BoxDecoration(
                              color: day['date'] == todayKey
                                  ? palette.accent
                                  : palette.muted.withValues(alpha: 0.35),
                              borderRadius: const BorderRadius.vertical(
                                top: Radius.circular(2),
                              ),
                            ),
                          ),
                        ),
                      ),
                    ),
                ],
              ),
            ),
            const SizedBox(height: 6),
            Row(
              children: [
                for (final day in items)
                  Expanded(
                    child: Text(
                      _dayLabel(day['date'], todayKey),
                      textAlign: TextAlign.center,
                      style: TextStyle(
                        color: day['date'] == todayKey
                            ? palette.accent
                            : palette.muted,
                        fontSize: 9,
                      ),
                    ),
                  ),
              ],
            ),
          ],
          const SizedBox(height: 10),
          Align(
            alignment: Alignment.centerLeft,
            child: Text(
              '本周 $totalSessions 次 · ${totalMinutes.round()} 分钟',
              style: TextStyle(color: palette.muted, fontSize: 11),
            ),
          ),
        ],
      ),
    );
  }
}

class _TodoCard extends StatelessWidget {
  const _TodoCard({
    required this.data,
    required this.offline,
    required this.controller,
    required this.onAdd,
    required this.onToggle,
    required this.onEdit,
    required this.onDelete,
  });

  final Map<String, dynamic>? data;
  final bool offline;
  final TextEditingController controller;
  final Future<void> Function() onAdd;
  final Future<void> Function(Map<String, dynamic>) onToggle;
  final Future<void> Function(Map<String, dynamic>) onEdit;
  final Future<void> Function(Map<String, dynamic>) onDelete;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    final items = ((data?['items'] as List<dynamic>?) ?? const [])
        .whereType<Map<String, dynamic>>()
        .toList();
    final completed = (data?['completed'] as num?)?.toInt() ?? 0;
    final total = (data?['total'] as num?)?.toInt() ?? items.length;
    return _ModuleCard(
      title: data == null ? '今日待办' : '今日待办 · $completed/$total',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: controller,
                  enabled: !offline,
                  maxLength: 200,
                  textInputAction: TextInputAction.done,
                  onSubmitted: (_) => onAdd(),
                  decoration: const InputDecoration(
                    hintText: '添加待办',
                    counterText: '',
                    isDense: true,
                  ),
                ),
              ),
              const SizedBox(width: 6),
              IconButton(
                onPressed: offline ? null : onAdd,
                tooltip: '添加待办',
                visualDensity: VisualDensity.compact,
                icon: const Icon(Icons.add, size: 18),
              ),
            ],
          ),
          const SizedBox(height: 8),
          if (items.isEmpty)
            Text(
              offline && data == null ? '恢复连接后显示待办' : '今天还没有待办',
              style: TextStyle(color: palette.muted, fontSize: 11),
            )
          else
            for (final item in items)
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SizedBox(
                    width: 28,
                    height: 28,
                    child: Checkbox(
                      value: item['completed'] == true,
                      onChanged: offline ? null : (_) => onToggle(item),
                      visualDensity: VisualDensity.compact,
                    ),
                  ),
                  Expanded(
                    child: Padding(
                      padding: const EdgeInsets.only(top: 5),
                      child: Text(
                        '${item['title'] ?? ''}',
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          fontSize: 11,
                          height: 1.35,
                          color: item['completed'] == true
                              ? palette.muted
                              : Theme.of(context).colorScheme.onSurface,
                          decoration: item['completed'] == true
                              ? TextDecoration.lineThrough
                              : null,
                        ),
                      ),
                    ),
                  ),
                  PopupMenuButton<String>(
                    enabled: !offline,
                    tooltip: '待办操作',
                    onSelected: (value) {
                      if (value == 'edit') onEdit(item);
                      if (value == 'delete') onDelete(item);
                    },
                    itemBuilder: (_) => const [
                      PopupMenuItem(value: 'edit', child: Text('编辑')),
                      PopupMenuItem(value: 'delete', child: Text('删除')),
                    ],
                    icon: const Icon(Icons.more_horiz, size: 16),
                    padding: EdgeInsets.zero,
                  ),
                ],
              ),
        ],
      ),
    );
  }
}

class _WeeklyPlanEditorPage extends StatefulWidget {
  const _WeeklyPlanEditorPage({required this.api, required this.initialPlans});

  final CoreApiClient api;
  final List<Map<String, dynamic>> initialPlans;

  @override
  State<_WeeklyPlanEditorPage> createState() => _WeeklyPlanEditorPageState();
}

class _WeeklyPlanEditorPageState extends State<_WeeklyPlanEditorPage> {
  late List<Map<String, dynamic>> _plans = [
    for (final item in widget.initialPlans) Map<String, dynamic>.from(item),
  ];
  late int _selectedWeekday = DateTime.now().weekday;
  bool _dirty = false;
  bool _saving = false;

  List<Map<String, dynamic>> get _selectedPlans {
    final items =
        _plans
            .where(
              (item) => (item['weekday'] as num?)?.toInt() == _selectedWeekday,
            )
            .toList()
          ..sort(
            (a, b) => ((a['sort_order'] as num?) ?? 0).compareTo(
              (b['sort_order'] as num?) ?? 0,
            ),
          );
    return items;
  }

  Future<void> _editPlan([Map<String, dynamic>? item]) async {
    final result = await showModalBottomSheet<Map<String, dynamic>>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => _PlanEditorSheet(
        weekday: _selectedWeekday,
        initial: item,
        sortOrder:
            (item?['sort_order'] as num?)?.toInt() ?? _selectedPlans.length,
      ),
    );
    if (result == null || !mounted) return;
    setState(() {
      final next = [..._plans];
      if (item == null) {
        next.add(result);
      } else {
        final index = next.indexWhere((entry) => entry['id'] == item['id']);
        if (index >= 0) next[index] = result;
      }
      next.sort((a, b) {
        final dayCompare = ((a['weekday'] as num?) ?? 0).compareTo(
          (b['weekday'] as num?) ?? 0,
        );
        if (dayCompare != 0) return dayCompare;
        return ((a['sort_order'] as num?) ?? 0).compareTo(
          (b['sort_order'] as num?) ?? 0,
        );
      });
      _plans = next;
      _dirty = true;
    });
  }

  void _deletePlan(Map<String, dynamic> item) {
    setState(() {
      _plans = _plans.where((entry) => entry['id'] != item['id']).toList();
      _dirty = true;
    });
  }

  Future<void> _save() async {
    setState(() => _saving = true);
    try {
      final saved = await widget.api.replaceWeeklyPlans(
        _plans
            .map(
              (item) => <String, dynamic>{
                if (item['id'] != null) 'id': item['id'],
                'weekday': item['weekday'],
                'exercise': item['exercise'],
                'view': item['view'],
                'sets': item['sets'],
                'target_reps': item['target_reps'],
                'note': item['note'],
                'sort_order': item['sort_order'],
              },
            )
            .toList(),
      );
      if (!mounted) return;
      Navigator.pop(context, saved);
    } on CoreApiException catch (error) {
      _showError(error.message);
    } catch (error) {
      _showError('无法保存计划：$error');
    } finally {
      if (mounted) setState(() => _saving = false);
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
    final today = DateTime.now().weekday;
    final items = _selectedPlans;
    return Scaffold(
      body: VigorPage(
        child: SafeArea(
          child: Column(
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(12, 8, 20, 10),
                child: Row(
                  children: [
                    IconButton(
                      onPressed: () => Navigator.pop(context),
                      icon: const Icon(Icons.close),
                      tooltip: '关闭',
                    ),
                    const SizedBox(width: 4),
                    Expanded(
                      child: Text('调整本周计划', style: theme.textTheme.titleLarge),
                    ),
                    if (_dirty)
                      Text(
                        '未保存',
                        style: TextStyle(color: palette.signal, fontSize: 10),
                      ),
                  ],
                ),
              ),
              Expanded(
                child: ListView(
                  padding: const EdgeInsets.fromLTRB(20, 8, 20, 24),
                  children: [
                    Text(
                      '选择星期并管理当天的训练项目，保存后会同步到 App 首页和镜端。',
                      style: TextStyle(
                        color: palette.muted,
                        fontSize: 12,
                        height: 1.55,
                      ),
                    ),
                    const SizedBox(height: 16),
                    SizedBox(
                      height: 40,
                      child: ListView.separated(
                        scrollDirection: Axis.horizontal,
                        itemCount: 7,
                        separatorBuilder: (_, _) => const SizedBox(width: 6),
                        itemBuilder: (context, index) {
                          final weekday = index + 1;
                          return ChoiceChip(
                            label: Text(
                              weekday == today ? '今' : _weekdayLabels[index],
                              style: const TextStyle(fontSize: 11),
                            ),
                            selected: weekday == _selectedWeekday,
                            onSelected: (_) {
                              setState(() => _selectedWeekday = weekday);
                            },
                            visualDensity: VisualDensity.compact,
                          );
                        },
                      ),
                    ),
                    const SizedBox(height: 18),
                    Row(
                      children: [
                        Text(
                          '周${_weekdayLabels[_selectedWeekday - 1]} · ${items.length} 项',
                          style: const TextStyle(
                            fontSize: 13,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                        const Spacer(),
                        TextButton.icon(
                          onPressed: _editPlan,
                          icon: const Icon(Icons.add, size: 16),
                          label: const Text('新增'),
                        ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    if (items.isEmpty)
                      Container(
                        padding: const EdgeInsets.symmetric(vertical: 28),
                        alignment: Alignment.center,
                        decoration: BoxDecoration(
                          color: palette.elevated.withValues(alpha: 0.5),
                          borderRadius: BorderRadius.circular(12),
                        ),
                        child: Text(
                          '当天暂无训练安排',
                          style: TextStyle(color: palette.muted, fontSize: 12),
                        ),
                      )
                    else
                      for (final item in items)
                        _PlanRow(
                          item: item,
                          exerciseLabel:
                              _exerciseLabels[item['exercise']] ??
                              '${item['exercise'] ?? '训练'}',
                          viewLabel: _viewLabels[item['view']],
                          onEdit: () => _editPlan(item),
                          onDelete: () => _deletePlan(item),
                        ),
                    const SizedBox(height: 16),
                    OutlinedButton.icon(
                      onPressed: _editPlan,
                      icon: const Icon(Icons.add, size: 16),
                      label: const Text('新增训练项目'),
                    ),
                  ],
                ),
              ),
              Container(
                padding: const EdgeInsets.fromLTRB(20, 12, 20, 20),
                decoration: BoxDecoration(
                  color: palette.backgroundTop.withValues(alpha: 0.96),
                  border: Border(top: BorderSide(color: palette.line)),
                ),
                child: FilledButton.icon(
                  onPressed: _dirty && !_saving ? _save : null,
                  icon: _saving
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 1.8),
                        )
                      : const Icon(Icons.save_outlined, size: 18),
                  label: Text(
                    _saving
                        ? '保存中…'
                        : _dirty
                        ? '保存本周计划'
                        : '暂无修改',
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _PlanRow extends StatelessWidget {
  const _PlanRow({
    required this.item,
    required this.exerciseLabel,
    required this.viewLabel,
    required this.onEdit,
    required this.onDelete,
  });

  final Map<String, dynamic> item;
  final String exerciseLabel;
  final String? viewLabel;
  final VoidCallback onEdit;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    final sets = (item['sets'] as num?)?.toInt();
    final reps = (item['target_reps'] as num?)?.toInt();
    final note = '${item['note'] ?? ''}'.trim();
    final detail = [
      if (viewLabel != null) viewLabel,
      if (sets != null) '$sets 组',
      if (reps != null) '$reps 次',
    ].whereType<String>().join(' · ');
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.fromLTRB(12, 10, 4, 10),
      decoration: BoxDecoration(
        color: palette.elevated.withValues(alpha: 0.58),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  exerciseLabel,
                  style: const TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                if (detail.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 2),
                    child: Text(
                      detail,
                      style: TextStyle(color: palette.muted, fontSize: 10.5),
                    ),
                  ),
                if (note.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 3),
                    child: Text(
                      note,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(color: palette.muted, fontSize: 10),
                    ),
                  ),
              ],
            ),
          ),
          PopupMenuButton<String>(
            tooltip: '计划操作',
            onSelected: (value) {
              if (value == 'edit') onEdit();
              if (value == 'delete') onDelete();
            },
            itemBuilder: (_) => const [
              PopupMenuItem(value: 'edit', child: Text('编辑')),
              PopupMenuItem(value: 'delete', child: Text('删除')),
            ],
            icon: const Icon(Icons.more_horiz, size: 18),
            padding: EdgeInsets.zero,
          ),
        ],
      ),
    );
  }
}

class _PlanEditorSheet extends StatefulWidget {
  const _PlanEditorSheet({
    required this.weekday,
    required this.initial,
    required this.sortOrder,
  });

  final int weekday;
  final Map<String, dynamic>? initial;
  final int sortOrder;

  @override
  State<_PlanEditorSheet> createState() => _PlanEditorSheetState();
}

class _PlanEditorSheetState extends State<_PlanEditorSheet> {
  late String _exercise = '${widget.initial?['exercise'] ?? 'squat'}';
  late String _view = '${widget.initial?['view'] ?? ''}';
  late final TextEditingController _setsController = TextEditingController(
    text: widget.initial?['sets']?.toString() ?? '',
  );
  late final TextEditingController _repsController = TextEditingController(
    text: widget.initial?['target_reps']?.toString() ?? '12',
  );
  late final TextEditingController _noteController = TextEditingController(
    text: widget.initial?['note']?.toString() ?? '',
  );

  @override
  void dispose() {
    _setsController.dispose();
    _repsController.dispose();
    _noteController.dispose();
    super.dispose();
  }

  void _submit() {
    final reps = int.tryParse(_repsController.text.trim());
    final setText = _setsController.text.trim();
    final sets = setText.isEmpty ? null : int.tryParse(setText);
    if (reps == null || reps < 1) {
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('请填写有效的目标次数')));
      return;
    }
    if (setText.isNotEmpty && (sets == null || sets < 1)) {
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('组数必须为正整数')));
      return;
    }
    final note = _noteController.text.trim();
    Navigator.pop(context, <String, dynamic>{
      if (widget.initial?['id'] != null) 'id': widget.initial!['id'],
      'weekday': widget.weekday,
      'exercise': _exercise,
      'view': _view.isEmpty ? null : _view,
      'sets': sets,
      'target_reps': reps,
      'note': note.isEmpty ? null : note,
      'sort_order': widget.sortOrder,
    });
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: Padding(
        padding: EdgeInsets.fromLTRB(
          20,
          4,
          20,
          MediaQuery.viewInsetsOf(context).bottom + 20,
        ),
        child: ListView(
          shrinkWrap: true,
          children: [
            Text(
              '${widget.initial == null ? '新增' : '编辑'}训练计划 · 周${_dayLabel(widget.weekday)}',
              style: Theme.of(context).textTheme.titleLarge,
            ),
            const SizedBox(height: 16),
            SegmentedButton<String>(
              segments: const [
                ButtonSegment(value: 'squat', label: Text('深蹲')),
                ButtonSegment(value: 'pushup', label: Text('俯卧撑')),
              ],
              selected: {_exercise},
              onSelectionChanged: (value) {
                setState(() => _exercise = value.first);
              },
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              initialValue: _view,
              decoration: const InputDecoration(labelText: '视角（选填）'),
              items: const [
                DropdownMenuItem(value: '', child: Text('不填写')),
                DropdownMenuItem(value: 'front', child: Text('正面')),
                DropdownMenuItem(value: 'rear', child: Text('背面')),
                DropdownMenuItem(value: 'oblique_left', child: Text('左斜')),
                DropdownMenuItem(value: 'oblique_right', child: Text('右斜')),
                DropdownMenuItem(value: 'side_left', child: Text('左侧')),
                DropdownMenuItem(value: 'side_right', child: Text('右侧')),
                DropdownMenuItem(value: 'unknown', child: Text('未知')),
              ],
              onChanged: (value) => setState(() => _view = value ?? ''),
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _setsController,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(labelText: '组数（选填）'),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: TextField(
                    controller: _repsController,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(labelText: '每组次数'),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _noteController,
              maxLength: 300,
              decoration: const InputDecoration(labelText: '备注（选填）'),
            ),
            const SizedBox(height: 8),
            FilledButton(onPressed: _submit, child: const Text('加入本周草稿')),
          ],
        ),
      ),
    );
  }

  String _dayLabel(int weekday) =>
      const ['一', '二', '三', '四', '五', '六', '日'][weekday - 1];
}
