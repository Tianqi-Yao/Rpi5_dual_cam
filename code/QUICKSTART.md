# 快速上手

## 安装依赖（第一次使用）

```bash
sudo apt update
sudo apt install -y python3-picamera2 rpicam-apps
sudo apt install python3-opencv python3-matplotlib
```

## 确认相机

```bash
rpicam-hello --list-cameras
```

记下顺序：第一个 = `CAM_IDX=0`，第二个 = `CAM_IDX=1`。

## 启动

编辑 `preview_focus_hybrid.py` 顶部：

```python
CAM_IDX  = 0      # 改成你要用的相机
INIT_LP  = 15.0   # 找到最佳对焦值后写在这里
```

然后运行：

```bash
cd code
python3 preview_focus_hybrid.py
```

---

## 基本操作流程

### 1. 对焦

```
t        → 自动对焦（推荐先用一次）
] [      → LP ±0.5（粗调）
= -      → LP ±0.1（精调）
u        → burst：在当前 LP 附近即时拍 5 张（~1.5秒），找最清晰的
```

找到最佳 LP 后，把值写入顶部 `INIT_LP`，下次启动直接用。

### 2. 调整曝光

```
e / w    → EV +0.5 / -0.5
y        → EV bracket：拍 5 张（EV -1.0 到 +1.0），找最合适亮度
```

### 3. 调整视角

```
d / a    → 放大 / 缩小（1x–20x）
i k j l  → 平移（上/下/左/右）
r        → 重置视角到 1x 中心
1-5      → 2x 区域快速跳转（中/四角）
6-0      → 4x 区域快速跳转
```

### 4. 对比采集

```
m        → 一次拍全部 6 张（推荐，覆盖所有路线）
```

或单独拍某一条：

```
z → rpicam JPEG（5s）    x → rpicam PNG（5s）    c → rpicam DNG（5s）
v → Picamera2 JPEG（即时）  b → Picamera2 PNG（即时）  n → Picamera2 DNG（5s）
```

**v/b 是直接从运行中的 preview 抓帧，即时完成，分辨率 = preview 分辨率（`p` 键控制）。**

---

## 其他常用按键

```
p        → 切换 preview 分辨率（4624×3472 ↔ 9248×6944）
           同时也决定了 v/b 的保存分辨率
g        → 切换 rpicam/DNG 保存分辨率（FULL 64MP ↔ HALF 16MP）
           只影响 z/x/c/n，不影响 v/b
h        → 切换 preview 后端（Picamera2 ↔ rpicam-still）
f        → 打印当前状态
q        → 退出
```

---

## 输出位置

```
~/Desktop/images/preview_captures/cam{N}/
```

按 `m` 产生的 6 张文件共享同一时间戳，文件名里含路线标识（`z_r1_rpicam_jpg` 等），便于对比。

---

## 常见问题

**按 z/x/c/n 后要等很久？**
正常。这些路线需要开新相机并等待 AE/镜头稳定（5s），v/b 直接用 preview 无需等待。

**v/b 和 z/x/c 同一场景颜色/锐度有差异？**
正常。两条路线走不同的 ISP 参数，这正是对比实验的目的。

**v/b 分辨率和 z/x/c 不一样？**
v/b 保存的是 preview 分辨率（由 `p` 键决定）；z/x/c 保存分辨率由 `g` 键决定。
要让 v/b 也拍全分辨率，先按 `p` 切换 preview 到 9248×6944（会变慢）。

**DNG 文件怎么看？**
用 darktable 或 Lightroom 打开，可以在后期关掉 NR（降噪）再查看细节。

**详细设计说明？**
见同目录 `README.md`。
