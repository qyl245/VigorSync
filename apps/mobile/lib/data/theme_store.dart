import 'package:flutter_secure_storage/flutter_secure_storage.dart';

class ThemeStore {
  ThemeStore({FlutterSecureStorage? storage})
    : _storage = storage ?? const FlutterSecureStorage();

  static const _key = 'vigorsync.theme_mode';

  final FlutterSecureStorage _storage;

  Future<bool> readDarkMode() async {
    return await _storage.read(key: _key) != 'light';
  }

  Future<void> saveDarkMode(bool darkMode) async {
    await _storage.write(key: _key, value: darkMode ? 'dark' : 'light');
  }
}
