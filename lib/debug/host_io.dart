import 'dart:io';
import 'dart:typed_data';

Map<String, String> hostOptions() => Platform.environment;

void saveFrame(String path, Uint8List bytes) =>
    File(path).writeAsBytesSync(bytes);

void quit() => exit(0);
