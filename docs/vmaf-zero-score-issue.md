# VMAF 零分帧问题排查记录

## 问题描述

使用 `vmaf_compare.sh` 对 Bilibili 下载的 AVC 视频与 YouTube 参考视频进行 VMAF 质量对比时，发现大量帧（约 15%）得分为 0，但肉眼观察这些帧与参考帧几乎一致。

## 环境信息

| 项目 | 值 |
|------|-----|
| ffmpeg | N-122395-g48c9c38684-20260109 |
| libvmaf | version=vmaf_v0.6.1 |
| 测试视频 | BV1Q6W5eLEye (电子天使) |
| 参考视频 | Electric_Angel_ref.mp4 |
| 分辨率 | 1920x1080, 60fps, yuv420p, h264 |
| 总帧数 | 11064 |

## 问题现象

- AVC/HEVC/AV1 三个编码的零分帧**位置完全一致**（如 296, 299, 302, 305...）
- 零分帧严格**每隔 3 帧出现一次**
- 三个编码共 1724 帧 VMAF=0（占 15.6%）

## 排查过程

### 1. 排除画质差异

提取 frame 296 的 PNG 图片进行像素级对比：

| 指标 | 值 |
|------|-----|
| SSIM | 0.94 |
| PSNR | 32.77 dB |
| Y 平面像素一致率 | 53% |
| Y 平面 mean_diff | 1.68 |

结论：帧本身质量正常，不是画质问题。

### 2. 排除帧错位假设

对比 distorted[N] vs reference[N-1] 和 distorted[N] vs reference[N+1]：

| 比较 | PSNR | SSIM |
|------|------|------|
| distorted[296] vs ref[296]（正确对齐） | **35.52dB** | **0.9932** |
| distorted[296] vs ref[295]（移位 -1） | 20.25dB | 0.7684 |
| distorted[296] vs ref[297]（移位 +1） | 20.10dB | 0.7632 |

结论：正确对齐时质量很高，简单的帧移位不是原因。

### 3. 分析 PTS 时间戳差异

两个视频的原始 PTS：

| | AVC (distorted) | Reference |
|---|---|---|
| time_base | 1/16000 | 1/15360 |
| PTS 间距 | 272 或 256 交替（17ms/16ms） | 恒定 256（16.667ms） |
| 模式 | 每 3 帧一个周期：[272, 272, 256] | 均匀 |

AVC 使用**整数毫秒量化**（16ms 和 17ms 交替），而 Reference 使用**精确 1/60 秒**。

逐帧 PTS 差值（零分帧附近）：

| 帧 | AVC PTS | Ref PTS | 差值 | VMAF |
|----|---------|---------|------|------|
| 293 | 4.883000 | 4.883333 | -0.333ms | 2.10 (低) |
| 294 | 4.900000 | 4.900000 | 0.000ms | 90.07 |
| 295 | 4.917000 | 4.916667 | +0.333ms | 77.93 |
| **296** | **4.933000** | **4.933333** | **-0.333ms** | **0.00** |
| 297 | 4.950000 | 4.950000 | 0.000ms | 89.47 |
| 298 | 4.967000 | 4.966667 | +0.333ms | 78.27 |
| **299** | **4.983000** | **4.983333** | **-0.333ms** | **0.00** |

**规律：pts_diff = -0.333ms 的帧全部是零分或低分帧。**

差值在 -0.333 / 0 / +0.333ms 之间振荡，从不累积。

### 4. 排除 `settb=AVTB` 修复

使用 `settb=AVTB,setpts=PTS-STARTPTS` 后结果**完全不变**：

```
ffmpeg -i distorted.mp4 -i reference.mp4 \
  -lavfi "[0:v]settb=AVTB,setpts=PTS-STARTPTS[dist];\
          [1:v]settb=AVTB,setpts=PTS-STARTPTS[ref];\
          [dist][ref]libvmaf=..."
```

原因：`settb=AVTB` 只是将每路输入的 time base 设为各自的平均帧时长——AVC 仍然是 1/16000，Ref 仍然是 1/15360，两路 time base 没有统一。

### 5. 根因定位

**framesync 默认 `ts_sync_mode=default`**，使用 "nearest lower or equal timestamp" 配对帧。

当 AVC 的 PTS 略小于 Reference（-0.333ms）时：
- distorted 帧 PTS = 4.933000
- reference 帧 PTS = 4.933333
- framesync 选择 ≤ 4.933000 的最近 reference 帧 → 选择了 frame 295（PTS=4.916667）
- 导致 VMAF 比较的是 distorted[296] vs reference[295]，帧对完全错误

### 6. VMAF 内部指标佐证

零分帧的子指标：

| 指标 | 正常帧 (297) | 零分帧 (296) |
|------|-------------|-------------|
| adm2 | 0.9348 | 0.5898 |
| vif_scale0 | 0.5672 | 0.1823 |
| **motion** | **13.79** | **0.00** |

`motion=0` 表明 VMAF 收到的 distorted 连续帧内容相同（重复帧），这是 framesync 错误配对的直接后果。

## 修复方案

### 方案 A：`ts_sync_mode=nearest`（推荐）

修改 libvmaf 参数，使用 "absolute nearest timestamp" 配对：

```bash
ffmpeg -i distorted.mp4 -i reference.mp4 \
  -lavfi "[0:v]setpts=PTS-STARTPTS[distorted];\
          [1:v]setpts=PTS-STARTPTS[reference];\
          [distorted][reference]libvmaf=log_fmt=json:log_path=output.json:ts_sync_mode=nearest" \
  -f null -
```

### 方案 B：强制统一 PTS

用 `setpts=N/(60*TB)` 按固定帧率重设 PTS，消除时间戳偏差：

```bash
ffmpeg -i distorted.mp4 -i reference.mp4 \
  -lavfi "[0:v]format=yuv420p,settb=AVTB,setpts=N/(60*TB)[dist];\
          [1:v]format=yuv420p,settb=AVTB,setpts=N/(60*TB)[ref];\
          [dist][ref]libvmaf=log_fmt=json:log_path=output.json" \
  -f null -
```

两种方案效果完全一致，任选其一。

## 测试验证结果

| 方案 | VMAF mean | Zero-VMAF | Frame 296 |
|------|-----------|-----------|-----------|
| 原始写法 | 64.61 | 66 | 0.00 |
| settb=AVTB | 64.61 | 66 | 0.00 |
| **ts_sync_mode=nearest** | **91.28** | **4** | **92.13** |
| **setpts=N/(60*TB)** | **91.28** | **4** | **92.13** |

## 影响范围

此问题影响所有使用 ffmpeg libvmaf 滤镜对比以下视频的情况：
- 两路输入的 time_base 不同
- PTS 间距存在量化差异（如整数毫秒 vs 精确分数）
- 差异导致 framesync 在默认模式下错误配对帧

常见场景：
- Bilibili 下载视频 vs YouTube 视频
- 不同编码器/封装工具输出的视频对比
- VFR（可变帧率）视频对比

## 更新 vmaf_compare.sh

在 libvmaf 参数中添加 `ts_sync_mode=nearest`：

```bash
ffmpeg -i "$DISTORTED" -i "$REF" \
        -lavfi "[0:v]setpts=PTS-STARTPTS[distorted];
                [1:v]setpts=PTS-STARTPTS[reference];
                [distorted][reference]libvmaf=log_fmt=json:log_path=${OUTPUT}:ts_sync_mode=nearest" \
        -f null - 2>&1 | tail -n 5
```
