# VigorSync Windows 迷你主机移植方案

## 1. 文档目标

本文用于把当前开发机上的 VigorSync 迁移到一台预装 Windows 的迷你主机，并形成可重复、可验收、可回滚的现场部署流程。

适用目标：

- Windows 10/11 x64 迷你主机。
- USB 摄像头、本地显示器、本地音箱或 HDMI 音频。
- 同一局域网内的 Android 手机。
- 第一版以 CPU 运行 MediaPipe Pose，不要求独立显卡。
- 镜端页面、Core API、Live Engine 和 Offline Worker 运行在同一台迷你主机。

本文讨论的是产品化迁移，不包含 Flutter App 的重新签名和上架流程。

## 2. 部署决策

### 2.1 第一版继续使用原生 Windows 部署

当前桌面壳会在一个原生 WebView2 窗口内启动并管理：

- Core API：`127.0.0.1:8011` 或局域网 `0.0.0.0:8011`
- Live Engine：`127.0.0.1:8020`
- Offline Worker：`127.0.0.1:8030`
- 镜端静态页面：`127.0.0.1:8080`

桌面窗口关闭时会停止活动训练、完成录像和摘要落盘，并关闭由它管理的服务。该流程适合镜端产品形态，因此第一版不使用 Docker 作为主要交付方式。

Docker 只在后续确有 Linux 服务器化需求时用于 Core API、Offline Worker 和无摄像头回放模式。Windows 实时摄像头、DirectShow 和 SAPI TTS 仍保留在本机。

### 2.2 部署边界

| 组件 | 运行位置 | 局域网暴露 |
| --- | --- | --- |
| Flutter App | Android 手机 | 客户端 |
| Core API | 迷你主机 | TCP `8011` |
| 手机自动发现 | 迷你主机 | UDP `8765` |
| Live Engine | 迷你主机 | 不开放，仅本机 |
| Offline Worker | 迷你主机 | 不开放，仅本机 |
| 镜端页面 | 迷你主机 | 不开放，仅本机 |
| 摄像头、录像、TTS | 迷你主机 | 不经过局域网 |

### 2.3 不直接复制开发环境的 `.venv`

当前 `.venv` 适合开发机使用，但不能假设可以原样复制到另一台电脑：

- 虚拟环境可能包含开发机绝对路径和较脆弱的入口脚本。
- OpenCV、MediaPipe、JAX 等二进制依赖必须与目标 Windows、CPU 架构和 Python ABI 匹配。
- 当前构建脚本还带有开发机路径假设，例如 `D:\Anaconda`。

迁移应在目标机重新创建 `.venv`，或使用经过验证的离线 wheelhouse；不能把开发机 `.venv` 当作通用安装包。

## 3. 目标机基线

### 3.1 最低建议

| 项目 | 建议 |
| --- | --- |
| 系统 | Windows 10 22H2 或 Windows 11 x64 |
| CPU | 4 核 8 线程，支持 AVX2 |
| 内存 | 8 GB 最低，推荐 16 GB |
| 存储 | SSD，至少预留 30 GB 可用空间 |
| 显示 | 1920×1080，缩放 100% 或 125% |
| 浏览器运行时 | Microsoft Edge WebView2 Runtime |
| Python | 独立安装的 CPython 3.12 x64，或嵌入式运行时 |
| 摄像头 | USB UVC 摄像头，优先固定 USB 端口 |
| 音频 | 可用的本地输出设备，安装简体中文系统语音 |
| 网络 | 千兆有线优先，或稳定的 2.4/5 GHz Wi-Fi |

不把独立显卡作为第一版前置条件。若目标机有显卡，也先保持 CPU 基线验收，避免图形加速环境导致现场行为与当前测试基线不一致。

### 3.2 现场信息采集

迁移前在目标机 PowerShell 中记录以下信息：

```powershell
Get-ComputerInfo |
  Select-Object WindowsProductName, WindowsVersion, OsArchitecture,
    CsProcessors, CsTotalPhysicalMemory

Get-PnpDevice -Class Camera -PresentOnly |
  Select-Object Status, FriendlyName, InstanceId

Get-NetConnectionProfile
```

迁移记录至少包含：

- 迷你主机型号、CPU、内存、系统版本。
- 摄像头名称、USB 端口、默认索引、实际分辨率与 FPS。
- 显示器分辨率、缩放比例、横竖屏方向。
- 系统默认音频设备和中文 TTS 语音。
- 主机局域网 IP、网段、网关和 DHCP 方式。
- VigorSync 版本号和迁移包 SHA-256。

