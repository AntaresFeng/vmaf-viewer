# VMAF 子指标值域报告

基于 `videos/` 目录下 20 个 VMAF JSON 文件的实际数据统计。

## 汇总表

### VMAF 模型分数

| 指标 | 观测 min | 观测 max | 理论范围 | 文件数 | 说明 |
|------|----------|----------|----------|--------|------|
| `vmaf` | 0.000 | 100.000 | 0–100 | 8 | 默认模型 |
| `vmaf_hd` | 43.588 | 100.000 | 0–100 | 6 | HD 模型 |
| `vmaf_hd_neg` | 42.288 | 100.000 | 0–100 | 6 | HD NEG 模型 |
| `vmaf_hd_phone` | 63.559 | 100.000 | 0–100 | 6 | HD Phone 模型 |
| `vmaf_4k` | 81.337 | 100.000 | 0–100 | 6 | 4K 模型 |

### ADM (Average Distortion Metric)

| 指标 | 观测 min | 观测 max | 文件数 | 备注 |
|------|----------|----------|--------|------|
| `integer_adm2` | 0.353 | 1.011 | 20 | 观测到 >1.0 |
| `integer_adm3` | 0.428 | 1.000 | 19 | / |
| `integer_adm_scale0` | 0.660 | 1.027 | 20 | 观测到 >1.0 |
| `integer_adm_scale1` | 0.486 | 1.041 | 20 | 观测到 >1.0 |
| `integer_adm_scale2` | 0.270 | 1.015 | 20 | 观测到 >1.0 |
| `integer_adm_scale3` | 0.182 | 1.017 | 20 | 观测到 >1.0 |

### ADM EGL 变体

| 指标 | 观测 min | 观测 max | 文件数 |
|------|----------|----------|--------|
| `integer_adm2_egl_1` | 0.790 | 0.995 | 6 |
| `integer_adm3_egl_1` | 0.839 | 0.996 | 5 |
| `integer_adm_scale0_egl_1` | 0.658 | 0.996 | 6 |
| `integer_adm_scale1_egl_1` | 0.608 | 0.993 | 6 |
| `integer_adm_scale2_egl_1` | 0.724 | 0.995 | 6 |
| `integer_adm_scale3_egl_1` | 0.841 | 0.997 | 6 |

### VIF (Visual Information Fidelity)

| 指标 | 观测 min | 观测 max | 文件数 | 备注 |
|------|----------|----------|--------|------|
| `integer_vif_scale0` | 0.112 | 1.000 | 20 | / |
| `integer_vif_scale1` | 0.177 | 1.012 | 20 | 观测到 >1.0 |
| `integer_vif_scale2` | 0.180 | 1.019 | 20 | 观测到 >1.0 |
| `integer_vif_scale3` | 0.155 | 1.019 | 20 | 观测到 >1.0 |

### VIF EGL 变体

| 指标 | 观测 min | 观测 max | 文件数 |
|------|----------|----------|--------|
| `integer_vif_scale0_egl_1` | 0.260 | 1.000 | 6 |
| `integer_vif_scale1_egl_1` | 0.559 | 1.000 | 6 |
| `integer_vif_scale2_egl_1` | 0.668 | 1.000 | 6 |
| `integer_vif_scale3_egl_1` | 0.742 | 1.000 | 6 |

### Motion

| 指标 | 观测 min | 观测 max | 文件数 | 说明 |
|------|----------|----------|--------|------|
| `integer_motion` | 0.000 | 109.298 | 20 | 有上限，float 版默认 10000 |
| `integer_motion2` | 0.000 | 67.746 | 20 | 有上限，float 版默认 10000 |
| `integer_motion3` | 0.000 | 67.746 | 19 | 有上限，float 版默认 10000 |

### AIM (Adaptive Information Metric)

| 指标 | 观测 min | 观测 max | 文件数 |
|------|----------|----------|--------|
| `integer_aim` | 0.000 | 0.717 | 19 |
| `integer_aim_egl_1` | 0.003 | 0.118 | 5 |

### PSNR (Peak Signal-to-Noise Ratio)

| 指标 | 观测 min | 观测 max | 单位 | 文件数 | 说明 |
|------|----------|----------|------|--------|------|
| `psnr_y` | 23.153 | 60.000 | dB | 12 | 亮度分量 |
| `psnr_cb` | 30.654 | 60.000 | dB | 12 | 色度 Cb 分量 |
| `psnr_cr` | 29.027 | 60.000 | dB | 12 | 色度 Cr 分量 |

## 值域特征总结

| 类别 | 典型范围 | 上限特征 |
|------|----------|----------|
| VMAF 分数 | 0–100 | 硬上限 100（VMAF 模型输出截断） |
| ADM 系列 | 0–~1.04 | 理论 1.0，可配置 `adm_enhn_gain_limit` 允许超过 |
| VIF 系列 | 0–~1.02 | 理论 1.0，可配置 `vif_enhn_gain_limit` 允许超过 |
| Motion 系列 | 0–~110 | 有上限（float 版默认 `motion_max_val=10000`），integer 版上限未公开 |
| AIM 系列 | 0–~0.72 | 理论 1.0 |
| PSNR 系列 | 0–60 dB | FFmpeg libvmaf 滤镜截断（8-bit: 60 dB, 10-bit: 72 dB） |
| EGL 变体 | 与基础指标类似 | 未观测到 >1.0 |

## 关于 ADM/VIF 超过 1.0 的说明

libvmaf 提供两个可配置参数控制增强增益上限：
- `adm_enhn_gain_limit`：默认值 "1"
- `vif_enhn_gain_limit`：默认值 "1"

可通过 `vmaf_feature_dictionary_set` 设为更大值（如 "1.2"）。本数据集中观测到的 >1.0 值可能与使用的模型配置有关，不一定是定点运算误差。

> 参考：[Netflix/vmaf test_model.c](https://github.com/Netflix/vmaf/blob/master/libvmaf/test/test_model.c)

## 关于 PSNR 60 dB 上限的说明

PSNR 上限 60 dB 来自 FFmpeg 的 libvmaf 滤镜（`vf_libvmaf.c`），而非 libvmaf 核心库：
- 8-bit 内容：上限 60 dB
- 10-bit 内容：上限 72 dB

本数据集中三个 PSNR 指标最大值**恰好都是 60.000 dB**，与该行为一致。

> 参考：[Netflix/vmaf#1109](https://github.com/Netflix/vmaf/issues/1109)

## 关于 Motion 上限的说明

`float_motion.c` 中有明确的默认上限 `motion_max_val = 10000.0`。`integer_motion.c` 中也存在上限，但具体数值未在公开文档中找到。本数据集中观测最大值为 109.298，远低于 float 版上限。

> 参考：[float_motion.c](https://github.com/Netflix/vmaf/blob/master/libvmaf/src/feature/float_motion.c)

## 备注

- `_egl_1` 变体仅出现在 5–6 个文件中（使用了 EGL 模型的视频）。
- ADM/VIF 的 `scale0`–`scale3` 是不同空间尺度下的子指标，scale0 为最粗尺度。
- `integer_motion` / `integer_motion2` / `integer_motion3` 是不同版本的运动估计指标，motion2/3 使用了改进算法。
- `pooled_metrics` 中的 min/max 是文件级聚合（所有帧的极值），非逐帧数据。
