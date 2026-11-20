import 'dart:async';
import 'dart:convert';
import 'dart:io';

const vigorsyncDiscoveryPort = 8765;
const vigorsyncDiscoveryProbe = 'VIGORSYNC_DISCOVER/1';

class DiscoveredCore {
  const DiscoveredCore({
    required this.coreUrl,
    required this.deviceName,
    required this.hostname,
  });

  final String coreUrl;
  final String deviceName;
  final String hostname;
}

DiscoveredCore? parseDiscoveryResponse(String raw) {
  try {
    final decoded = jsonDecode(raw);
    if (decoded is! Map<String, dynamic>) return null;
    if (decoded['service'] != 'vigorsync-core') return null;
    final coreUrl = decoded['core_url'];
    if (coreUrl is! String || coreUrl.trim().isEmpty) return null;
    final uri = Uri.tryParse(coreUrl);
    if (uri == null ||
        (uri.scheme != 'http' && uri.scheme != 'https') ||
        uri.host.isEmpty) {
      return null;
    }
    return DiscoveredCore(
      coreUrl: coreUrl.replaceFirst(RegExp(r'/$'), ''),
      deviceName: '${decoded['device_name'] ?? 'VigorSync'}',
      hostname: '${decoded['hostname'] ?? ''}',
    );
  } catch (_) {
    return null;
  }
}

class LanDiscoveryService {
  const LanDiscoveryService({this.port = vigorsyncDiscoveryPort});

  final int port;

  Future<List<DiscoveredCore>> discover({
    Duration timeout = const Duration(seconds: 3),
  }) async {
    RawDatagramSocket? socket;
    StreamSubscription<RawSocketEvent>? subscription;
    Timer? repeatTimer;
    Timer? timeoutTimer;
    try {
      socket = await RawDatagramSocket.bind(InternetAddress.anyIPv4, 0);
      socket.broadcastEnabled = true;
      final results = <String, DiscoveredCore>{};
      final completer = Completer<List<DiscoveredCore>>();
      final probe = utf8.encode(vigorsyncDiscoveryProbe);
      final broadcast = InternetAddress('255.255.255.255');

      void sendProbe() {
        final current = socket;
        if (current == null) return;
        try {
          current.send(probe, broadcast, port);
        } catch (_) {
          // A network without broadcast support may still answer direct probes.
        }
      }

      subscription = socket.listen((event) {
        if (event != RawSocketEvent.read) return;
        final datagram = socket?.receive();
        if (datagram == null) return;
        final discovered = parseDiscoveryResponse(utf8.decode(datagram.data));
        if (discovered != null) {
          results[discovered.coreUrl] = discovered;
        }
      });

      sendProbe();
      repeatTimer = Timer.periodic(
        const Duration(milliseconds: 600),
        (_) => sendProbe(),
      );
      timeoutTimer = Timer(timeout, () {
        if (!completer.isCompleted) {
          completer.complete(results.values.toList());
        }
      });
      return await completer.future;
    } catch (_) {
      return const [];
    } finally {
      repeatTimer?.cancel();
      timeoutTimer?.cancel();
      await subscription?.cancel();
      socket?.close();
    }
  }
}