## 4. 目录规划

### 4.1 现场部署目录

建议使用非系统保护目录：

```text
D:\VigorSync\
├── app\                    # 当前发布版本，不直接修改
├── backup\                 # 升级前数据快照
├── config\                 # 本机配置，不进入 Git
└── webdata\                # 可选：采用源码目录运行时使用
```

不要把运行目录放在 `C:\Program Files`。Offline Worker、日志、SQLite 和训练产物都需要持续写文件，标准用户对程序目录通常没有稳定写权限。

如果使用当前轻量启动器，则 `app\` 中应保持完整工作区结构：

```text
D:\VigorSync\app\
├── .venv\
├── apps\
├── configs\
├── packages\
├── scripts\
├── services\
├── VERSION
└── VigorSyncLauncher.exe
```

当前轻量启动器依赖工作区中的 `.venv`、桌面宿主和源码路径，因此发布包必须保留目录结构，不能只复制一个 EXE。

### 4.2 数据目录

源码方式运行时，默认数据位于：

```text
<工作区>\webdata\
├── core\core.db
├── sessions\<session_id>\
├── offline\jobs\<job_id>\
└── runtime\
```

自包含桌面版冻结运行时，默认数据位于：

```text
%LOCALAPPDATA%\VigorSync\webdata\
```

迁移和备份必须让以下数据处于同一数据根目录：

- Core SQLite 数据库。
- `sessions` 目录。
- `offline\jobs` 目录。
- 报告引用的视频、关键点和证据图。

`webdata\runtime\browser-profile`、浏览器缓存和 npm cache 不属于业务数据，不应纳入常规数据迁移包。

## 5. 发布包策略

### 5.1 P0：先行迁移包

第一阶段采用“固定版本工作区 + wheelhouse + 安装脚本”方式：

```text
VigorSync-<version>-win-x64.zip
├── app\                    # 完整工作区，不含 .venv、webdata、dist、build
├── wheels\win_amd64\       # Python 3.12 离线依赖
├── config\machine.env.example
├── scripts\install_windows.ps1
├── VERSION
└── SHA256SUMS.txt
```

优点：

- 与当前开发路径一致，移植风险最低。
- 依赖版本可由 wheelhouse 固定。
- 摄像头、TTS 和 WebView2 行为容易排查。

要求：

- 目标机 Python 版本与 wheelhouse 一致。
- 安装过程不依赖开发机的 Anaconda。
- 所有依赖必须来自锁定版本，不使用无约束的 `pip install mediapipe`。

### 5.2 P1：便携交付包

验证稳定后，将 CPython 3.12 嵌入运行时和已安装依赖封装进发布目录，现场无需单独安装 Python。

该方案仍需保留 MediaPipe 模型资源、Offline Worker `vendor\vls`、镜端静态文件和配置目录。

### 5.3 P2：完整 PyInstaller 目录包

`scripts\build_desktop.ps1` 可以作为完整自包含构建入口，但必须先解决和验证：

- MediaPipe `.task` 模型及数据文件收集。
- OpenCV 动态库和编码器。
- Core、Offline Worker 动态导入的 hidden imports。
- Offline Worker `vendor\vls` 和配置资源。
- WebView2 原生依赖。
- Windows SAPI、写文件权限和数据迁移路径。

当前工程已补齐以下 P2 链路：

- 三个 Python 服务均进入 `VigorSyncDesktop.exe` 的打包分析路径。
- VLS 运行源码、配置、模型、报告脚本和轨迹模板随应用目录分发。
- 离线分析子进程通过打包 EXE 的 `--run-python-script` 模式执行内置脚本。
- Inno Setup 安装工程和构建脚本位于 `packaging\windows\` 与
  `scripts\build_installer.ps1`。
- 自包含 EXE 已通过四服务 headless 启动冒烟。

构建：

```powershell
.\scripts\build_desktop.ps1
.\scripts\build_installer.ps1 -Version 0.1.0
```

日常构建默认使用 `lzma2/normal`；正式发布可使用：

```powershell
.\scripts\build_installer.ps1 `
  -Version 0.1.0 `
  -Compression lzma2/ultra64
