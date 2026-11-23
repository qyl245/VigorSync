# VigorSync Windows Installer

Windows 安装程序使用 Inno Setup 6，输入为 PyInstaller `onedir` 目录：

```text
dist\VigorSyncDesktop\
```

构建脚本先复制出一个干净 staging 目录，再由 Inno Setup 生成安装包。安装包不会读取仓库中的 `webdata`。

## 一键构建

```powershell
cd E:\project\Exercise\VigorSync
.\scripts\build_installer.ps1 -Version 0.1.0
```

日常迭代默认使用较快的 `lzma2/normal` 压缩。发布包可改为：

```powershell
.\scripts\build_installer.ps1 `
  -Version 0.1.0 `
  -Compression lzma2/ultra64
```

输出：

```text
dist\installer\VigorSync-Mirror-0.1.0-win-x64-setup.exe
```

仅调整安装程序、已存在可用的 `dist\VigorSyncDesktop` 时：

```powershell
.\scripts\build_installer.ps1 -Version 0.1.0 -SkipBuild
```

跳过打包后 EXE 冒烟：

```powershell
.\scripts\build_installer.ps1 -Version 0.1.0 -SkipBuild -SkipSmokeTest
```

## 安装行为

- 默认安装到 Program Files。
- 创建开始菜单快捷方式。
- 可选创建桌面快捷方式。
- 可选登录 Windows 后自动启动。
- 可选添加专用网络的 TCP `8011` 和 UDP `8765` 防火墙规则。
- 训练数据写入 `%LOCALAPPDATA%\VigorSync\webdata`。
- 升级保留训练数据。
- 卸载默认保留训练数据。

## 构建产物

```text
dist\
├── VigorSyncDesktop\                 # PyInstaller onedir
├── installer-staging\
│   └── VigorSyncMirror\              # 安装输入，不含 webdata
└── installer\
    ├── VigorSync-Mirror-<version>-win-x64-setup.exe
    └── VigorSync-Mirror-<version>-win-x64-setup.exe.sha256
```

`dist`、`build` 和生成的 `.spec` 均不进入 Git。版本化的安装配置和说明位于当前目录。
