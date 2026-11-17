import 'dart:convert';
import 'dart:io';
import 'dart:math';

import '../models/session.dart';

class CoreApiException implements Exception {
  const CoreApiException(this.message, {this.code, this.statusCode});

  final String message;
  final String? code;
  final int? statusCode;

  @override
  String toString() => message;
}

class CoreApiClient {
  CoreApiClient(this.credentials)
    : _client = HttpClient()..connectionTimeout = const Duration(seconds: 4);

  final CoreCredentials credentials;
  final HttpClient _client;

  String get baseUrl => credentials.baseUrl.replaceFirst(RegExp(r'/$'), '');

  Future<Map<String, dynamic>> _request(
    String method,
    String path, {
    Map<String, dynamic>? body,
    bool authenticated = true,
    String? idempotencyKey,
  }) async {
    final request = await _client.openUrl(method, Uri.parse('$baseUrl$path'));
    request.headers.contentType = ContentType.json;
    if (authenticated) {
      request.headers.set(
        HttpHeaders.authorizationHeader,
        'Bearer ${credentials.token}',
      );
    }
    if (idempotencyKey != null) {
      request.headers.set('Idempotency-Key', idempotencyKey);
    }
    if (body != null) {
      request.write(jsonEncode(body));
    }
    final response = await request.close();
    final text = await utf8.decoder.bind(response).join();
    final decoded = text.isEmpty ? <String, dynamic>{} : jsonDecode(text);
    if (response.statusCode < 200 || response.statusCode >= 300) {
      var message = '请求失败 (${response.statusCode})';
      String? code;
      if (decoded is Map<String, dynamic>) {
        final error = decoded['error'];
        if (error is Map<String, dynamic>) {
          message = error['message']?.toString() ?? message;
          code = error['code']?.toString();
        } else if (decoded['detail'] != null) {
          message = decoded['detail'].toString();
          code = decoded['detail'].toString();
        }
      }
      throw CoreApiException(
        message,
        code: code,
        statusCode: response.statusCode,
      );
    }
    return decoded is Map<String, dynamic> ? decoded : <String, dynamic>{};
  }

  Future<String> health() async {
    final payload = await _request(
      'GET',
      '/api/v1/health',
      authenticated: false,
    );
    return payload['live_engine'] as String? ?? 'unknown';
  }

  static Future<CoreCredentials> claimPairing({
    required String baseUrl,
    required String pairingCode,
    required String deviceName,
  }) async {
    final normalized = baseUrl.trim().replaceFirst(RegExp(r'/$'), '');
    final probe = CoreApiClient(
      CoreCredentials(baseUrl: normalized, token: ''),
    );
    try {
      final payload = await probe._request(
        'POST',
        '/api/v1/pairing/claim',
        authenticated: false,
        body: {
          'pairing_code': pairingCode.trim(),
          'device_name': deviceName.trim(),
          'platform': 'android',
        },
      );
      return CoreCredentials(
        baseUrl: normalized,
        token: payload['token'] as String,
      );
    } finally {
      probe.close();
    }
  }

  Future<List<SessionRecord>> listSessions() async {
    final payload = await _request('GET', '/api/v1/sessions?limit=50');
    final items = payload['items'] as List<dynamic>? ?? const [];
    return items
        .whereType<Map<String, dynamic>>()
        .map(SessionRecord.fromJson)
        .toList();
  }

  Future<void> updateSessionConfig({
    required String exercise,
    required String view,
    required int targetReps,
  }) async {
    await _request(
      'POST',
      '/api/v1/control/config',
      body: {
        'exercise': exercise,
        'view': view,
        'target_reps': targetReps,
      },
    );
  }

  Future<SessionRecord> createSession({
    required String exercise,
    required String view,
    required int targetReps,
  }) async {
    final payload = await _request(
      'POST',
      '/api/v1/sessions',
      idempotencyKey: _newIdempotencyKey(),
      body: {
        'exercise': exercise,
        'view': view,
        'target_reps': targetReps,
        'notes': null,
      },
    );
    return SessionRecord.fromJson(payload);
  }

  Future<SessionRecord> startNewSession({
    required String exercise,
    required String view,
    required int targetReps,
  }) async {
    final payload = await _request(
      'POST',
      '/api/v1/sessions/start',
      idempotencyKey: _newIdempotencyKey(),
      body: {
        'exercise': exercise,
        'view': view,
        'target_reps': targetReps,
      },
    );
    return SessionRecord.fromJson(payload);
  }

  Future<SessionRecord> startSession(String sessionId) async {
    final payload = await _request(
      'POST',
      '/api/v1/sessions/$sessionId/start',
      idempotencyKey: _newIdempotencyKey(),
    );
    return SessionRecord.fromJson(payload);
  }

  Future<SessionRecord> pauseSession(String sessionId) async {
    final payload = await _request(
      'POST',
      '/api/v1/sessions/$sessionId/pause',
      idempotencyKey: _newIdempotencyKey(),
    );
    return SessionRecord.fromJson(payload);
  }

  Future<SessionRecord> resumeSession(String sessionId) async {
    final payload = await _request(
      'POST',
      '/api/v1/sessions/$sessionId/resume',
      idempotencyKey: _newIdempotencyKey(),
    );
    return SessionRecord.fromJson(payload);
  }

  Future<SessionRecord> stopSession(String sessionId) async {
    final payload = await _request(
      'POST',
      '/api/v1/sessions/$sessionId/stop',
      idempotencyKey: _newIdempotencyKey(),
    );
    return SessionRecord.fromJson(payload);
  }

  Future<void> dismissMirrorSession(String sessionId) async {
    await _request(
      'POST',
      '/api/v1/sessions/$sessionId/mirror/dismiss',
    );
  }

  Future<void> deleteSession(String sessionId) async {
    await _request('DELETE', '/api/v1/sessions/$sessionId');
  }

  Future<WebSocket> connectClientSocket() async {
    final wsBase = baseUrl.replaceFirst(RegExp(r'^http'), 'ws');
    final socket = await WebSocket.connect(
      '$wsBase/api/v1/ws/client',
      headers: {
        HttpHeaders.authorizationHeader: 'Bearer ${credentials.token}',
      },
    ).timeout(const Duration(seconds: 5));
    return socket;
  }

  void close() {
    _client.close(force: true);
  }

  static String _newIdempotencyKey() {
    final random = Random.secure();
    final suffix = List.generate(
      8,
      (_) => random.nextInt(256),
    ).map((value) => value.toRadixString(16).padLeft(2, '0')).join();
    return '${DateTime.now().microsecondsSinceEpoch}-$suffix';
  }
}
