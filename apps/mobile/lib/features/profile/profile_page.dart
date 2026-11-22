import 'dart:async';

import 'package:material_ui/material_ui.dart';

import '../../data/core_api_client.dart';
import '../../theme/vigorsync_theme.dart';

const _moduleCatalog = [
  ('weather', '天气'),
  ('trend', '训练趋势'),
  ('todo', '今日待办'),
  ('weekly_plan', '本周计划'),
];

const _moduleIcons = {
  'weather': Icons.wb_sunny_outlined,
  'trend': Icons.bar_chart_rounded,
  'todo': Icons.checklist_rounded,
  'weekly_plan': Icons.event_note_rounded,
};

String _moduleTitle(String id) {
  for (final entry in _moduleCatalog) {
    if (entry.$1 == id) return entry.$2;
  }
  return id;
}

class _ModuleConfig {
  _ModuleConfig({
    required this.id,
    this.enabled = false,
    this.size = '1x1',
    this.column = 'left',
    this.order = 0,
  });

  final String id;
  bool enabled;
  String size;
  String column;
  int order;

  _ModuleConfig clone() => _ModuleConfig(
    id: id,
    enabled: enabled,
    size: size,
    column: column,
    order: order,
  );

  Map<String, dynamic> toJson() => {
    'id': id,
    'size': size,
    'column': column,
    'order': order,
  };
}

class ProfilePage extends StatefulWidget {
  const ProfilePage({
    super.key,
    required this.api,
    required this.refreshToken,
    required this.connected,
    required this.isDarkMode,
    required this.onToggleTheme,
    required this.onLogout,
    required this.onReconnect,
  });

  final CoreApiClient api;
  final int refreshToken;
  final bool connected;
  final bool isDarkMode;
  final VoidCallback onToggleTheme;
  final Future<void> Function() onLogout;
  final Future<bool> Function() onReconnect;

  @override
  State<ProfilePage> createState() => _ProfilePageState();
}

