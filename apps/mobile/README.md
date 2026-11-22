# VigorSync Mobile

Flutter 手机控制端，负责局域网配对、创建训练、暂停/恢复/结束、实时查看次数与
关节角，以及浏览本机训练历史。摄像头、Pose 推理和原始画面仍只运行在镜端。

## 页面

- `配对`：填写 Core API 地址、六位配对码和设备名。
- `训练`：选择深蹲/俯卧撑、机位和目标次数，启动镜端训练。
- `实时会话`：显示 rep、计时、阶段、主关节角、置信度、推理帧率和告警。
- `历史`：显示 Core 持久化的训练记录。

配对 token 保存在 Android 安全存储中。手机 WebSocket 使用 Bearer header
握手；手机不接收原始帧或完整关键点。

首页配置会实时同步到镜端。训练结束后点击总结页“返回主页”时，镜端也会从总结页
返回配置页。

手机点击启动时使用合并接口 `/api/v1/sessions/start`，不再依次等待配置同步、创建
会话和启动会话三个请求。

历史卡片右下角可永久删除已结束训练；确认后同时移除 Core 记录和本机视频、关键点、
事件与摘要。训练中的记录不会开放删除。

M3 离线报告完成后，历史卡片会显示“查看离线复盘”。手机原生页展示离线计数、
实时计数、质量状态、问题数和主要问题卡；“打开完整证据报告”会调用系统浏览器
访问 Core 签发的报告票据地址。

Core 时间统一以 UTC 存储，首页和历史页会按手机本地时区显示为“今天/昨天 HH:mm”，
并同时显示中文状态、机位和训练时长。

首页和历史页右上角的太阳/月亮按钮可切换深色与浅色主题，选择会保存在本机。
训练记录使用自绘深蹲、俯卧撑姿态图标，不依赖通用健身图标猜测动作。

## 本地运行

先启动 Core、Live Engine 和镜端页面：

```powershell
cd E:\project\Exercise\VigorSync
powershell -ExecutionPolicy Bypass -File .\scripts\mirror-control.ps1 -Action start -NoBrowser
```

再启动 Android 模拟器并运行 App：

```powershell
.\scripts\start_avd.cmd
cd apps\mobile
D:\flutter\bin\flutter.bat run -d emulator-5554
```

`start_avd.cmd` 会自动选择唯一 AVD、复用已运行模拟器并等待开机完成。
也可通过 `start_avd.ps1 -StartServices -InstallApp` 同时启动后端并打开现有调试包。

模拟器内 Core 地址使用 `http://10.0.2.2:8011`，开发配对码默认为
`482913`。实体手机使用电脑的局域网 IPv4 地址，例如
`http://192.168.1.20:8011`，并确保 Windows 防火墙允许 TCP 8011。

## 构建与检查

国内网络构建时可复用以下环境：

```powershell
$env:JAVA_HOME = "D:\work app\Android\Android Studio\jbr"
$env:ANDROID_HOME = "D:\app\Android\SDK"
$env:FLUTTER_STORAGE_BASE_URL = "https://storage.flutter-io.cn"
```

`android/settings.gradle.kts` 已配置阿里云 Maven 与 Flutter 中国镜像。构建调试包：

```powershell
D:\flutter\bin\flutter.bat build apk --debug --no-pub
```

产物位于 `build\app\outputs\flutter-apk\app-debug.apk`。静态检查和测试：

```powershell
D:\flutter\bin\flutter.bat analyze
D:\flutter\bin\flutter.bat test
```

M2 全套回归可从仓库根目录运行：

```powershell
.\scripts\check_m2.ps1
.\scripts\check_m2.ps1 -BuildApk
```

M3 已接入离线任务和报告入口，后续继续完善报告页的小屏回放体验。
