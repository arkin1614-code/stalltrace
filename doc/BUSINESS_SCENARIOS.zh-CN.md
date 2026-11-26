# 从一次输入 ANR 找到业务代码

在同一个混淆 ARM64 release APK 上，一加 PGP110 真机（Android 15）和 Android 17 ARM64 模拟器各跑了三轮。两端分别触发 9 次系统输入 ANR，18 次现场采样都定位到了预设业务函数。把相同工作移到后台线程或 isolate 后，两端共 18 次对照都在工作结束前响应了触摸，对应工作区间内的系统输入 ANR 和 Stalltrace 卡顿报告均为 0。

| 每个场景共 6 次 | Stalltrace 现场业务帧 | Android 主 dump 独立证据 |
| --- | --- | --- |
| 库存同步事务占用 SQLite 写连接，UI 保存库存 | 6/6 为 `InventoryStore.saveStockQuantity` 第 73 行 | 6/6 同一业务行，主线程位于 `SQLiteConnectionPool.waitForConnection`。栈只能识别等待方，无法从这里推出谁占用数据库连接 |
| UI 更新价格，后台同步持有同一 Java monitor | 6/6 为 `InventoryStore.applyPriceUpdate` 第 67 行 | 6/6 找到等待方、持锁的 `inventory-sync` 线程和 `syncInventory` 调用。ART 指出的持锁线程列出同一把锁，其 Linux TID 与用例独立记录一致 |
| Dart 根 isolate 同步解析、汇总库存缓存 | 6/6 为 `decodeStockPage` 第 24 行，调用方为 `restoreInventoryCache` 第 35 行 | 4/6 落在 JSON 解码第 24 行，1/6 在同一函数第 27 行做汇总，1/6 栈止于信号相关的 libc 调用和未知地址 |

一加的 9 次阳性都被系统以 ANR 原因终止，下次启动读回了现场。模拟器的 9 次阳性都恢复执行，随后在测试脚本重启示例时取得系统 trace。对照组从注入触摸前的设备时间到 UI 收到探针，一加为 95–199 ms，模拟器为 54–67 ms。这段时间含 ADB 调度开销。

**如何理解精度。** 18 次阳性都由对应进程的系统 `am_anr` 触摸超时事件确认。插件选中的现场采样有 3 次得到原生帧匹配支持，15 次按时间邻近选取。采样在系统 dump 时间之后 309–767 ms。系统与现场在 17/18 次中都能看见同一业务方法。模拟器第三轮 JSON 的系统栈没有业务帧，之后 740 ms 的现场采样补上了解码函数。一加第三轮在系统栈上是汇总，在之后 380 ms 的现场栈上是解码。报告保留两次观察及其时间差，单次采样不能代表整个阻塞过程。

## 复现

需要专用的、已解锁的 ARM64 Android 设备。运行器会更新并反复重启示例，仅向已确认处于前台的示例窗口注入触摸，在本地保存系统与应用诊断。以下命令从仓库根目录执行。保留同一次构建的 APK、Dart 符号和 R8 映射。

```sh
(cd example && flutter build apk --release --obfuscate --target-platform android-arm64 \
  --split-debug-info=build/scenario-symbols --dart-define=STALLTRACE_BUSINESS_LAB=true)
TOOLS=example/build/app/outputs
RUN=example/build/business-run
python3 tool/business_run.py --serial "$SERIAL" --rounds 3 --out "$RUN" \
  --install "$TOOLS/flutter-apk/app-release.apk"
python3 tool/verify_business.py "$RUN" --llvm "$LLVM_BIN" \
  --dart-symbols example/build/scenario-symbols/app.android-arm64.symbols \
  --mapping "$TOOLS/mapping/release/mapping.txt" \
  --r8-jar "$ANDROID_HOME/build-tools/36.0.0/lib/d8.jar"
```

`SERIAL` 来自 `adb devices`。`LLVM_BIN` 指向 Android NDK 的 LLVM 工具目录。每轮在独立进程运行六个场景。原始导出在 `round-N/raw`，运行器的独立触摸和工作记录在 `round-N/cases.json`。验证器同时检查进程、事件时间、主 dump 的输入超时描述、处理输入的线程、业务帧以及对照组响应。SQLite 和 monitor 对照执行完全相同的写操作并检查结果。JSON 使用设备校准后的固定页数，每页 256 条合成记录，完成时校验总和。真机被系统终止的阳性用例只有开始页数，没有完成校验和。

## 阅读一份报告

在导出的 `catalog.json` 中选一个 `kind=stall` 的 ID，代入 `STALL_ID`。使用 APK 对应的 R8 映射和 ELF 符号。下面的包名和 Dart 文件路径属于示例，分析自己的应用时替换这两个参数。

```sh
python3 tool/diagnose.py "$RUN/round-1/raw" "$STALL_ID" \
  --llvm "$LLVM_BIN" --elf "libc.so=$RUN/libc.so" \
  --elf "libapp.so=example/build/scenario-symbols/app.android-arm64.symbols" \
  --mapping "$TOOLS/mapping/release/mapping.txt" \
  --r8-jar "$ANDROID_HOME/build-tools/36.0.0/lib/d8.jar" \
  --java-package dev.stalltrace.stalltrace_example --dart-file lib/business_scenarios.dart
```

读 `system.applicationFrames` 看 Android 主 dump 的业务帧，读 `live.applicationFrames` 看现场采样的业务帧。`live.javaDelayMs` 给出获取 Java 栈相对原生采样的起始时间差。`system.owner` 仅在 ART 显式记录等待关系且能唯一找到持锁线程时填充。`mechanismSource` 标明诊断摘要采用哪份观察。没有可关联的主 dump 时，`system` 为 `null`，只展示现场证据。

这组实验覆盖前台输入超时以及根 isolate 和平台线程合并的配置。跨进程 Binder 的服务端、广播或服务超时、异步 Dart 调用的发起方和拆分运行的引擎线程还需要各自的实测。设备或工作负载变化时，取样点和栈完整度也会变化。

三轮对照使用的 APK SHA-256 为 `6ce2825d78fd11994d97bbd540d70dacb8ee900efef02e5d98b11606a2e86216`。示例、符号化工具和验收脚本均在仓库内，原始设备 trace 和特定构建的符号保存在测试机本地。
