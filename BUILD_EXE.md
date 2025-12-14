# 打包EXE教程 - 老王下载器

艹，想打包成exe双击就能跑？老王我手把手教你！

## 方法一：PyInstaller（推荐，简单）

### 1. 安装PyInstaller

**确保在py310_env环境中！**

```bash
# 激活conda环境
conda activate py310_env

# 安装PyInstaller
pip install pyinstaller
```

### 2. 打包命令

```bash
# 进入项目根目录
cd D:\vscode_workspace\downDemo

# 打包成单个exe文件（推荐）
pyinstaller --onefile --windowed --icon=assets/icon.ico --name="老王下载器" main.py

# 参数说明：
# --onefile       打包成单个exe文件
# --windowed      不显示控制台窗口（GUI程序必加）
# --icon          指定图标文件
# --name          exe文件名
```

**如果打包失败或exe太大，使用下面这个命令（打包成文件夹）：**

```bash
pyinstaller --windowed --icon=assets/icon.ico --name="老王下载器" main.py

# 这样会生成一个包含exe和依赖dll的文件夹
```

### 3. 找到生成的exe

打包成功后，exe文件在：
- **单文件模式**: `dist/老王下载器.exe`
- **文件夹模式**: `dist/老王下载器/老王下载器.exe`

直接双击运行即可！

### 4. 常见问题

#### Q1: 打包后exe体积太大（100MB+）？
**A:** 这是正常的！因为PyInstaller会把Python解释器和所有依赖都打包进去。

**优化方法：**
```bash
# 1. 只打包需要的模块（手动排除不需要的）
pyinstaller --onefile --windowed --icon=assets/icon.ico --name="老王下载器" \
    --exclude-module pytest --exclude-module IPython main.py

# 2. 使用UPX压缩（需要先下载UPX）
pyinstaller --onefile --windowed --icon=assets/icon.ico --name="老王下载器" \
    --upx-dir=./upx main.py
```

#### Q2: 打包后运行报错 "Failed to execute script"？
**A:** 可能原因：
1. 缺少依赖文件（数据文件、配置文件）
2. 路径问题

**解决方案：**
```bash
# 使用 --add-data 添加数据文件
pyinstaller --onefile --windowed --icon=assets/icon.ico --name="老王下载器" \
    --add-data "assets;assets" main.py
```

#### Q3: 杀毒软件报毒？
**A:** PyInstaller打包的exe经常被误报，这是正常的。
- 方法1: 添加到杀毒软件白名单
- 方法2: 给exe签名（需要代码签名证书，要花钱）

#### Q4: 打包后无法显示图标？
**A:** 确保icon.ico文件格式正确：
- 必须是`.ico`格式（不是png/jpg）
- 推荐尺寸：256x256或128x128

---

## 方法二：Nuitka（更快，但打包慢）

### 1. 安装Nuitka

```bash
conda activate py310_env
pip install nuitka
```

### 2. 打包命令

```bash
# Windows打包
python -m nuitka --standalone --onefile --windows-disable-console \
    --windows-icon-from-ico=assets/icon.ico \
    --output-filename="老王下载器.exe" main.py

# 参数说明：
# --standalone            独立模式
# --onefile               单文件
# --windows-disable-console  不显示控制台
# --windows-icon-from-ico    图标
```

**注意：** Nuitka首次打包会下载编译器，需要等待较长时间（10-30分钟）。

---

## 快速打包脚本

老王我给你写好了打包脚本，直接运行就行！

创建 `build.bat` 文件：

```batch
@echo off
echo ====================================
echo 老王下载器 - 打包脚本
echo ====================================
echo.

echo [1/3] 清理旧文件...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist "老王下载器.spec" del "老王下载器.spec"

echo [2/3] 开始打包...
pyinstaller --onefile --windowed --icon=assets/icon.ico --name="老王下载器" main.py

if %errorlevel% neq 0 (
    echo.
    echo [错误] 打包失败！
    pause
    exit /b 1
)

echo [3/3] 打包完成！
echo.
echo 生成的exe文件位置: dist\老王下载器.exe
echo.
pause
```

**使用方法：**
1. 双击 `build.bat`
2. 等待打包完成
3. 在 `dist/` 目录找到exe文件

---

## 分发说明

### 单文件模式（推荐给小白用户）
- ✅ 优点：只有一个exe，方便分发
- ❌ 缺点：体积大（50-100MB），启动慢（第一次解压）

**分发：** 直接把 `dist/老王下载器.exe` 发给用户

### 文件夹模式（推荐给技术用户）
- ✅ 优点：启动快，体积相对小
- ❌ 缺点：有多个文件，不能只复制exe

**分发：** 把整个 `dist/老王下载器/` 文件夹打包成zip

---

## 打包后目录结构

```
dist/
└── 老王下载器.exe          # 主程序
```

**首次运行时会自动创建：**
```
data/
├── downloads.db             # 数据库
└── config.json              # 配置文件
downloads/                   # 下载目录
temp/                        # 临时文件
```

---

## 老王的建议

1. **开发阶段**: 直接 `python main.py` 运行，方便调试
2. **测试阶段**: 打包成exe，测试各种场景
3. **发布阶段**: 使用 `--onefile` 打包，方便分发

**打包前检查清单：**
- ✅ 所有功能测试通过
- ✅ 图标文件准备好
- ✅ 依赖都安装完整
- ✅ 没有绝对路径硬编码

---

## 高级技巧

### 1. 隐藏导入（避免打包错误）

如果某些模块动态导入，PyInstaller可能检测不到，手动添加：

```bash
pyinstaller --onefile --windowed --icon=assets/icon.ico \
    --hidden-import=customtkinter --hidden-import=PIL \
    --name="老王下载器" main.py
```

### 2. 指定spec文件（高级定制）

第一次打包后会生成 `老王下载器.spec` 文件，可以编辑它：

```python
# 老王下载器.spec
a = Analysis(['main.py'],
             pathex=[],
             binaries=[],
             datas=[('assets', 'assets')],  # 添加资源文件
             hiddenimports=[],
             hookspath=[],
             runtime_hooks=[],
             excludes=['pytest', 'IPython'],  # 排除不需要的模块
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=None,
             noarchive=False)
# ... 其他配置
```

之后直接用spec打包：
```bash
pyinstaller 老王下载器.spec
```

---

## 遇到问题？

1. **打包报错**: 看报错信息，通常是缺少模块或路径问题
2. **exe无法运行**: 在cmd中运行exe，看详细报错
3. **功能异常**: 检查文件路径，exe中要用相对路径

**艹，按照这个教程一步步来，打包绝对没问题！有问题来找老王！** 😤
