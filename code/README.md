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

### 为什么是 Rpi5 而不是 Rpi4

OV64A40 传感器全分辨率每帧约 **193MB**。在 Rpi4 上，Picamera2 使用 `buffer_count × 帧大小` 的内存，直接 `capture_array()` 会触发 OOM killer。因此 Rpi4 项目（见 `../64mp/`）必须绕道 `rpicam-still` 子进程拍照。

**Rpi5 内存更大，已验证可直接通过 `Picamera2.capture_array()` 获取 9248×6944 帧而不 OOM**，因此本项目可以：
- 用 Picamera2 做预览（capture_array → OpenCV → imshow）
- 用 Picamera2 做高质量保存（still configuration + capture_array）
- 同时保留 rpicam-still 作为对比后端

---

## 三、预览架构：为什么用 OpenCV 而不是 QTGL

原始 `64mp/cam_test/preview_focus_hybrid.py` 使用 Picamera2 的 QTGL 预览窗口。改用 OpenCV 显示的原因：

1. **可以在画面上方叠加状态栏**：QTGL 窗口由 Picamera2 内部管理，无法在其上绘制状态信息；OpenCV 窗口完全可控，可以用 `np.vstack([status_bar, frame])` 把信息栏拼在画面上方
2. **键盘焦点在 GUI 上**：QTGL 方案需要用 `termios/tty` 把终端切换成 raw 模式读键盘；OpenCV 的 `cv2.waitKey()` 直接在窗口捕获按键，更自然
3. **Rpi5 上 `capture_array()` 帧率足够**：使用 mid 分辨率（2312×1736）做预览，resize 到显示尺寸，帧率满足实时操作需求

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
    ↓ 输出到 main stream（BGR888 / RGB888）
    ↓ 格式压缩（视保存方式）
    ↓ 文件