class _ProfilePageState extends State<ProfilePage> {
  late List<_ModuleConfig> _configs = [
    for (final entry in _moduleCatalog) _ModuleConfig(id: entry.$1),
  ];
  List<Map<String, dynamic>> _devices = const [];
  bool _loading = true;
  bool _reconnecting = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void didUpdateWidget(covariant ProfilePage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.refreshToken != widget.refreshToken) {
      _load();
    }
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final values = await Future.wait([
        widget.api.getMirrorLayout(),
        widget.api.listPairedDevices(),
      ]);
      final layout = values[0] as Map<String, dynamic>;
      final devices = values[1] as List<Map<String, dynamic>>;
      final modules = ((layout['modules'] as List<dynamic>?) ?? const [])
          .whereType<Map<String, dynamic>>();
      for (final config in _configs) {
        final match = modules
            .where((module) => module['id'] == config.id)
            .firstOrNull;
        config.enabled = match != null;
        config.size = (match?['size'] as String?) ?? '1x1';
        config.column = (match?['column'] as String?) ?? 'left';
        config.order = ((match?['order'] as num?) ?? 0).toInt();
      }
      if (!mounted) return;
      setState(() {
        _devices = devices;
        _loading = false;
        _error = null;
      });
    } catch (_) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = '无法连接 Core，设备状态暂不可用';
        });
      }
    }
  }

  Future<void> _openLayoutEditor() async {
    final updated = await Navigator.of(context).push<List<_ModuleConfig>>(
      MaterialPageRoute(
        builder: (_) =>
            _MirrorLayoutEditorPage(api: widget.api, initialConfigs: _configs),
      ),
    );
    if (!mounted || updated == null) return;
    setState(() => _configs = updated);
    ScaffoldMessenger.of(context)
        .showSnackBar(const SnackBar(content: Text('镜端布局已更新')));
  }

  Future<void> _showDevices() async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (context) {
        final palette = context.vigor;
        return SafeArea(
          child: SizedBox(
            height: MediaQuery.sizeOf(context).height * 0.68,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(20, 4, 20, 20),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text('已配对设备', style: Theme.of(context).textTheme.titleLarge),
                  const SizedBox(height: 4),
                  Text(
                    '当前连接：${widget.api.baseUrl}',
                    style: TextStyle(color: palette.muted, fontSize: 11),
                  ),
                  const SizedBox(height: 16),
                  Expanded(
                    child: _devices.isEmpty
                        ? Center(
                            child: Text(
                              '暂无已配对设备',
                              style: TextStyle(color: palette.muted),
                            ),
                          )
                        : ListView(
                            children: [
                              for (final device in _devices)
                                _DeviceRow(device: device),
                            ],
                          ),
                  ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }

  Future<void> _confirmUnpair() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('解除当前配对？'),
        content: const Text('解除后需要重新输入镜端配对码才能继续连接。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            style: FilledButton.styleFrom(
              backgroundColor: context.vigor.signal,
              foregroundColor: Colors.white,
            ),
            child: const Text('解除配对'),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      await widget.onLogout();
    }
  }

  Future<void> _reconnect() async {
    if (_reconnecting) return;
    setState(() => _reconnecting = true);
    var connected = false;
    try {
      connected = await widget.onReconnect();
    } catch (_) {
      connected = false;
    }
    if (!mounted) return;
    setState(() => _reconnecting = false);
    if (connected) {
      await _load();
    } else {
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('未在局域网找到已配对镜端')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final palette = context.vigor;
    final enabled = _configs.where((config) => config.enabled).toList();
    final leftCount = enabled.where((config) => config.column == 'left').length;
    final rightCount = enabled.length - leftCount;
    final offline = !_loading && _error != null;
    final online = widget.connected && !offline;
    return SafeArea(
      child: RefreshIndicator(
        onRefresh: _load,
        color: palette.accent,
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.fromLTRB(22, 22, 22, 32),
          children: [
            Row(
              children: [
                const BrandMark(compact: true),
                const SizedBox(width: 12),
                const Expanded(
                  child: Text(
                    '我的',
                    style: TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w800,
                      letterSpacing: 2.1,
                    ),
                  ),
                ),
                Text(
                  _loading
                      ? '同步中'
                      : !widget.connected
                      ? '未连接'
                      : offline
                      ? '离线'
                      : '已同步',
                  style: TextStyle(
                    color: online ? palette.muted : palette.signal,
                    fontSize: 10,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 20),
            Text(
              '设备与偏好',
              style: theme.textTheme.headlineLarge?.copyWith(fontSize: 30),
            ),
            const SizedBox(height: 6),
            Text(
              '管理镜端显示、连接状态与 App 外观。',
              style: TextStyle(color: palette.muted, fontSize: 12, height: 1.5),
            ),
            const SizedBox(height: 18),
            _ConnectionCard(
              loading: _loading,
              connected: widget.connected,
              apiUnavailable: offline,
              baseUrl: widget.api.baseUrl,
              reconnecting: _reconnecting,
              onReconnect: !online && !_loading
                  ? () => unawaited(_reconnect())
                  : null,
            ),
            const SizedBox(height: 24),
            const _SectionTitle('镜端显示'),
            const SizedBox(height: 10),
            _MirrorDisplayCard(
              configs: _configs,
              loading: _loading,
              unavailable: !online,
              leftCount: leftCount,
              rightCount: rightCount,
              onEdit: () => unawaited(_openLayoutEditor()),
            ),
            const SizedBox(height: 22),
            const _SectionTitle('外观'),
            const SizedBox(height: 10),
            _SettingsTile(
              icon: widget.isDarkMode
                  ? Icons.dark_mode_outlined
                  : Icons.light_mode_outlined,
              title: '深色模式',
              subtitle: widget.isDarkMode ? '当前使用深色主题' : '当前使用浅色主题',
              trailing: Switch(
                value: widget.isDarkMode,
                onChanged: (_) => widget.onToggleTheme(),
              ),
            ),
            const SizedBox(height: 22),
            const _SectionTitle('配对与连接'),
            const SizedBox(height: 10),
            _SettingsTile(
              icon: Icons.devices_rounded,
              title: '已配对设备',
              subtitle: _loading
                  ? '正在读取设备信息'
                  : _devices.isEmpty
                  ? '暂无已配对设备'
                  : '${_devices.length} 台设备 · ${online ? '实时连接正常' : '当前离线'}',
              onTap: () => unawaited(_showDevices()),
              trailing: Icon(
                Icons.chevron_right,
                color: palette.muted,
                size: 20,
              ),
            ),
            const SizedBox(height: 22),
            const _SectionTitle('关于'),
            const SizedBox(height: 10),
            const _SettingsTile(
              icon: Icons.info_outline_rounded,
              title: 'VigorSync Mobile',
              subtitle: '版本 1.0.0 · 手机控制与训练复盘',
            ),
            const SizedBox(height: 30),
            OutlinedButton.icon(
              onPressed: () => unawaited(_confirmUnpair()),
              style: OutlinedButton.styleFrom(
                foregroundColor: palette.signal,
                backgroundColor: palette.signal.withValues(alpha: 0.06),
                side: BorderSide(color: palette.signal.withValues(alpha: 0.35)),
              ),
              icon: const Icon(Icons.link_off_rounded, size: 18),
              label: const Text('解除当前配对'),
            ),
            const SizedBox(height: 8),
            Text(
              '解除后需要重新输入镜端配对码才能连接。',
              textAlign: TextAlign.center,
              style: TextStyle(color: palette.muted, fontSize: 10),
            ),
          ],
        ),
      ),
    );
  }
}

class _ConnectionCard extends StatelessWidget {
  const _ConnectionCard({
    required this.loading,
    required this.connected,
    required this.apiUnavailable,
    required this.baseUrl,
    required this.reconnecting,
    required this.onReconnect,
  });

  final bool loading;
  final bool connected;
  final bool apiUnavailable;
  final String baseUrl;
  final bool reconnecting;
  final VoidCallback? onReconnect;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    final statusColor = connected
        ? palette.accent
        : loading
        ? palette.cyan
        : palette.signal;
    final status = loading
        ? '正在连接'
        : connected
        ? '已连接'
        : '当前离线';
    final subtitle = loading
        ? '正在读取镜端状态'
        : connected
        ? '手机与 Core 的实时通道正常'
        : apiUnavailable
        ? '配对可能已失效，或 Core 当前不可达'
        : '实时通道未建立，请重新连接';
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: palette.surface.withValues(alpha: 0.95),
        border: Border.all(color: statusColor.withValues(alpha: 0.38)),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Row(
        children: [
          Container(
            width: 42,
            height: 42,
            decoration: BoxDecoration(
              color: statusColor.withValues(alpha: 0.12),
              borderRadius: BorderRadius.circular(12),
            ),
            child: Icon(Icons.sensors_rounded, color: statusColor, size: 21),
          ),
          const SizedBox(width: 13),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Container(
                      width: 7,
                      height: 7,
                      decoration: BoxDecoration(
                        color: statusColor,
                        shape: BoxShape.circle,
                      ),
                    ),
                    const SizedBox(width: 7),
                    Text(
                      status,
                      style: const TextStyle(
                        fontSize: 14,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 3),
                Text(
                  subtitle,
                  style: TextStyle(color: palette.muted, fontSize: 10.5),
                ),
                const SizedBox(height: 5),
                Text(
                  baseUrl,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    color: palette.muted.withValues(alpha: 0.78),
                    fontSize: 9.5,
                    fontFeatures: const [FontFeature.tabularFigures()],
                  ),
                ),
              ],
            ),
          ),
          if (onReconnect != null) ...[
            const SizedBox(width: 8),
            TextButton(
              onPressed: reconnecting ? null : onReconnect,
              child: Text(reconnecting ? '搜索中…' : '自动连接'),
            ),
          ],
        ],
      ),
    );
  }
}

