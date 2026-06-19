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
CAM_IDX = 0   # 改成你要用的相机
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
t          → 自动对焦（推荐先用一次）
] [        → LP ±0.5（粗调）
= -        → LP ±0.1（精调）
u          → burst：在当前 LP 附近拍 5 张（±0.5 范围），找最清晰的
```

找到最佳 LP 后，把值写入顶部 `INIT_LP`，下次启动直接用。

### 2. 调整曝光

```
e / w      → EV +0.5 / -0.5
y          → EV bracket：拍 5 张（EV -1.0 到 +1.0），找最合适亮度
```

### 3. 调整视角

```
d / a      → 放大 / 缩小（1x–20x）
i k j l    → 平移（上/下/左/右）
r          → 重置视角到 1x 中心
1-5        → 2x 区域快速跳转（中/四角）
6-0        → 4x 区域快速跳转
```

### 4. 对比采集

```
m          → 一次拍全部 6 张（推荐，覆盖所有路线）
```

或单独拍某一条：

```
z → rpicam JPEG    x → rpicam PNG    c → rpicam DNG
v → Picamera2 JPEG  b → Picamera2 PNG  n → Picamera2 DNG
```

---

## 其他常用按键

```
g          → 切换保存分辨率（FULL 64MP ↔ HALF 16MP）
p          → 切换预览分辨率（4624×3472 ↔ 9248×6944，后者极慢）
h          → 切换预览后端（Picamera2 ↔ rpicam-still）
f          → 打印当前状态
q          → 退出
```

---

## 输出位置

```
~/Desktop/images/preview_captures/cam{N}/
```

按 `m` 产生的 6 张文件共享同一时间戳，文件名里含路线标识（`z_r1_rpicam_jpg` 等），便于对比。

---

## 常见问题

**按 z/x/c 后要等很久？**
正常。rpicam-still 默认等 5 秒让曝光收敛再拍，和 Picamera2 路线（v/b/n）的等待时间对齐，保证对比公平。

**DNG 文件怎么看？**
用 darktable 或 Lightroom 打开，可以在后期关掉 NR（降噪）再查看细节，适合评估 ISP 对 4px 特征的影响。

**颜色看起来反了？**
见 README.md 第四节，原因和解决方法在那里。

**详细设计说明？**
见同目录 `README.md`。
