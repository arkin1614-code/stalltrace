# 在 Flutter Android 应用中使用 Stalltrace

运行示例后，打开 **Collect foreground stalls** 开始采集。页面会显示当前设备支持的采样能力。**Local reports** 列出应用私有目录中已有的卡顿和进程退出记录，点刷新可以重新读取。首次安装时列表为空。

在仓库根目录执行以下命令，并选择 Android 设备。

```sh
cd example
flutter pub get
flutter run
```

## 在自己的应用中接入

示例通过 path 依赖引用仓库里的插件。复制到其他工程时，把路径改为该工程到 Stalltrace 的相对路径。

```yaml
dependencies:
  stalltrace:
    path: ../
```

在用户开启诊断后，从 Flutter 根 isolate 调用 `start()`。在已经显示的 Flutter 页面中调用时，Flutter binding 已完成初始化。

```dart
import 'package:stalltrace/stalltrace.dart';

Future<StalltraceSession> enableDiagnostics() => Stalltrace.start();
Future<void> disableDiagnostics() => Stalltrace.stop();
```

`session.nativeSamplingAvailable` 表示这个进程能否采集原生栈。值为 `false` 时仍可采集 Java 栈。关闭开关会暂停采样，以前保存的报告仍可读取。

```dart
Future<List<StalltraceReport>> listInputAnrs() async {
  final reports = await Stalltrace.listReports();
  return reports
      .where((r) => r.traceAssociation == StalltraceTraceAssociation.matched)
      .toList();
}
```

`stall` 表示观测到前台主线程卡顿，`processExit` 是 Android 的历史退出记录。历史记录在启动采集后异步导入，稍后刷新可看到新增报告。匹配成功时，`exitReportId` 指向带系统 trace 的退出报告。需要响应用户的删除操作时，可以调用 `Stalltrace.deleteReport(report.id)`。示例仅在本机查看诊断。启停、能力展示和报告读取的完整调用见 [lib/main.dart](lib/main.dart)。

要复现 ANR、导出 trace 或校验混淆 release 栈，请使用[真机验收工程](../tool/anr_lab/README.md)。