class _SectionTitle extends StatelessWidget {
  const _SectionTitle(this.label);

  final String label;

  @override
  Widget build(BuildContext context) {
    return Text(
      label,
      style: TextStyle(
        color: context.vigor.muted,
        fontSize: 11,
        fontWeight: FontWeight.w700,
        letterSpacing: 1.3,
      ),
    );
  }
}

class _MirrorDisplayCard extends StatelessWidget {
  const _MirrorDisplayCard({
    required this.configs,
    required this.loading,
    required this.unavailable,
    required this.leftCount,
    required this.rightCount,
    required this.onEdit,
  });

  final List<_ModuleConfig> configs;
  final bool loading;
  final bool unavailable;
  final int leftCount;
  final int rightCount;
  final VoidCallback onEdit;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    final enabledCount = configs.where((config) => config.enabled).length;
    final summary = loading
        ? '正在读取布局'
        : unavailable
        ? '布局暂不可用'
        : '$enabledCount 个模块 · 左 $leftCount / 右 $rightCount';
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: palette.surface.withValues(alpha: 0.94),
        border: Border.all(color: palette.line),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _MirrorLayoutPreview(
            configs: configs,
            loading: loading,
            unavailable: unavailable,
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: Text(
                  summary,
                  style: TextStyle(color: palette.muted, fontSize: 10.5),
                ),
              ),
              TextButton.icon(
                onPressed: loading || unavailable ? null : onEdit,
                icon: const Icon(Icons.grid_view_rounded, size: 15),
                label: const Text('编辑布局'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _MirrorLayoutPreview extends StatelessWidget {
  const _MirrorLayoutPreview({
    required this.configs,
    required this.loading,
    required this.unavailable,
  });

  final List<_ModuleConfig> configs;
  final bool loading;
  final bool unavailable;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    final left = _modulesFor('left');
    final right = _modulesFor('right');
    final empty = left.isEmpty && right.isEmpty;
    return Container(
      height: 132,
      padding: const EdgeInsets.all(8),
      decoration: BoxDecoration(
        color: const Color(0xFF070907),
        border: Border.all(color: palette.line),
        borderRadius: BorderRadius.circular(12),
      ),
      child: loading || unavailable || empty
          ? Center(
              child: Text(
                loading
                    ? '正在读取镜端布局'
                    : unavailable
                    ? '无法读取镜端布局'
                    : '镜端暂未启用模块',
                style: TextStyle(color: palette.muted, fontSize: 10),
              ),
            )
          : Row(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Expanded(child: _PreviewColumn(modules: left)),
                Container(
                  width: 1,
                  margin: const EdgeInsets.symmetric(horizontal: 6),
                  color: palette.line,
                ),
                Expanded(child: _PreviewColumn(modules: right)),
              ],
            ),
    );
  }

  List<_ModuleConfig> _modulesFor(String column) {
    final modules =
        configs
            .where((config) => config.enabled && config.column == column)
            .toList()
          ..sort((a, b) => a.order.compareTo(b.order));
    return modules;
  }
}

class _PreviewColumn extends StatelessWidget {
  const _PreviewColumn({required this.modules});

