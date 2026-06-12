# easyVmaf fps 滤镜的 PTS 归一化副作用

## 概述

easyVmaf 在 `_autoDeinterlace()` 步骤中，即使两路视频帧率完全相同，也会为每路添加 `fps=fps=<N>` 滤镜。这个滤镜的一个**副作用**是会重建输出帧的 PTS 时间戳，从而无意中消除了两路输入因 time_base 不同导致的 PTS 量化偏差，掩盖了 libvmaf `framesync` 默认模式下的帧错配问题。

## 问题背景

### PTS 量化偏差

不同编码器/封装工具产生的视频，即使帧率相同，也可能使用不同的 time_base：

| 来源 | time_base | PTS 间距 | 模式 |
|------|-----------|----------|------|
| Bilibili AVC (distorted) | 1/16000 | 272/272/256 交替 | 整数毫秒量化 (16ms/17ms) |
| YouTube Reference | 1/15360 | 恒定 256 | 精确 1/60 秒 |

这种差异导致逐帧 PTS 存在 ±0.333ms 的振荡偏差。

### libvmaf framesync 默认行为

libvmaf 的 `ts_sync_mode=default` 使用 "nearest lower or equal timestamp" 策略配对帧：

- 当 distorted PTS 略小于 reference PTS（差 -0.333ms）时
- framesync 选择 ≤ distorted PTS 的最近 reference 帧
- 结果：distorted[N] 被错误地与 reference[N-1] 配对
- 表现：VMAF=0，motion=0（收到重复帧内容）

### 问题表现

| 指标 | 值 |
|------|-----|
| 零分帧位置 | 从 frame 296 开始，每隔 3 帧出现 |
| 零分帧数量 | 约 15%（11064 帧中 1724 帧） |
| 零分帧 motion | 0.00（表示收到重复帧） |
| VMAF mean（受影响） | 64.61 |
| VMAF mean（修复后） | 91.28 |

## fps 滤镜的作用机制

### FFmpeg 官方文档

> Convert the video to specified constant frame rate by duplicating or dropping frames as necessary.
> — FFmpeg Documentation, Section 11.98 fps

fps 滤镜的核心功能是将视频转换为指定的恒定帧率（CFR）。其实现方式：

1. 根据目标帧率计算每个输出帧的理想 PTS：`PTS_out = frame_number / target_fps`
2. 从输入帧中选择最接近理想 PTS 的帧
3. 输出帧的 PTS 由输出 link 的 time_base 和帧序号决定

### PTS 归一化效果

当 `fps=fps=60` 应用于两路输入时：

```
输入 AVC:  time_base=1/16000, PTS=[0, 272, 528, 800, ...]  (272/272/256 交替)
输入 Ref:  time_base=1/15360, PTS=[0, 256, 512, 768, ...]  (恒定 256)

经过 fps=60 后:
输出 AVC:  PTS 按 1/60 秒均匀间距重建
输出 Ref:  PTS 按 1/60 秒均匀间距重建
```

两路输出的 PTS 都基于相同的目标帧率重新量化，消除了原始 time_base 差异。

## 实验验证

### 测试环境

| 项目 | 值 |
|------|-----|
| 视频 | BV1Q6W5eLEye-AVC_proxy.mp4 / Electric_Angel_ref_proxy.mp4 |
| 分辨率 | 1920x1080, 60fps |
| 时长 | 10 秒 / 600 帧 |
| AVC time_base | 1/16000 |
| Ref time_base | 1/15360 |
| 生成方式 | `-c copy` 流复制（保留原始 PTS） |

### 对照实验

| 条件 | VMAF mean | 零分帧 | Frame 296 |
|------|-----------|--------|-----------|
| 不加 fps 滤镜（仅 setpts=PTS-STARTPTS） | **65.04** | **62 个** | **0.00** |
| 加 fps 滤镜（fps=fps=60） | **91.91** | **0** | **92.13** |