```

P2 仍需在干净 Windows 用户环境完成安装、升级、卸载、摄像头、TTS 和完整离线报告
验收；通过前不能删除 P0/P1 回退路径。

## 6. 目标机安装步骤

### 6.1 准备系统

1. 安装 Windows 更新、显卡驱动和芯片组驱动。
2. 确认 WebView2 Runtime 可用。
3. 将网络配置文件设为“专用网络”。
4. 给迷你主机设置 DHCP 保留地址，或配置固定局域网 IP。
5. 设置正确的时区和时间同步。
6. 关闭睡眠、休眠和屏幕自动关闭。
7. 安装简体中文系统语音，设置默认扬声器。

### 6.2 创建独立 Python 环境

使用 CPython 3.12 x64，不使用开发机的 Anaconda：

```powershell
cd D:\VigorSync\app
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
```

有网络时：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-contracts.txt
.\.venv\Scripts\python.exe -m pip install -r services\core-api\requirements.txt
.\.venv\Scripts\python.exe -m pip install -r services\live-engine\requirements.txt
.\.venv\Scripts\python.exe -m pip install -r services\offline-worker\requirements.txt
.\.venv\Scripts\python.exe -m pip install -r apps\desktop\requirements.txt
```

离线现场使用 wheelhouse：

```powershell
.\.venv\Scripts\python.exe -m pip install `
  --no-index `
  --find-links D:\VigorSync\wheels\win_amd64 `
  -r requirements-contracts.txt `
  -r services\core-api\requirements.txt `
  -r services\live-engine\requirements.txt `
  -r services\offline-worker\requirements.txt `
  -r apps\desktop\requirements.txt
```

安装后记录：

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip freeze
```

冻结输出作为该版本目标机的依赖清单。

### 6.3 配置本机参数

当前代码从进程环境变量读取参数。短期迁移可先在启动前注入用户或系统环境变量；正式发布应增加 `config\machine.env` 加载器，避免手工修改系统全局环境。

建议配置：

```dotenv
VIGORSYNC_CORE_HOST=0.0.0.0
VIGORSYNC_LIVE_HOST=127.0.0.1
VIGORSYNC_LIVE_PORT=8020

VIGORSYNC_CAMERA_INDEX=1
VIGORSYNC_CAMERA_BACKEND=dshow
VIGORSYNC_CAMERA_WIDTH=640
VIGORSYNC_CAMERA_HEIGHT=480
VIGORSYNC_CAMERA_FPS=30
VIGORSYNC_INFERENCE_FPS=20
VIGORSYNC_PREVIEW_FPS=15

VIGORSYNC_TTS_ENABLED=1
VIGORSYNC_TTS_VOICE=Huihui
VIGORSYNC_TTS_RATE=170
VIGORSYNC_TTS_VOLUME=1.0

VIGORSYNC_WEATHER_LOCATION=深圳
VIGORSYNC_WEATHER_LATITUDE=22.5431
VIGORSYNC_WEATHER_LONGITUDE=114.0579
```

当前代码同时存在 `VIGORSYNC_*` 与 `VIGORYSYNC_*` 两种拼写。现场配置必须按当前版本已实现的变量名填写；后续应统一命名并保留一个版本周期的兼容读取。

正式迁移时还必须替换默认开发密钥：

- `VIGORYSYNC_OFFLINE_INTERNAL_TOKEN`
- `VIGORYSYNC_MIRROR_TOKEN`
- `VIGORYSYNC_PAIRING_CODE`

密钥只保存在目标机本机配置中，不提交 Git，不写入日志。

## 7. 摄像头适配

### 7.1 当前限制

当前默认摄像头索引是开发机的 USB 摄像头索引 `1`。程序只打开配置的索引，不会扫描其他设备，也不会在 DirectShow 打不开时自动切换 MSMF。

因此现场必须显式完成一次摄像头探测，不能假设索引仍为 `1`。

### 7.2 探测流程

1. 关闭相机、会议软件和浏览器摄像头页面。
2. 列出当前摄像头设备。
3. 使用单帧冒烟脚本逐个测试候选索引。
4. 选择能稳定出帧且正确识别 Pose 的索引。
5. 固定 USB 接口，不随意更换。
6. 记录设备名称、索引、后端和实际分辨率。

PowerShell：

```powershell
Get-PnpDevice -Class Camera -PresentOnly |
  Select-Object Status, FriendlyName, InstanceId
```

逐个测试：

```powershell
.\.venv\Scripts\python.exe services\live-engine\scripts\smoke_camera.py `
  --camera-index 0 `
  --frames 20 `
  --exercise squat `
  --view side_left
```