```

ISP 是**不可逆**的，一旦完成就无法还原 raw 数据。

### 三种格式的信息保留对比

| 格式 | ISP 影响 | 额外损耗 | 适用场景 |
|---|---|---|---|
| **JPEG** | 有 | **有（8×8 DCT 块压缩）** | 文件小，快速浏览 |
| **PNG** | 有 | **无（无损压缩）** | ISP 后零损失，所见即所保存 |
| **DNG** | **无** | **无** | 原始 Bayer 数据，后期自控 ISP |

**对 4px 虫头黑点的影响**：JPEG 的 8×8 DCT 块比目标特征更大，会导致边缘振铃（Gibbs 效应）、黑点变灰或出现彩色噪点，即使 quality=95 也有损。PNG 保留 ISP 输出的完整像素值，推荐用于对比实验。DNG 可以在后期用 darktable/Lightroom 关闭 NR 再检查细节。

### rpicam-still 与 Picamera2 的 ISP 差异

两者底层使用同一块 ISP 硬件，但 tuning 参数不同：
- `create_preview_configuration` / `create_still_configuration` 使用 Picamera2 的调优参数
- `rpicam-still` 使用 rpicam-apps 自带的调优参数

这意味着**同一场景下两条管线的颜色、锐度、NR 程度不完全相同**——这正是做对比实验的意义所在。

### 颜色通道格式

经实测，本机 Picamera2 的 `capture_array()` 在配置 `"format": "RGB888"` 时实际返回 **BGR 排列的数据**（通道 0 = B，命名是反直觉的）。OpenCV 的 `imshow` 和 `imwrite` 均期望 BGR，因此代码中**不做任何颜色转换**直接使用。

---

## 五、6 条保存路线设计

### 设计动机

为了全面比较不同调用路径和格式的成像差异，设计了 6 条路线，覆盖：
- **调用方式**：rpicam-still 子进程 vs Picamera2 直接调用
- **格式**：JPEG（有损）/ PNG（ISP 后无损）/ DNG（完全无损，跳过 ISP）

### 路线清单

| 按键 | 路线 | 调用方式 | 格式 | ISP 管线 |
|---|---|---|---|---|
| `z` | R1 | rpicam-still | JPEG | rpicam-apps 参数 |
| `x` | R2 | rpicam-still | PNG | rpicam-apps 参数 |
| `c` | R3 | rpicam-still | DNG + JPEG | rpicam-apps 参数；DNG 为原始 Bayer |
| `v` | R4 | Picamera2 | JPEG | Picamera2 参数 |
| `b` | R5 | Picamera2 | PNG | Picamera2 参数 |
| `n` | R6 | Picamera2 | DNG | **无 ISP**（raw Bayer） |
| `m` | 全部 | 依次执行 R1→R6 | 各一张 | — |

### 文件命名规则

```
{时间戳}_{按键}_{路线}_{方式}_{格式}_lp{LP}_ev{EV}_cam{N}.{ext}
例：20260618_120000_z_r1_rpicam_jpg_lp5.00_ev+0.0_cam0.jpg
```

按 `m` 时 6 张共享同一时间戳，便于横向对比。

---

## 六、曝光（AE）设计思路

### 为什么不锁定曝光

早期版本在保存时从 Picamera2 preview 读取已收敛的快门时间和增益（`ExposureTime` / `AnalogueGain`），传给 rpicam-still 的 `--shutter --gain`。

但这破坏了对比实验的独立性：rpicam-still 会使用 Picamera2 的曝光设置，而不是自己的 ISP 算法计算出的最优曝光。**现在的设计让每条路线独立完成自己的 AE 收敛**。

### rpicam-still 的收敛方式

`rpicam-still` 不传 `-t` 参数时，**默认等待 5000ms（5 秒）**让 AE 收敛，然后自动拍摄退出。代码中不传 `-t`，让 rpicam-still 使用自己的默认机制。这是最干净的方式。

### Picamera2 的收敛等待

Picamera2 没有"AE 收敛完成"的回调，只能等待固定时间。`PICAM_SETTLE_S = 5.0` 与 rpicam-still 默认 5s 对齐，确保对比实验两边收敛时间一致。

### `--ev` 参数的性质

`--ev` 是 AE 的**偏置**（bias），告诉 AE 算法"目标亮度往亮/暗偏移 N 档"，但 AE 本身仍在运行和自适应。这与 `--shutter --gain`（锁死曝光参数）是根本不同的概念：

- `--ev`：我要拍亮一点，但相机自己决定怎么实现
- `--shutter --gain`：我指定用 1/100s + ISO400 拍，相机不能自由调整

`--ev` 是用户的创作/技术参数，应该传给各路线；`--shutter --gain` 不传（让各路线自主决定）。

---

## 七、分辨率与 zoom 分析

### 采集分辨率

| 配置 | 分辨率 | 说明 |
|---|---|---|
| Preview（日常） | 2312×1736 (mid) | 预览帧率足够，resize 到 DISPLAY_W×DISPLAY_H 显示 |
| Preview（可切换） | 9248×6944 (full) | 按 `p` 切换，帧率极低，用于检查全分辨率细节 |
| 保存（full mode） | 9248×6944 | `g` 键切换，save_full=True |
| 保存（half mode） | 4624×3472 | save_full=False |

### zoom 的工作原理与信息损耗

zoom 通过 Picamera2 的 `ScalerCrop` 控制，告诉 ISP 只采集传感器的某个区域：

```
zoom=1x：裁 9248×6944（全部）→ ISP 2x 缩小 → 输出 4624×3472
zoom=2x：裁 4624×3472（中央）→ ISP 1:1 → 输出 4624×3472（零损失！）
zoom=4x：裁 2312×1736       → ISP 2x 放大 → 输出 4624×3472（插值）
```

**zoom=2x 是信息无损的临界点**。大于 2x 之后 ISP 开始插值放大，看到的是"放大的像素"而非更多传感器信息。

### DISPLAY_W 的意义

`DISPLAY_W` 决定 OpenCV 窗口里显示的宽度（默认 1280）。改成 4624 则等于取消 resize，看到 ISP 输出的原始像素，无额外缩放损失，但窗口会超出屏幕，需手动拖小。

---

## 八、对焦（LP）设计

`LensPosition`：
- `0.0` = 无穷远
- 数值越大 = 对焦距离越近
- 上限约 16.0（对应约 9-10cm 最近对焦距离）
- 启动时从 `cam.camera_controls["LensPosition"]` 读取实际上限

**实际部署场景**：相机与被摄虫头之间的距离固定，LP 固定在标定出的最佳值，AE 自动跟随环境光线变化。`calibration.py` 可以通过粗扫（step=0.5）+ 精扫（±1.0，step=0.1）找出使 Laplacian 方差最大的最佳 LP。

---

## 九、Burst 与 EV Bracket 设计

### Burst（LP 扫描，按键 `u`）

用途：在当前 LP 附近扫描对焦，找最清晰的焦平面。

**设计**：使用 Picamera2，AE **收敛一次（5s）**，然后快速切换 5 个 LP 值：

```
base-0.50, base-0.25, base+0.00, base+0.25, base+0.50
每次 LP 切换等 0.3s（镜头机械移动），AE 保持不变
```

总耗时：5s（收敛）+ 5×0.3s = **6.5 秒**。AE 全程一致，只有 LP 在变，对焦对比公平。

若改用 rpicam-still：每张 5s × 5 = 25 秒，且每次 AE 从零收敛，曝光可能微差。

### EV Bracket（曝光包围，按键 `y`）

用途：在当前 LP 下测试不同曝光偏置，找最合适的亮度。

**设计**：使用 Picamera2，以 `evs[0]=-1.0` 冷启动收敛（5s），第一张直接拍，后续每次 EV 切换等 1.5s：

```
EV: -1.0(冷启+5s) → -0.5(+1.5s) → 0.0(+1.5s) → +0.5(+1.5s) → +1.0(+1.5s)
```

总耗时：5s + 4×1.5s = **11 秒**。

EV 切换是**热调整**（AE 已在运行，仅调整目标亮度偏置），不需要完整的 5s 冷启动，1.5s 足够重新收敛到新的 EV 目标。

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
| `p` | 切换预览采集分辨率（4624×3472 ↔ 9248×6944） |
| `g` | 切换保存分辨率（FULL 64MP ↔ HALF 16MP） |
| `h` | 切换预览后端（Picamera2 ↔ rpicam-still） |
| `f` | 打印当前完整状态到终端 |
| `q` | 退出 |

### 保存

| 按键 | 路线 | 说明 |
|---|---|---|
| `z` | R1 | rpicam-still → JPEG |
| `x` | R2 | rpicam-still → PNG |
| `c` | R3 | rpicam-still → DNG + JPEG（raw Bayer）|
| `v` | R4 | Picamera2 → JPEG |
| `b` | R5 | Picamera2 → PNG |
| `n` | R6 | Picamera2 → DNG（raw Bayer）|
| `m` | ALL | 依次执行 R1→R6，共享时间戳 |
| `u` | Burst | Picamera2 LP 扫 5 张（PNG），AE 收敛一次 |
| `y` | EV bracket | Picamera2 EV 包围 5 张（PNG） |

---

## 十一、顶部超参数

文件顶部所有参数均可直接修改，无需理解代码内部逻辑：

```python
CAM_IDX        = 0       # 选择相机（0 或 1）
DISPLAY_W      = 1280    # OpenCV 窗口宽度（改为 4624 可看全分辨率）
DISPLAY_H      = 720     # OpenCV 窗口高度
STATUS_H       = 145     # 状态栏高度（像素）
INIT_LP        = 15.0    # 启动时初始焦距（靠近端）
PICAM_SETTLE_S = 5.0     # Picamera2 still AE 收敛等待时间（与 rpicam-still 默认 5s 对齐）
```

---

## 十二、输出目录结构

所有文件输出到 `~/Desktop/images/preview_captures/cam{N}/`：

```
~/Desktop/images/preview_captures/cam0/
  ├── 20260618_120000_z_r1_rpicam_jpg_lp5.00_ev+0.0_cam0.jpg
  ├── 20260618_120000_x_r2_rpicam_png_lp5.00_ev+0.0_cam0.png
  ├── 20260618_120000_c_r3_rpicam_dng_lp5.00_ev+0.0_cam0.jpg   ← + 同名 .dng
  ├── 20260618_120000_v_r4_picam_jpg_lp5.00_ev+0.0_cam0.jpg
  ├── 20260618_120000_b_r5_picam_png_lp5.00_ev+0.0_cam0.png
  ├── 20260618_120000_n_r6_picam_dng_lp5.00_ev+0.0_cam0.dng
  ├── 20260618_120006_burst_lp4.75_ev+0.0_cam0.png              ← burst 5张
  └── 20260618_120020_bracket_lp5.00_ev-1.0_brk0_cam0.png       ← EV bracket 5张
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
python3 -c "from picamera2 import Picamera2; print(Picamera2.global_camera_info())"

