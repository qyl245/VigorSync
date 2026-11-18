import 'dart:async';

import '../models/session.dart';
import 'core_api_client.dart';
import 'lan_discovery.dart';

class CoreRecoveryResult {
  const CoreRecoveryResult({
    required this.credentials,
    required this.deviceName,
    required this.discovered,
  });

  final CoreCredentials credentials;
  final String deviceName;
  final bool discovered;
}

class CoreRediscoveryService {
  const CoreRediscoveryService({
    this.discovery = const LanDiscoveryService(),
    this.probeTimeout = const Duration(seconds: 3),
  });

  final LanDiscoveryService discovery;
  final Duration probeTimeout;

  Future<CoreRecoveryResult?> recover(
    CoreCredentials current, {
    Duration discoveryTimeout = const Duration(seconds: 2),
  }) async {
    final direct = await _probe(current);
    if (direct != null) {
      return CoreRecoveryResult(
        credentials: current,
        deviceName: '${direct['device_name'] ?? 'VigorSync'}',
        discovered: false,
      );
    }

    final candidates = await discovery.discover(timeout: discoveryTimeout);
    for (final candidate in candidates) {
      if (candidate.coreUrl == current.baseUrl) continue;
      final credentials = CoreCredentials(
        baseUrl: candidate.coreUrl,
        token: current.token,
      );
      final identity = await _probe(credentials);
      if (identity == null) continue;
      return CoreRecoveryResult(
        credentials: credentials,
        deviceName: candidate.deviceName,
        discovered: true,
      );
    }
    return null;
  }

  Future<Map<String, dynamic>?> _probe(CoreCredentials credentials) async {
    final api = CoreApiClient(credentials);
    try {
      return await api.getPairingIdentity().timeout(probeTimeout);
    } catch (_) {
      return null;
    } finally {
      api.close();
    }
  }
}
