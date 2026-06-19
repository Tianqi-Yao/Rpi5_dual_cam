# preview_focus_hybrid.py — 设计文档

## 一、项目背景与研究目标

本脚本用于在 **Raspberry Pi 5** 上控制 **Arducam 64MP（OV64A40）** 摄像头，对微小目标（约 4 像素宽的虫头黑点）进行高质量采集研究。

研究目标是找到最优的采集方法和参数组合：
- 对比 rpicam-still 与 Picamera2 两种调用方式的成像差异
- 对比 JPEG / PNG / DNG 三种格式的信息保留程度
- 在固定拍摄距离下确定最佳对焦值（LP）
- 理解 ISP 管线在不同配置下对 4px 级别细节的影响

---

## 二、硬件平台选择

OV64A40 传感器全分辨率每帧约 **193MB**。在 Rpi4 上，Picamera2 直接 `capture_array()` 会触发 OOM killer，必须绕道 `rpicam-still` 子进程拍照。

**Rpi5 内存更大，已验证可直接通过 Picamera2 获取 9248×6944 帧而不 OOM**，因此本项目可以：
- 用 Picamera2 做预览（capture_array → OpenCV → imshow）
- Picamera2 路线直接从运行中的 preview 抓帧保存
- 同时保留 rpicam-still 作为独立对比路线

---

## 三、预览架构

改用 OpenCV 显示（而非 QTGL）的原因：
1. 可在画面上方叠加状态栏（`np.vstack([status_bar, frame])`）
2. 键盘焦点在 GUI 窗口上（`cv2.waitKey()`），不需要 termios raw 模式
3. Rpi5 上 `capture_array()` 帧率满足实时操作需求

---

## 四、ISP 管线与信息损耗分析

### 传感器到文件的完整链路

```
传感器 9248×6944 (raw Bayer, 12-bit)
    ↓ ISP（不可逆）
      · Demosaic：插值补全每像素缺失的 R/G/B
      · Noise Reduction：降噪（可能模糊细节）
      · White Balance / 色彩校正
      · Sharpening
    ↓ 输出到 main stream
    ↓ 格式压缩（视保存方式）
    ↓ 文件
```

### 三种格式的信息保留对比

| 格式 | ISP 影响 | 额外损耗 | 适用场景 |
|---|---|---|---|
| **JPEG** | 有 | **有（8×8 DCT 块压缩）** | 文件小，快速浏览 |
| **PNG** | 有 | **无（无损压缩）** | ISP 后零损失，所见即所保存 |
| **DNG** | **无** | **无** | 原始 Bayer 数据，后期自控 ISP |

对 4px 虫头黑点：JPEG 的 8×8 DCT 块会导致边缘振铃、黑点变灰；PNG 保留完整像素值；DNG 可在后期（darktable）关闭 NR 后检查。

### create_preview_configuration vs create_still_configuration

**关键发现**：Picamera2 的 `create_still_configuration` 与 `create_preview_configuration` 使用不同的 ISP tuning 参数（不同的 NR 强度、锐化程度），导致：
- still config 采集的图像与 preview 看到的图像**风格不一致**，且 still config 在 `buffer_count=1` 时 AE 来不及收敛（过曝）
- **解决方案：所有 Picamera2 保存路线均使用 `create_preview_configuration`**，确保"所见即所保存"

### rpicam-still 与 Picamera2 的 ISP 差异

两者底层使用同一块 ISP 硬件，但 tuning 参数不同：
- Picamera2 使用自己的调优参数
- rpicam-still 使用 rpicam-apps 的调优参数

**同一场景两条管线的颜色、锐度、NR 程度不完全相同**——这正是做对比实验的意义所在。

### 颜色通道格式

经实测，本机 Picamera2 的 `capture_array()` 在配置 `"format": "RGB888"` 时实际返回 **BGR 排列的数据**。OpenCV 期望 BGR，因此代码中不做任何颜色转换。

---

## 五、6 条保存路线设计

| 按键 | 路线 | 调用方式 | 格式 | 分辨率来源 | ISP |
|---|---|---|---|---|---|
| `z` | R1 | rpicam-still | JPEG | `g` 键（FULL/HALF） | rpicam-apps |
| `x` | R2 | rpicam-still | PNG | `g` 键 | rpicam-apps |
| `c` | R3 | rpicam-still | DNG+JPEG | `g` 键 | rpicam-apps；DNG 无 ISP |
| `v` | R4 | Picamera2（直接从 preview 抓） | JPEG | **`p` 键**（preview 分辨率） | Picamera2 preview |
| `b` | R5 | Picamera2（直接从 preview 抓） | PNG | **`p` 键** | Picamera2 preview |
| `n` | R6 | Picamera2（新开相机+raw流） | DNG | `g` 键 | 无（raw Bayer） |
| `m` | ALL | 先 R4/R5，再停机做 R1~R3/R6 | 各一张 | — | — |