  final List<_ModuleConfig> modules;

  @override
  Widget build(BuildContext context) {
    final children = <Widget>[];
    var usedSlots = 0;
    for (var index = 0; index < modules.length; index++) {
      if (usedSlots >= 4) break;
      final module = modules[index];
      final span = module.size == '1x2' ? 2 : 1;
      final remaining = 4 - usedSlots;
      final flex = span > remaining ? remaining : span;
      if (index > 0) children.add(const SizedBox(height: 4));
      children.add(
        Expanded(
          flex: flex,
          child: _PreviewModuleTile(config: module),
        ),
      );
      usedSlots += flex;
    }
    if (usedSlots < 4) {
      children.add(Expanded(flex: 4 - usedSlots, child: const SizedBox()));
    }
    return Column(children: children);
  }
}

class _PreviewModuleTile extends StatelessWidget {
  const _PreviewModuleTile({required this.config});

  final _ModuleConfig config;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 5),
      decoration: BoxDecoration(
        color: palette.accent.withValues(alpha: 0.085),
        border: Border.all(color: palette.accent.withValues(alpha: 0.22)),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Row(
        children: [
          Icon(
            _moduleIcons[config.id] ?? Icons.widgets_outlined,
            size: 11,
            color: palette.accent,
          ),
          const SizedBox(width: 5),
          Expanded(
            child: Text(
              _moduleTitle(config.id),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                color: palette.ink,
                fontSize: 9,
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _SettingsTile extends StatelessWidget {
  const _SettingsTile({
    required this.icon,
    required this.title,
    required this.subtitle,
    this.trailing,
    this.onTap,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final Widget? trailing;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    final shape = RoundedRectangleBorder(
      borderRadius: BorderRadius.circular(14),
      side: BorderSide(color: palette.line),
    );
    return Material(
      color: palette.surface.withValues(alpha: 0.92),
      shape: shape,
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
          child: Row(
            children: [
              Container(
                width: 34,
                height: 34,
                decoration: BoxDecoration(
                  color: palette.elevated,
                  borderRadius: BorderRadius.circular(10),
                ),
                child: Icon(icon, color: palette.accent, size: 18),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      title,
                      style: const TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      subtitle,
                      style: TextStyle(color: palette.muted, fontSize: 10),
                    ),
                  ],
                ),
              ),
              if (trailing != null) ...[const SizedBox(width: 8), trailing!],
            ],
          ),
        ),
      ),
    );
  }
}

class _DeviceRow extends StatelessWidget {
  const _DeviceRow({required this.device});

  final Map<String, dynamic> device;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    final createdAt = DateTime.tryParse('${device['created_at']}')?.toLocal();
    final platform = switch ('${device['platform'] ?? ''}') {
      'android' => 'Android',
      'ios' => 'iOS',
      final value when value.isNotEmpty => value,
      _ => '未知平台',
    };
    return Container(
      margin: const EdgeInsets.only(bottom: 9),
      padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 11),
      decoration: BoxDecoration(
        color: palette.surface.withValues(alpha: 0.92),
        border: Border.all(color: palette.line),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        children: [
          Icon(Icons.smartphone, size: 17, color: palette.accent),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${device['device_name'] ?? '未知设备'}',
                  style: const TextStyle(
                    fontWeight: FontWeight.w700,
                    fontSize: 12,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  '$platform'
                  '${createdAt != null ? ' · ${createdAt.month}月${createdAt.day}日配对' : ''}',
                  style: TextStyle(color: palette.muted, fontSize: 10),
                ),
              ],
            ),
          ),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
            decoration: BoxDecoration(
              color: palette.elevated,
              borderRadius: BorderRadius.circular(99),
            ),
            child: Text(
              '已配对',
              style: TextStyle(color: palette.muted, fontSize: 9),
            ),
          ),
        ],
      ),
    );
  }
}

