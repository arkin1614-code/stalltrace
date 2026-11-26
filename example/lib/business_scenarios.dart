import 'dart:convert';
import 'dart:io';
import 'dart:isolate';

import 'package:flutter/services.dart';

const businessLabels = [
  'Prepare cache',
  'SQLite UI',
  'SQLite worker',
  'Monitor UI',
  'Monitor worker',
  'JSON UI',
  'JSON isolate',
];

// Synthetic catalog pages have a fixed schema, size and checksum on both paths.
final String _stockPage = jsonEncode([
  for (var i = 0; i < 256; i++)
    {'sku': 'item-$i', 'stock': i % 31, 'price': 200 + i},
]);

int decodeStockPage(String page) {
  final items = jsonDecode(page) as List<dynamic>;
  var total = 0;
  for (final item in items) {
    total += (item['stock'] as int) * (item['price'] as int);
  }
  return total;
}

int restoreInventoryCache(int pages) {
  var checksum = 0;
  for (var i = 0; i < pages; i++) {
    checksum += decodeStockPage(_stockPage);
  }
  return checksum;
}

int _sizeCache() {
  restoreInventoryCache(20);
  final timer = Stopwatch()..start();
  var pages = 0;
  do {
    restoreInventoryCache(10);
    pages += 10;
  } while (timer.elapsedMilliseconds < 1000);
  return (pages * 20000 / timer.elapsedMilliseconds).round().clamp(1, 2000000);
}

Future<String> runBusinessScenario(String name, MethodChannel channel) async {
  final countFile = File(
    '${Directory.systemTemp.path}/stalltrace-scenario-pages',
  );
  if (name == 'Prepare cache') {
    final count = await Isolate.run(_sizeCache);
    await countFile.writeAsString('$count', flush: true);
    return 'pages=$count';
  }
  if (name.startsWith('JSON ')) {
    final pages = int.parse(await countFile.readAsString());
    await channel.invokeMethod<void>('mark', 'work $name pages=$pages');
    final checksum = name == 'JSON UI'
        ? restoreInventoryCache(pages)
        : await Isolate.run(() => restoreInventoryCache(pages));
    return 'pages=$pages checksum=$checksum';
  }
  final value = await channel.invokeMethod<int>('inventoryEdit', {
    'database': name.startsWith('SQLite '),
    'onMain': name.endsWith(' UI'),
  });
  return 'saved=$value';
}
