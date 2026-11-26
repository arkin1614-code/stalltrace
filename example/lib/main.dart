import 'package:flutter/material.dart';
import 'package:stalltrace/stalltrace.dart';

import 'anr_scenarios.dart';

void main() => runApp(const MaterialApp(home: DiagnosticsPage()));

class DiagnosticsPage extends StatefulWidget {
  const DiagnosticsPage({super.key});

  @override
  State<DiagnosticsPage> createState() => _DiagnosticsPageState();
}

class _DiagnosticsPageState extends State<DiagnosticsPage> {
  StalltraceSession? _session;
  List<StalltraceReport>? _reports;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _setEnabled(bool enabled) => _perform(() async {
    if (enabled) {
      _session = await Stalltrace.start();
    } else {
      await Stalltrace.stop();
      _session = null;
    }
    _reports = await Stalltrace.listReports();
  });

  Future<void> _refresh() => _perform(() async {
    _reports = await Stalltrace.listReports();
  });

  Future<void> _perform(Future<void> Function() action) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await action();
    } catch (error) {
      _error = error.toString();
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final reports = _reports;
    return Scaffold(
      appBar: AppBar(title: const Text('Stalltrace example')),
      body: ListView(
        children: [
          SwitchListTile(
            title: const Text('Collect foreground stalls'),
            subtitle: Text(switch (_session?.nativeSamplingAvailable) {
              true => 'Collecting Java and native stacks',
              false => 'Collecting Java stacks only',
              null => 'Collection is off',
            }),
            value: _session != null,
            onChanged: _busy ? null : _setEnabled,
          ),
          ListTile(
            title: const Text('ANR test scenarios'),
            subtitle: _session == null
                ? const Text('Enable collection to run a scenario.')
                : null,
            trailing: const Icon(Icons.chevron_right),
            enabled: _session != null && !_busy,
            onTap: _session == null || _busy
                ? null
                : () async {
                    await Navigator.of(context).push<void>(
                      MaterialPageRoute(
                        builder: (_) => const AnrScenariosPage(),
                      ),
                    );
                    if (mounted) await _refresh();
                  },
          ),
          const Divider(),
          ListTile(
            title: const Text('Local reports'),
            trailing: IconButton(
              tooltip: 'Refresh reports',
              onPressed: _busy ? null : _refresh,
              icon: const Icon(Icons.refresh),
            ),
          ),
          if (_error != null) ListTile(title: Text(_error!)),
          if (reports != null && reports.isEmpty && _error == null)
            const ListTile(title: Text('No saved reports')),
          for (final report in reports ?? <StalltraceReport>[])
            ListTile(
              title: Text(switch (report.kind) {
                StalltraceReportKind.stall =>
                  report.traceAssociation == StalltraceTraceAssociation.matched
                      ? 'Stall with Android input ANR trace'
                      : 'Foreground stall',
                StalltraceReportKind.processExit =>
                  report.confirmedAnrExit ? 'ANR exit' : 'Process exit',
              }),
              subtitle: Text(report.id),
            ),
        ],
      ),
    );
  }
}
