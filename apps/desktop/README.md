# VigorSync Desktop Shell

轻量 WebView2 桌面外壳。它在同一进程内启动现有 Live Engine 和镜端静态服务，
不使用外部浏览器。

开发启动：

```powershell
cd E:\project\Exercise\VigorSync
.\.venv\Scripts\python.exe apps\desktop\app.py
```

也可以双击 `scripts\start_desktop.cmd`，通过 `pythonw.exe` 启动，不显示控制台窗口。

窗口右上角的 `X` 会：

1. 检查是否存在活动训练。
2. 训练中弹出确认框。
3. 用户确认后停止训练并完成录像、关键点和 summary 落盘。
4. 关闭本程序内部启动的 Live Engine 与镜端服务。

单实例互斥会阻止重复打开导致摄像头争夺。运行日志写入
`webdata/runtime/desktop.log`。

## 构建轻量启动器 EXE

```powershell
cd E:\project\Exercise\VigorSync
.\scripts\build_launcher.ps1
```

生成：

```text
dist\VigorSyncLauncher\VigorSyncLauncher.exe
```

该 EXE 是轻量启动器，调用当前工作区的 `.venv\Scripts\pythonw.exe` 和桌面宿主，
不包含 MediaPipe/OpenCV。它适合当前 M1 开发机，生成快、体积小且没有控制台窗口。

`build_desktop.ps1` 保留为实验性的完整 self-contained 打包入口；MediaPipe 依赖会使
目录达到数百 MB，当前不作为默认交付方式。
