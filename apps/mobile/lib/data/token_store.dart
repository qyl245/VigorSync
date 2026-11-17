import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../models/session.dart';

class TokenStore {
  TokenStore({FlutterSecureStorage? storage})
    : _storage = storage ?? const FlutterSecureStorage();

  static const _baseUrlKey = 'vigorsync.core_url';
  static const _tokenKey = 'vigorsync.pairing_token';

  final FlutterSecureStorage _storage;

  Future<CoreCredentials?> read() async {
    final values = await _storage.readAll();
    final baseUrl = values[_baseUrlKey];
    final token = values[_tokenKey];
    if (baseUrl == null || token == null) {
      return null;
    }
    return CoreCredentials(baseUrl: baseUrl, token: token);
  }

  Future<void> save(CoreCredentials credentials) async {
    await _storage.write(key: _baseUrlKey, value: credentials.baseUrl);
    await _storage.write(key: _tokenKey, value: credentials.token);
  }

  Future<void> clear() async {
    await _storage.delete(key: _baseUrlKey);
    await _storage.delete(key: _tokenKey);
  }
}