class _MirrorLayoutEditorPage extends StatefulWidget {
  const _MirrorLayoutEditorPage({
    required this.api,
    required this.initialConfigs,
  });

  final CoreApiClient api;
  final List<_ModuleConfig> initialConfigs;

  @override
  State<_MirrorLayoutEditorPage> createState() =>
      _MirrorLayoutEditorPageState();
}

class _MirrorLayoutEditorPageState extends State<_MirrorLayoutEditorPage> {
  late final List<_ModuleConfig> _configs = [
    for (final config in widget.initialConfigs) config.clone(),
  ];
  bool _dirty = false;
  bool _saving = false;

  Future<void> _save() async {
    setState(() => _saving = true);
    try {
      await widget.api.updateMirrorLayout([
        for (final config in _configs)
          if (config.enabled) config.toJson(),
      ]);
      if (!mounted) return;
      Navigator.pop(context, [for (final config in _configs) config.clone()]);
    } on CoreApiException catch (error) {
      _showError(error.message);
    } catch (error) {
      _showError('无法保存布局：$error');
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
                      child: Text('编辑镜端布局', style: theme.textTheme.titleLarge),
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
                    _MirrorLayoutPreview(
                      configs: _configs,
                      loading: false,
                      unavailable: false,
                    ),
                    const SizedBox(height: 16),
                    Text(
                      '选择镜端初始页展示的模块、卡片大小和所在列。保存后镜端会实时刷新。',
                      style: TextStyle(
                        color: palette.muted,
                        fontSize: 12,
                        height: 1.55,
                      ),
                    ),
                    const SizedBox(height: 16),
                    for (final entry in _moduleCatalog)
                      _ModuleEditor(
                        title: entry.$2,
                        config: _configs.firstWhere(
                          (config) => config.id == entry.$1,
                        ),
                        onChanged: () => setState(() => _dirty = true),
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
                      : const Icon(Icons.sync, size: 17),
                  label: Text(
                    _saving
                        ? '同步中…'
                        : _dirty
                        ? '保存并同步到镜端'
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

class _ModuleEditor extends StatelessWidget {
  const _ModuleEditor({
    required this.title,
    required this.config,
    required this.onChanged,
  });

  final String title;
  final _ModuleConfig config;
  final VoidCallback onChanged;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: palette.surface.withValues(alpha: 0.92),
        border: Border.all(
          color: config.enabled
              ? palette.accent.withValues(alpha: 0.4)
              : palette.line,
        ),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Column(
        children: [
          Row(
            children: [
              Icon(
                _moduleIcons[config.id] ?? Icons.widgets_outlined,
                color: config.enabled ? palette.accent : palette.muted,
                size: 17,
              ),
              const SizedBox(width: 9),
              Expanded(
                child: Text(
                  title,
                  style: const TextStyle(
                    fontWeight: FontWeight.w700,
                    fontSize: 13,
                  ),
                ),
              ),
              Switch(
                value: config.enabled,
                onChanged: (value) {
                  config.enabled = value;
                  onChanged();
                },
              ),
            ],
          ),
          if (config.enabled) ...[
            const SizedBox(height: 6),
            _OptionRow(
              label: '卡片大小',
              options: const [('1x1', '标准卡'), ('1x2', '长卡')],
              value: config.size,
              onSelected: (value) {
                config.size = value;
                onChanged();
              },
            ),
            const SizedBox(height: 8),
            _OptionRow(
              label: '所在位置',
              options: const [('left', '左列'), ('right', '右列')],
              value: config.column,
              onSelected: (value) {
                config.column = value;
                onChanged();
              },
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Text(
                  '排序',
                  style: TextStyle(color: palette.muted, fontSize: 11),
                ),
                const Spacer(),
                IconButton(
                  onPressed: config.order > 0
                      ? () {
                          config.order -= 1;
                          onChanged();
                        }
                      : null,
                  icon: const Icon(Icons.keyboard_arrow_up_rounded, size: 18),
                  visualDensity: VisualDensity.compact,
                  tooltip: '上移',
                ),
                Text(
                  '第 ${config.order + 1} 位',
                  style: const TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                IconButton(
                  onPressed: config.order < 3
                      ? () {
                          config.order += 1;
                          onChanged();
                        }
                      : null,
                  icon: const Icon(Icons.keyboard_arrow_down_rounded, size: 18),
                  visualDensity: VisualDensity.compact,
                  tooltip: '下移',
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

class _OptionRow extends StatelessWidget {
  const _OptionRow({
    required this.label,
    required this.options,
    required this.value,
    required this.onSelected,
  });

  final String label;
  final List<(String, String)> options;
  final String value;
  final ValueChanged<String> onSelected;

  @override
  Widget build(BuildContext context) {
    final palette = context.vigor;
    return Row(
      children: [
        Text(label, style: TextStyle(color: palette.muted, fontSize: 11)),
        const Spacer(),
        for (final option in options)
          Padding(
            padding: const EdgeInsets.only(left: 6),
            child: ChoiceChip(
              label: Text(option.$2),
              selected: value == option.$1,
              onSelected: (_) => onSelected(option.$1),
              labelStyle: const TextStyle(fontSize: 10.5),
              visualDensity: VisualDensity.compact,
            ),
          ),
      ],
    );
  }
}
