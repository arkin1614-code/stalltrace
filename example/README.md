# 在 Flutter Android 应用中使用 Stalltrace

运行示例后，打开 **Collect foreground stalls** 开始采集。页面会显示当前设备支持的采样能力。**Local reports** 列出应用私有目录中已有的卡顿和进程退出记录，点刷新可以重新读取。首次安装时列表为空。

在仓库根目录执行以下命令，并选择 Android 设备。

```sh
cd example
flutter pub get
flutter run
```

## 手动触发一次输入 ANR

在 `example/` 下构建，并安装到 `ANDROID_SERIAL` 指定的 ARM64 Android 测试机，打开 **Stalltrace example**。若要离线还原混淆后的 Dart 行号，保留这次构建生成的符号。

```sh
flutter build apk --release --obfuscate --target-platform android-arm64 --split-debug-info=build/manual-symbols
adb -s "$ANDROID_SERIAL" install -r build/app/outputs/flutter-apk/app-release.apk
```

1. 打开 **Collect foreground stalls**，进入 **ANR test scenarios**。
2. 选择一个场景。页面提示 **Tap here once during the freeze** 时，在示例窗口再点一下，保持应用在前台。每个场景会阻塞约 18 秒；第二次触摸让 Android 有一个等待处理的输入事件。
3. 如果应用恢复，点 **Back to reports**。若系统关闭了应用，重新打开示例、开启采集，稍后刷新 **Local reports**。

| 按钮 | 阻塞操作 | 排查时可找的调用 |
| --- | --- | --- |
| Dart JSON decoding | 在根 isolate 反复同步解析商品列表 | `decodeCatalogOnUiIsolate`、Dart JSON 解析栈 |
| Native FFI wait | 从 Dart 同步调用 Android libc 的 `poll` | `waitInNativePoll` 及 libc 等待栈 |
| Java lock contention | Android 主线程等待工作线程持有的 Java 锁 | `MainActivity.waitForWorker`；系统 trace 可记录持锁线程 `example-lock-owner` |

**Foreground stall** 表示采到卡顿现场，**Stall with Android input ANR trace** 表示已关联到系统输入超时记录。系统是否弹窗、何时提供 trace 会因设备而异。首页展示报告摘要，调用栈名称用于对照符号化结果。

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

要批量复现、导出 trace 或校验混淆 release 栈，请使用[真机验收工程](../tool/anr_lab/README.md)。
