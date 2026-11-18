import 'dart:io';

import 'package:material_ui/material_ui.dart';

import '../../data/core_api_client.dart';
import '../../models/session.dart';
import '../../theme/vigorsync_theme.dart';

class PairingPage extends StatefulWidget {
  const PairingPage({
    super.key,
    required this.onPaired,
    required this.isDarkMode,
    required this.onToggleTheme,
    this.initialMessage,
  });

  final Future<void> Function(CoreCredentials credentials) onPaired;
  final bool isDarkMode;
  final VoidCallback onToggleTheme;
  final String? initialMessage;

  @override
  State<PairingPage> createState() => _PairingPageState();
}

class _PairingPageState extends State<PairingPage> {
  final _formKey = GlobalKey<FormState>();
  late final TextEditingController _urlController;
  late final TextEditingController _codeController;
  late final TextEditingController _deviceController;
  bool _loading = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _urlController = TextEditingController(text: 'http://10.0.2.2:8011');
    _codeController = TextEditingController();
    _deviceController = TextEditingController(text: 'VigorSync Android');
    _error = widget.initialMessage;
  }

  @override
  void dispose() {
    _urlController.dispose();
    _codeController.dispose();
    _deviceController.dispose();
    super.dispose();
  }

  Future<void> _pair() async {
    if (!_formKey.currentState!.validate()) {
      return;
    }
    FocusScope.of(context).unfocus();
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final credentials = await CoreApiClient.claimPairing(
        baseUrl: _urlController.text,
        pairingCode: _codeController.text,
        deviceName: _deviceController.text,
      );
      await widget.onPaired(credentials);
    } on SocketException {
      setState(() => _error = '无法连接 Core API，请检查地址和局域网连接。');
    } on CoreApiException catch (error) {
      setState(() => _error = error.message);
    } catch (error) {
      setState(() => _error = '$error');
    } finally {
      if (mounted) {
        setState(() => _loading = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final palette = context.vigor;
    return Scaffold(
      body: VigorPage(
        child: SafeArea(
          child: SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(24, 30, 24, 36),
            child: Form(
              key: _formKey,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Row(
                    children: [
                      const BrandMark(),
                      const SizedBox(width: 12),
                      const Text(
                        'VIGORSYNC',
                        style: TextStyle(
                          fontWeight: FontWeight.w800,
                          letterSpacing: 2.2,
                          fontSize: 12,
                        ),
                      ),
                      const Spacer(),
                      ThemeModeButton(
                        isDarkMode: widget.isDarkMode,
                        onPressed: widget.onToggleTheme,
                      ),
                    ],
                  ),
                  const SizedBox(height: 54),
                  Text('连接你的\n训练空间', style: theme.textTheme.displayLarge),
                  const SizedBox(height: 18),
                  const Text(
                    '输入镜端显示的配对码，或填写 Core API 地址完成本地连接。',
                    style: TextStyle(height: 1.6),
                  ),
                  const SizedBox(height: 34),
                  _FieldLabel('CORE ADDRESS'),
                  const SizedBox(height: 8),
                  TextFormField(
                    controller: _urlController,
                    keyboardType: TextInputType.url,
                    decoration: const InputDecoration(
                      hintText: 'http://192.168.1.20:8011',
                    ),
                    validator: (value) {
                      final uri = Uri.tryParse(value?.trim() ?? '');
                      if (uri == null || !uri.hasScheme || uri.host.isEmpty) {
                        return '请输入完整的 Core API 地址';
                      }
                      return null;
                    },
                  ),
                  const SizedBox(height: 18),
                  _FieldLabel('PAIRING CODE'),
                  const SizedBox(height: 8),
                  TextFormField(
                    controller: _codeController,
                    keyboardType: TextInputType.number,
                    maxLength: 6,
                    style: const TextStyle(
                      fontSize: 24,
                      letterSpacing: 8,
                      fontWeight: FontWeight.w700,
                    ),
                    decoration: const InputDecoration(
                      counterText: '',
                      hintText: '000000',
                    ),
                    validator: (value) =>
                        (value?.trim().length ?? 0) < 4 ? '配对码过短' : null,
                  ),
                  const SizedBox(height: 18),
                  TextFormField(
                    controller: _deviceController,
                    decoration: const InputDecoration(labelText: '设备名称'),
                    validator: (value) =>
                        (value?.trim().isEmpty ?? true) ? '请输入设备名称' : null,
                  ),
                  const SizedBox(height: 24),
                  FilledButton(
                    onPressed: _loading ? null : _pair,
                    child: _loading
                        ? SizedBox(
                            width: 22,
                            height: 22,
                            child: CircularProgressIndicator(
                              strokeWidth: 2,
                              color: palette.buttonInk,
                            ),
                          )
                        : const Text('连接 VigorSync  →'),
                  ),
                  if (_error != null) ...[
                    const SizedBox(height: 16),
                    Text(_error!, style: TextStyle(color: palette.signal)),
                  ],
                  const SizedBox(height: 34),
                  Text(
                    '局域网模式 · 训练视频与姿态数据保留在镜端',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: palette.muted, fontSize: 11),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _FieldLabel extends StatelessWidget {
  const _FieldLabel(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Text(
      text,
      style: Theme.of(context).textTheme.labelSmall
          ?.copyWith(color: context.vigor.accent, letterSpacing: 1.4),
    );
  }
}