# 3. 启动
cd code
python3 preview_focus_hybrid.py        # 默认 CAM_IDX=0

# 4. 对焦流程
# · 按 t 触发自动对焦，获得初始 LP
# · 用 ] [ 粗调，= - 精调
# · 按 u 做 burst LP 扫，找最清晰的焦平面
# · 确认 LP 后写入顶部 INIT_LP

# 5. 对比采集
# · 按 m 一次拍 6 张（覆盖全部路线）
# · 把文件拷到 Mac 用 darktable / Preview 对比细节
# · DNG 文件用 darktable 打开，可关闭 NR 后再看
```

---

## 十四、已知限制与注意事项

1. **rpicam-still 每张需 5 秒**：按 `z/x/c` 或 `m` 时需等待，正常现象
2. **DNG（路线 R6）**：`request.save_dng()` 依赖 Picamera2 版本，在较新版本（Bookworm 上的包）已验证可用
3. **颜色格式**：本机 Picamera2 `RGB888` 实际返回 BGR 数据，如换机器出现颜色反转，在 `grab_frame()` 加 `cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)` 即可
4. **zoom > 2x 是插值**：zoom 超过 2x 后 ISP 开始放大，看到的不是更多传感器像素，而是放大后的像素
5. **`p` 键切换到 9248×6944 预览**：每帧 ~193MB，帧率会降到 1-2fps，仅用于最终质量核查