将 `--camera-index` 依次改为 `1`、`2` 等，直到得到正确画面。不要把自动扫描范围无条件设得很大，避免误选虚拟摄像头或会议软件虚拟设备。

DirectShow 无法稳定打开时再测试 MSMF：

```powershell
$env:VIGORSYNC_CAMERA_BACKEND = "msmf"
```

当前基线优先：

```dotenv
VIGORSYNC_CAMERA_BACKEND=dshow
VIGORSYNC_CAMERA_WIDTH=640
VIGORSYNC_CAMERA_HEIGHT=480
VIGORSYNC_CAMERA_FPS=30
```

摄像头驱动可能拒绝请求的参数。正式验收必须检查实际输出尺寸、录像播放速度和事件时间轴，而不是只确认脚本没有报错。

### 7.3 机位适配

同一型号摄像头换到不同高度、距离和角度后，Pose 置信度与动作阈值仍可能变化。

每个正式机位至少验证：

- 深蹲侧面：全身入镜，膝、髋、踝在下降阶段持续可见。
- 深蹲正面：左右膝、髋和肩可见。
- 俯卧撑侧面：肩、肘、腕、髋、膝或踝根部可见。
- 俯卧撑正面：左右肩、肘、腕可见。

机位变化后必须重新录制小样本并确认计数、深度、膝内扣、前倾等提示，不直接沿用原机位的质量结论。

## 8. 网络与防火墙

### 8.1 开放端口

只对专用网络和本地子网开放：

- TCP `8011`：手机访问 Core API。
- UDP `8765`：手机发现宿主。

不开放：

- TCP `8020`：Live Engine。
- TCP `8030`：Offline Worker。
- TCP `8080`：镜端页面。

管理员 PowerShell 示例：

```powershell
New-NetFirewallRule `
  -DisplayName "VigorSync Core TCP 8011" `
  -Direction Inbound `
  -Action Allow `
  -Protocol TCP `
  -LocalPort 8011 `
  -Profile Private `
  -RemoteAddress LocalSubnet

New-NetFirewallRule `
  -DisplayName "VigorSync Discovery UDP 8765" `
  -Direction Inbound `
  -Action Allow `
  -Protocol UDP `
  -LocalPort 8765 `
  -Profile Private `
  -RemoteAddress LocalSubnet
```

不要把 Windows 配置文件设为“公用网络”。如果现场必须使用公用网络配置，应先完成更严格的网段限制和访问验证。

### 8.2 固定地址

推荐在路由器中为迷你主机设置 DHCP 保留地址。需要在手机上手工填写地址时，使用：

```text
http://<迷你主机局域网IP>:8011
```

Android 模拟器继续使用：

```text
http://10.0.2.2:8011
```

实体手机不能使用 `127.0.0.1` 或 `10.0.2.2`。

## 9. 显示、TTS 与开机启动

### 9.1 显示

- 优先使用标准 1920×1080，缩放 125% 以内。
- 检查待机页、训练页和报告在目标分辨率下没有溢出、裁切或时间错位。
- 关闭 Windows 屏幕保护、自动睡眠和快速启动带来的异常残留。
- 横屏与竖屏都需现场确认；若产品只支持一种方向，应固定并记录。

### 9.2 TTS

确认中文系统语音存在并完成扬声器试播：

```powershell
Add-Type -AssemblyName System.Speech
$speech = New-Object System.Speech.Synthesis.SpeechSynthesizer
$speech.GetInstalledVoices() |
  ForEach-Object { $_.VoiceInfo.Name }
$speech.Speak("VigorSync 语音测试")
```

验收至少覆盖：

- “完成 5 个”等计数里程碑。
- 目标次数达成。
- 至少一种稳定姿态纠正提示。
- 训练中静音与恢复。
- TTS 异常时训练、计数和录像继续。

### 9.3 开机启动

桌面版需要交互式 Windows 会话，因此使用“用户登录时”的计划任务启动，不使用无界面的 Windows Service 托管 WebView2。

建议：

1. 建立专用本地用户并配置自动登录。
2. 在“任务计划程序”创建登录时任务。
3. 启动 `D:\VigorSync\app\VigorSyncLauncher.exe`。
4. 不勾选“只在计算机空闲时运行”。
5. 确保任务在标准用户交互会话中运行。

自动登录只适用于受控的本地设备。系统磁盘启用 BitLocker 或设备加密。

## 10. 数据迁移与备份

切换前先在旧机停止 VigorSync，确认关闭窗口后没有活动训练。

需要备份：