零分帧位置（无 fps 滤镜时）：296, 299, 302, 305, 308, 311, 314, 317, 320, 323, 326, 329, 332, 335, 338, 341, 344, 347, 350, 353...

规律：从 frame 296 开始，每隔 3 帧一个零分帧，与 PTS 量化周期（272/272/256 三帧一循环）完全吻合。

## 影响分析

### easyVmaf 的行为

在 `vmaf.py:_applyDeinterlaceFilters()` 中：

```python
if self.ref.interlaced == self.main.interlaced:
    # 两边帧率相同时，仍然各加一个 fps 滤镜
    qos.main.setFpsFilter(round(main_fps, 5))
    qos.ref.setFpsFilter(round(ref_fps, 5))
```

这段代码的原始意图是"锁定帧率"，但实际效果多了一个 PTS 归一化。

### 谁会受影响

**不受影响的场景**（大多数用户）：
- 通过 easyVmaf CLI 正常使用 → fps 滤镜自动应用 → PTS 问题被掩盖

**可能受影响的场景**：
1. 使用 `-fps 0` 或修改代码跳过 `_autoDeinterlace()` → fps 滤镜不应用 → PTS 问题暴露
2. 直接用 FFmpeg 命令行调用 libvmaf，不加 fps 滤镜 → PTS 问题暴露
3. 其他不加 fps 滤镜的 VMAF 工具 → PTS 问题暴露

### 不应依赖此副作用

虽然 fps 滤镜恰好解决了 PTS 问题，但这属于**无意的副作用**，不应作为设计意图依赖：

1. **fps 滤镜有额外开销** — 帧率转换需要计算和可能的帧丢弃/复制
2. **语义不明确** — 代码意图是帧率对齐，实际效果包含了 PTS 归一化
3. **不保证未来版本行为一致** — FFmpeg fps 滤镜的 PTS 处理可能随版本变化
4. **当帧率真正不同时** — fps 滤镜会做真正的帧率转换，此时 PTS 归一化是预期行为的一部分

## 建议的显式修复方案

### 方案 A：添加 ts_sync_mode=nearest（推荐）

在 libvmaf 参数中显式指定 `ts_sync_mode=nearest`，让 libvmaf 使用"绝对最近时间戳"策略配对帧，而非默认的"最近下界"策略。

```python
# ffmpeg.py getVmaf() 的 base_params 中追加
base_params = (
    f'log_fmt={log_fmt}'
    f':model={model_str}'
    f':n_subsample={subsample}'
    f':log_path={_esc(log_path)}'
    f':n_threads={threads}'
    f':shortest={shortest}'
    f':ts_sync_mode=nearest'          # ← 新增
)
```

优点：
- 一行改动，语义明确
- 是 libvmaf 原生支持的参数
- 不依赖 fps 滤镜的副作用
- 即使将来跳过 fps 滤镜，PTS 问题也能正确处理

### 方案 B：显式统一 PTS

在滤镜链中添加 `setpts=N/(FRAME_RATE*TB)`，强制按帧序号重建 PTS：

```python
# 在 setOffset() 中，即使 offset==0 也添加 PTS 归一化
if self.offset == 0:
    fps = getFrameRate(self.main.streamInfo['r_frame_rate'])
    self.ffmpegQos.main.setForcePts(fps)
    self.ffmpegQos.ref.setForcePts(fps)
```

需要在 `inputFFmpeg` 中新增 `setForcePts()` 方法。

## 参考

- [FFmpeg fps filter documentation](https://ffmpeg.org/ffmpeg-filters.html#fps)
- [VMAF 零分帧问题排查记录](vmaf-zero-score-issue.md)
- libvmaf `ts_sync_mode` 参数：`default`（nearest lower or equal）vs `nearest`（absolute nearest）
  > ```bash
  > ffmpeg -hide_banner -h filter=libvmaf
  > ```