**重要**：R4/R5 保存分辨率由 `p` 键（preview 采集分辨率）决定，与 `g` 键（保存分辨率）无关。

### 文件命名规则

```
{时间戳}_{按键}_{路线}_{方式}_{格式}_lp{LP}_ev{EV}_cam{N}.{ext}
例：20260619_120000_z_r1_rpicam_jpg_lp5.00_ev+0.0_cam0.jpg
```

按 `m` 时 6 张共享同一时间戳，便于横向对比。

---

## 六、曝光（AE）设计思路

### Picamera2 路线（v/b）：直接从 preview 抓，无需 AE 收敛

`v/b` 键直接调用 `qbe.cam.capture_array()`——preview 已经在运行，AE 已收敛，镜头在位，不停止/重启相机。这是最清晰、曝光最准确的方式。

### Picamera2 DNG（n）：开新相机，5s 收敛

DNG 需要 raw 流，必须重新配置相机。新相机开启后等待 5 秒让 AE 和镜头稳定。

### rpicam-still（z/x/c）：独立 AE，默认 5s

`rpicam-still` 不传 `-t` 参数时，默认等待 5000ms 让 AE 收敛后自动拍摄。各路线 AE 独立，互不干扰。

### `--ev` 参数的性质

`--ev` 是 AE 的**偏置**（bias），告诉 AE 算法目标亮度偏移 N 档，AE 本身仍在自适应运行。与锁定曝光（`--shutter --gain`）根本不同。

---

## 七、分辨率与 zoom 分析

### 采集分辨率

| 按键 | 配置 | 分辨率 | 说明 |
|---|---|---|---|
| — | preview（默认） | 4624×3472 | R4/R5/burst 保存此分辨率 |
| `p` | preview（切换） | 9248×6944 | 帧率极低，R4/R5 也随之升到全分辨率 |
| `g=FULL` | 保存 full | 9248×6944 | 影响 z/x/c/n 路线 |
| `g=HALF` | 保存 half | 4624×3472 | 影响 z/x/c/n 路线 |

### zoom 的工作原理与信息损耗

zoom 通过 Picamera2 的 `ScalerCrop` 控制 ISP 裁切区域：

```
zoom=1x：裁 9248×6944（全部）→ ISP 2x 缩小 → 输出 4624×3472
zoom=2x：裁 4624×3472（中央）→ ISP 1:1 → 输出 4624×3472（零损失！）
zoom=4x：裁 2312×1736       → ISP 2x 放大 → 输出 4624×3472（插值）
```

**zoom=2x 是信息无损的临界点**。R4/R5 按键保存的图像包含当前 zoom 的 ScalerCrop 效果。

---

## 八、对焦（LP）设计

`LensPosition`：0.0=无穷远，数值越大越近，上限约 16.0（约 9-10cm）。

实际部署：距离固定，LP 固定在 `INIT_LP`，AE 自动跟随环境变化。用 `calibration.py` 找最佳 LP。

---

## 九、Burst 与 EV Bracket 设计

### Burst（LP 扫描，按键 `u`）

用途：在当前 LP 附近扫描对焦，找最清晰的焦平面。

**设计**：直接用运行中的 preview 相机（不停止/重启），AE 已收敛，只改 LP：

```
LP: base-0.50 → base-0.25 → base+0.00 → base+0.25 → base+0.50
每次 LP 切换等 0.3s（镜头机械移动）
```

总耗时：5×0.3s = **1.5 秒**（无需 AE 收敛，preview 已稳定）。

### EV Bracket（曝光包围，按键 `y`）

用途：测试不同曝光偏置，找最合适的亮度。

**设计**：开新相机，以 `evs[0]=-1.0` 冷启动，AE 收敛后扫 5 个 EV：

```
EV: -1.0(冷启 8/5s) → -0.5(+4/1.5s) → 0.0 → +0.5 → +1.0
```

全分辨率冷启 8s/热调 4s；半分辨率冷启 5s/热调 1.5s。

---

## 十、按键速查

### 对焦 / 曝光 / 视角