```text
webdata\core\core.db
webdata\sessions\
webdata\offline\jobs\
```

不要整目录复制以下缓存：

```text
webdata\runtime\browser-profile\
webdata\runtime\webview\
webdata\runtime\npm-cache\
webdata\runtime\*.log
```

迁移后任意一次训练都应确认：

- 手机历史记录与 `webdata\core\core.db` 一致。
- 会话视频和 `landmarks.jsonl` 位于 `webdata\sessions`。
- 离线报告和证据图位于 `webdata\offline\jobs`。
- 手机删除记录后，Core 元数据和对应离线 job 均按接口约定处理。

升级时采用新目录保留旧版本：

```text
D:\VigorSync\app-<old-version>\
D:\VigorSync\app-<new-version>\
```

切换成功后再更新桌面快捷方式。禁止在原目录无备份覆盖升级。

## 11. 执行顺序

### 阶段 0：冻结与准备

- 确认 Git 提交、版本号和目标机架构。
- 生成 wheelhouse 与 `SHA256SUMS.txt`。
- 记录当前摄像头、TTS、端口和依赖基线。
- 准备旧机数据备份。

### 阶段 1：目标机基础环境

- 完成 Windows 更新和 WebView2 检查。
- 设置网络为专用网络并预留 IP。
- 建立部署目录、本地用户和显示/音频配置。
- 安装 Python 3.12 x64。

### 阶段 2：部署运行环境

- 解压固定版本工作区。
- 创建目标机 `.venv`。
- 从 wheelhouse 安装依赖。
- 固化 `pip freeze`。
- 注入本机配置和密钥。

### 阶段 3：单机冒烟

- 摄像头逐索引探测。
- Pose 20 帧检测。
- 启动桌面壳并检查四个健康接口。
- 验证 TTS、WebView2、镜端待机页。

### 阶段 4：端到端联调

- 实体手机发现或手工填写 Core 地址。
- 手机创建、启动、暂停、继续和结束。
- 断开手机后由镜端本地恢复。
- 完成离线分析并打开报告。

### 阶段 5：稳定性与交付

- 连续训练和重启验证。
- 记录现场配置和实测指标。
- 培训现场操作与回滚方法。
- 封存发布包、wheelhouse、配置模板和验收记录。

## 12. 验收清单

### 12.1 服务

- `http://127.0.0.1:8011/api/v1/health` 返回正常。
- `http://127.0.0.1:8020/api/v1/health` 返回正常。
- `http://127.0.0.1:8030/api/v1/health` 返回正常。
- `http://127.0.0.1:8080` 可打开镜端页面。
- 端口冲突时桌面日志能指出失败服务，而不是静默空白。

### 12.2 摄像头与实时性能

- 正确摄像头在 20 帧冒烟中稳定得到 Pose。
- 请求分辨率与实际分辨率已记录。
- 实时推理不低于 15 FPS，目标 20 FPS。
- MJPEG 预览不低于 15 FPS。
- 录像时间轴与事件时间轴无明显加速、减速或错位。
- 打开到 `active` 目标 5 秒内；冷启动超时必须有可见状态。
- 关闭其他相机程序后可以独占设备。
- 断连摄像头或启动失败后仍能安全结束训练。

性能门槛以同条件旧机实测或当前验收记录为对照。若目标机低于门槛，优先降低分辨率、推理 FPS 和预览 FPS，并重新验证计数，不单纯追求参数最高值。

### 12.3 手机与镜端

- 实体手机能自动发现，也能手工填写 IP。
- 手机创建训练后镜端同步切换。
- 手机配置与镜端首页一致。
- 暂停、继续、结束和返回主页均可同步。
- 手机断网时机端本地训练不被强制终止。
- 镜端刷新后能恢复当前会话。

### 12.4 动作与语音

- 深蹲和俯卧撑各完成至少一组人工计数对照。
- 计数多算、漏算问题在目标机重新确认，不直接继承旧机结论。
- 侧面和正面机位的质量门控工作正常。
- 稳定纠正提示可以播报并遵守冷却策略。
- 完成 5 个、目标达成和静音功能正常。

### 12.5 离线分析与数据

- 训练结束后自动生成离线 job。
- 报告可在浏览器中直接查看，不触发下载。
- 回放、3D 骨架、问题卡和证据区间可用。
- 手机历史时间、动作、次数与镜端数据一致。
- 在手机中删除记录后，对应 Core 记录、媒体和离线 job 的处理符合当前产品规则。
- 应用重启后历史记录仍存在。

