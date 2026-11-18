import 'dart:async';
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

bool shouldReturnToPairing(Object error) {
  return error is SocketException ||
      error is TimeoutException ||
      error is HttpException ||
      error is HandshakeException ||
      error is WebSocketException ||
      (error is CoreApiException &&
          (error.statusCode == 401 || error.statusCode == 403));
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
      body: {'exercise': exercise, 'view': view, 'target_reps': targetReps},
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
      body: {'exercise': exercise, 'view': view, 'target_reps': targetReps},
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

  Future<SessionRecord> cancelSession(String sessionId) async {
    final payload = await _request(
      'POST',
      '/api/v1/sessions/$sessionId/cancel',
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

  Future<Map<String, dynamic>> getReport(String sessionId) async {
    return _request('GET', '/api/v1/sessions/$sessionId/report');
  }

  String resolveUrl(String value) {
    final uri = Uri.parse(value);
    if (uri.hasScheme) return value;
    return Uri.parse('$baseUrl/')
        .resolve(value.replaceFirst(RegExp(r'^/'), ''))
        .toString();
  }

  Future<void> dismissMirrorSession(String sessionId) async {
    await _request('POST', '/api/v1/sessions/$sessionId/mirror/dismiss');
  }

  Future<void> deleteSession(String sessionId) async {
    await _request('DELETE', '/api/v1/sessions/$sessionId');
  }

  Future<WebSocket> connectClientSocket() async {
    final wsBase = baseUrl.replaceFirst(RegExp(r'^http'), 'ws');
    final socket = await WebSocket.connect(
      '$wsBase/api/v1/ws/client',
      headers: {HttpHeaders.authorizationHeader: 'Bearer ${credentials.token}'},
    ).timeout(const Duration(seconds: 5));
    return socket;
  }

  Future<Map<String, dynamic>> getWeather() async {
    return _request('GET', '/api/v1/weather');
  }

  Future<List<Map<String, dynamic>>> getTrend({int days = 7}) async {
    final payload = await _request('GET', '/api/v1/stats/trend?days=$days');
    final items = payload['days'] as List<dynamic>? ?? const [];
    return items.whereType<Map<String, dynamic>>().toList();
  }

  Future<Map<String, dynamic>> getMirrorLayout() async {
    return _request('GET', '/api/v1/mirror/layout');
  }

  Future<Map<String, dynamic>> updateMirrorLayout(
    List<Map<String, dynamic>> modules,
  ) async {
    return _request('PUT', '/api/v1/mirror/layout', body: {'modules': modules});
  }

  Future<void> updateMirrorFocus(String page) async {
    await _request('POST', '/api/v1/control/focus', body: {'page': page});
  }

  Future<void> updateMirrorTheme(String mode) async {
    await _request('POST', '/api/v1/control/theme', body: {'mode': mode});
  }

  Future<List<Map<String, dynamic>>> listPairedDevices() async {
    final payload = await _request('GET', '/api/v1/pairing/devices');
    final items = payload['items'] as List<dynamic>? ?? const [];
    return items.whereType<Map<String, dynamic>>().toList();
  }

  Future<Map<String, dynamic>> getPairingIdentity() async {
    return _request('GET', '/api/v1/pairing/me');
  }

  Future<void> revokeCurrentPairing() async {
    await _request('DELETE', '/api/v1/pairing/current');
  }

  Future<Map<String, dynamic>> getTodos({String? date}) async {
    final query = date == null ? '' : '?date=${Uri.encodeQueryComponent(date)}';
    return _request('GET', '/api/v1/todos$query');
  }

  Future<Map<String, dynamic>> createTodo(String title) async {
    return _request('POST', '/api/v1/todos', body: {'title': title});
  }

  Future<Map<String, dynamic>> updateTodo(
    String id, {
    String? title,
    bool? completed,
    int? sortOrder,
  }) async {
    final body = <String, dynamic>{};
    if (title != null) body['title'] = title;
    if (completed != null) body['completed'] = completed;
    if (sortOrder != null) body['sort_order'] = sortOrder;
    return _request(
      'PATCH',
      '/api/v1/todos/${Uri.encodeComponent(id)}',
      body: body,
    );
  }

  Future<void> deleteTodo(String id) async {
    await _request('DELETE', '/api/v1/todos/${Uri.encodeComponent(id)}');
  }

  Future<List<Map<String, dynamic>>> getWeeklyPlans() async {
    final payload = await _request('GET', '/api/v1/weekly-plans');
    final items = payload['items'] as List<dynamic>? ?? const [];
    return items.whereType<Map<String, dynamic>>().toList();
  }

  Future<List<Map<String, dynamic>>> replaceWeeklyPlans(
    List<Map<String, dynamic>> items,
  ) async {
    final payload = await _request(
      'PUT',
      '/api/v1/weekly-plans',
      body: {'items': items},
    );
    final result = payload['items'] as List<dynamic>? ?? const [];
    return result.whereType<Map<String, dynamic>>().toList();
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