| 按键 | 功能 |
|---|---|
| `= / -` | LP ±0.1（精细） |
| `] / [` | LP ±0.5（中等） |
| `. / ,` | LP ±1.0（粗调） |
| `e / w` | EV ±0.5 |
| `d / a` | Zoom 放大 / 缩小 |
| `i / k / j / l` | 平移（上/下/左/右） |
| `r` | 重置 zoom 到 1x 中心 |
| `` ` `` | ROI 全帧 |
| `1-5` | 2x 区域预设（中/四角） |
| `6-0` | 4x 区域预设（中/四角） |

### 系统控制

| 按键 | 功能 |
|---|---|
| `t` | 单次自动对焦（AF 完成后锁定 LP） |
| `p` | 切换 preview 采集分辨率（4624×3472 ↔ 9248×6944） |
| `g` | 切换 rpicam/DNG 保存分辨率（FULL ↔ HALF） |
| `h` | 切换 preview 后端（Picamera2 ↔ rpicam-still） |
| `f` | 打印当前完整状态到终端 |
| `q` | 退出 |

### 保存

| 按键 | 路线 | 说明 |
|---|---|---|
| `z` | R1 | rpicam-still → JPEG（g键分辨率，5s等待） |
| `x` | R2 | rpicam-still → PNG（g键分辨率，5s等待） |
| `c` | R3 | rpicam-still → DNG+JPEG（g键分辨率，5s等待） |
| `v` | R4 | Picamera2 → JPEG（**p键分辨率**，直接抓 preview，即时） |
| `b` | R5 | Picamera2 → PNG（**p键分辨率**，直接抓 preview，即时） |
| `n` | R6 | Picamera2 → DNG（g键分辨率，5s等待） |
| `m` | ALL | R4/R5先抓，再停机做R1~R3/R6，共享时间戳 |
| `u` | Burst | preview直接拍5张LP扫描（~1.5秒） |
| `y` | EV bracket | 新开相机5张EV包围（~11-24秒） |

---

## 十一、顶部超参数

```python
CAM_IDX   = 0       # 选择相机（0 或 1）
DISPLAY_W = 1280    # OpenCV 窗口宽度（改为 4624 可看全分辨率）
DISPLAY_H = 720     # OpenCV 窗口高度
STATUS_H  = 145     # 状态栏高度（像素）
INIT_LP   = 15.0    # 启动时初始焦距（靠近端）
```

---

## 十二、输出目录结构

所有文件输出到 `~/Desktop/images/preview_captures/cam{N}/`：

```
~/Desktop/images/preview_captures/cam0/
  ├── 20260619_120000_z_r1_rpicam_jpg_lp5.00_ev+0.0_cam0.jpg
  ├── 20260619_120000_x_r2_rpicam_png_lp5.00_ev+0.0_cam0.png
  ├── 20260619_120000_c_r3_rpicam_dng_lp5.00_ev+0.0_cam0.jpg   ← + 同名 .dng
  ├── 20260619_120000_v_r4_picam_jpg_lp5.00_ev+0.0_cam0.jpg    ← preview 分辨率
  ├── 20260619_120000_b_r5_picam_png_lp5.00_ev+0.0_cam0.png    ← preview 分辨率
  ├── 20260619_120000_n_r6_picam_dng_lp5.00_ev+0.0_cam0.dng
  ├── 20260619_120001_burst_lp4.75_ev+0.0_cam0.png              ← burst 5张
  └── 20260619_120015_bracket_lp5.00_ev-1.0_brk0_cam0.png       ← EV bracket 5张
```

---

## 十三、快速上手

```bash
# 1. 安装依赖（Rpi5，第一次）
sudo apt update
sudo apt install -y python3-picamera2 rpicam-apps
sudo apt install python3-opencv python3-matplotlib

# 2. 确认相机识别
rpicam-hello --list-cameras

# 3. 启动
cd code
python3 preview_focus_hybrid.py   # 默认 CAM_IDX=0

# 4. 对焦
# · 按 t 触发自动对焦
# · 用 ][ 粗调，=- 精调
# · 按 u 做 burst LP 扫（~1.5秒，即时），找最清晰的焦平面
# · 写入顶部 INIT_LP

# 5. 对比采集
# · 按 m 一次拍 6 张（覆盖全部路线）
# · DNG 文件用 darktable 打开，可关闭 NR 后再看
```

---

## 十四、已知限制与注意事项

1. **z/x/c/n 每张需等待 5 秒**：rpicam-still 和 DNG 路线需要 AE 收敛，正常现象
2. **v/b 保存分辨率 = preview 分辨率**：由 `p` 键决定，`g` 键对 v/b 无效
3. **v/b 包含当前 zoom 效果**：放大后保存的图像也是放大视角的内容
4. **DNG（路线 R6）**：`request.save_dng()` 在 Bookworm 上已验证可用
5. **颜色格式**：本机 `RGB888` 实际返回 BGR 数据，换机器出现颜色反转时在 `grab_frame()` 加 `cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)`
6. **zoom > 2x 是插值**：超过 2x 后 ISP 开始放大，看到的不是更多传感器像素
7. **`p` 键切换到 9248×6944 preview**：每帧 ~193MB，帧率 1-2fps