### 12.6 桌面生命周期

- 双击启动只允许一个实例。
- 训练中点击窗口 `X` 会弹出确认。
- 确认退出后视频、关键点和 summary 完整落盘。
- 退出后 `8011`、`8020`、`8030`、`8080` 均释放。
- 再次启动不会报摄像头被旧进程占用。

### 12.7 稳定性

- 连续训练 10 分钟无崩溃、黑屏或明显内存持续增长。
- 连续启动和关闭至少 20 次。
- 蓝牙/USB 摄像头重新插拔后可恢复。
- 手机切换 Wi-Fi 后可重新连接。
- 处理 1080p 报告和多会话回放时 CPU、内存和磁盘无异常。

## 13. 回滚条件

满足任一条件时停止切换并回滚：

- Core、Live Engine 或 Offline Worker 无法稳定启动。
- 摄像头无法在目标机得到可用 Pose。
- 实时计数在目标机出现系统性多算或漏算。
- TTS 阻塞训练或反复触发错误播报。
- 手机无法在验收网络中发现或连接 Core。
- 录像时间轴、报告或数据删除出现不可接受的数据错误。
- 连续启动、关闭或 10 分钟训练出现崩溃。

回滚步骤：

1. 关闭 VigorSync。
2. 保留新版本日志和失败现场。
3. 停止新版本计划任务。
4. 恢复旧版本目录、配置和经过确认的 `webdata` 快照。
5. 启动旧版本并完成健康接口、摄像头和手机连接冒烟。
6. 记录失败原因、版本号、摄像头设备和复现步骤。

## 14. 主要风险

| 风险 | 影响 | 处理 |
| --- | --- | --- |
| `.venv` 被直接跨机器复制 | 原生库加载失败 | 目标机重新创建环境或使用固定 wheelhouse |
| 摄像头索引变化 | 启动时报打不开或打开错误设备 | 现场重建索引映射、固定 USB 接口 |
| DirectShow 驱动兼容性 | 首帧慢或黑屏 | 逐索引冒烟，必要时切 MSMF |
| 请求的 30 FPS 未被驱动接受 | 录像和事件时间轴偏差 | 读取实际参数并重新验收 |
| 默认开发密钥未替换 | 局域网控制风险 | 首次部署强制替换并记录 |
| 防火墙未开放 | 手机显示未连接 | 仅对专用网络和本地子网开放 TCP 8011、UDP 8765 |
| 系统睡眠或锁屏 | 训练中断或窗口消失 | 禁用睡眠，使用交互式登录任务 |
| WebView2 缺失 | 桌面窗口无法启动 | 在发布前置检查中安装 Runtime |
| 中文语音缺失 | TTS 静默或音色错误 | 安装语音包并执行独立试播 |
| MediaPipe/OpenCV DLL 未收集 | 自包含包启动失败 | P1/P2 打包后必须在干净目标机复测 |
| 数据目录混入缓存 | 包体积过大和旧状态污染 | 只迁移业务数据 |

## 15. 后续改进项

在本轮迁移完成后，建议按以下顺序补齐产品化能力：

1. 增加 `config\machine.env` 加载器和配置校验，不依赖全局环境变量。
2. 增加摄像头候选探测、DirectShow/MSMF 回退和实际分辨率/FPS 读回。
3. 用摄像头名称、VID/PID 或序列号绑定稳定设备身份，避免索引漂移。
4. 增加“迁移诊断”页面，显示摄像头、WebView2、音频、端口和网络状态。
5. 将 P0 工作区包升级为 P1 便携 Python 包，减少现场安装步骤。
6. 在干净 Windows 虚拟机上验证安装、升级、卸载和 P2 PyInstaller 包，再决定是否替代 P1。
7. 评估 Windows 日志轮转、数据保留和磁盘告警策略。

## 16. 完成定义

目标机只有同时满足以下条件才算迁移完成：

- 不依赖开发机、Anaconda、Git 或手机调试环境即可启动。
- 桌面窗口可正常启动，关闭窗口能完整结束训练和释放全部服务。
- 实体手机可稳定连接并控制镜端。
- 摄像头、TTS、实时计数、纠错和离线报告均通过验收。
- 业务数据位于已规划目录，并完成至少一次备份恢复验证。
- 记录目标机完整基线、依赖清单、配置差异和验收结果。
- 保留可恢复的上一版本，发生现场故障时可以在一轮操作内回滚。
