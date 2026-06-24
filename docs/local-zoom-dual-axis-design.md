# Local Zoom 双坐标轴改造方案

## 背景

当前 Local Zoom 和 Per-frame VMAF 都在展示主 VMAF 曲线，职责有重叠。改造后，Per-frame VMAF 保持为主 VMAF 总览图；Local Zoom 改为细节视图，只展示 VMAF 子指标，用双纵坐标轴处理不同值域。

子指标值域依据见 [sub_metric_ranges.md](sub_metric_ranges.md)。

## 目标

- Local Zoom 仅展示子指标，不展示 Primary VMAF，也不展示 `vmaf*` 模型分数。
- Local Zoom 仍受 `metricToggles` 控制指标显隐。
- Local Zoom 仍受 Per-frame VMAF 区域的 `videoLegend` 控制视频文件显隐。
- 使用双纵坐标轴区分 `0-1.x` 归一化子指标和非归一化子指标。
- Motion 与 PSNR 使用严格互斥策略，不在同一个右轴同时展示。
- 初始状态自动选择每个重点系列的代表指标，使 Detail View 打开后立即有可分析曲线。

## 指标分类

前端使用显式 metric metadata 作为唯一分轴依据，不根据当前加载数据的 min/max 临时推断 family。

| 指标模式 | family | axisGroup | yAxisIndex | 单位/范围 |
| --- | --- | --- | --- | --- |
| `integer_adm*` | `adm` | `normalized` | `0` | 约 `0-1.1` |
| `integer_vif*` | `vif` | `normalized` | `0` | 约 `0-1.1` |
| `integer_aim*` | `aim` | `normalized` | `0` | 约 `0-1.0` |
| `integer_motion*` | `motion` | `raw` | `1` | Motion 值 |
| `psnr_*` | `psnr` | `raw` | `1` | dB，常见上限 `60` |

`_egl_1` 变体不用单独 fallback。它们仍以 `integer_adm`、`integer_vif`、`integer_aim` 开头，会自然归入左轴 `normalized`。

## 坐标轴行为

左轴固定为 `normalized` 轴：

- 展示 ADM、VIF、AIM 及其 EGL 变体。
- 轴标题建议为 `ADM / VIF / AIM`。
- 默认范围建议为 `0-1.1`，保留 ADM/VIF 观测到略大于 `1.0` 的情况。

右轴为 `raw` 轴：

- 只展示一个 raw family。
- 选择 Motion 指标时，右轴标题为 `Motion`。
- 选择 PSNR 指标时，右轴标题为 `PSNR (dB)`。
- Motion 和 PSNR 严格互斥，避免不同单位共用同一个右轴造成误读。

## Toggle 交互

- Toggle 列表只显示 metadata 可识别的子指标。
- `vmaf`、`vmaf_hd`、`vmaf_hd_neg`、`vmaf_hd_phone`、`vmaf_4k` 等模型分数不进入 Local Zoom toggle。
- normalized 指标之间可以多选。
- raw 指标同 family 内可以多选，例如多个 `integer_motion*`，或多个 `psnr_*`。
- 当已经选择 Motion 时，再点击 `psnr_y` 等 PSNR 指标，应清空已选 Motion 指标并选中该 PSNR 指标；反向同理。
- 这种“点击另一组即切换”的行为比禁用另一组更顺手，也避免用户必须先手动取消。

## 默认选择

新 comparison 加载后，Detail View 应按系列优先级自动选择代表指标。每个系列独立取第一个同时满足以下条件的指标：

- 存在于当前比较文件的 shared metrics 中。
- 能被 `metricMeta(metric)` 识别。
- 不违反 Motion/PSNR 的 raw family 互斥规则。

默认优先级：

```js
const DEFAULT_DETAIL_METRICS = {
  adm: [
    "integer_adm2",
    "integer_adm_scale0",
    "integer_adm_scale1",
    "integer_adm_scale2",
    "integer_adm_scale3",
  ],
  vif: [
    "integer_vif_scale0",
    "integer_vif_scale1",
    "integer_vif_scale2",
    "integer_vif_scale3",
  ],
  motion: [
    "integer_motion2",
    "integer_motion",
  ],
};
```

默认状态通常会选中一个 ADM、一个 VIF、一个 Motion。ADM/VIF 进入左轴 `normalized`；Motion 进入右轴 `raw`，右轴标题为 `Motion`。PSNR 不默认选中；用户点击任意 `psnr_*` 后，清空已选 Motion 指标并切换右轴为 `PSNR (dB)`。

## 前端呈现

坐标轴归属不只靠颜色表达：

- 颜色继续表示视频文件，沿用现有 `videoLegend` 语义。
- 子指标 chip 使用“chip 内短标签”方案，在指标名旁增加 `0-1`、`motion`、`dB` 等紧凑标签。
- 左右轴标题明确显示当前轴含义。
- tooltip 中带出指标 family 或单位，例如 `integer_vif_scale2 · 0-1`、`psnr_y · dB`。

线型可以继续用于区分主图和细节指标，或用于弱化子指标曲线，但不作为唯一的坐标轴提示。

## 未知指标策略

未知指标采用 fail closed 策略：

- `metricMeta(metric)` 返回 `null` 时，不显示 toggle，也不绘制到 Local Zoom。
- 不把未知指标自动塞进 `raw` 右轴。
- observed range 只用于校验或微调轴范围，不用于决定 family。
- 如果以后确认某个新指标有分析价值，再显式加入 metadata。

这样可以保证用户看到的 Local Zoom 指标都是前端已经理解过的指标，避免单位和值域被错误混合。

## 实现提示

建议集中实现一个 metadata 函数，供 toggle 渲染、series 生成、tooltip、yAxis 配置共同使用。

```js
function metricMeta(metric) {
  if (metric.startsWith("integer_adm")) return { family: "adm", axisGroup: "normalized", yAxisIndex: 0, unit: "" };
  if (metric.startsWith("integer_vif")) return { family: "vif", axisGroup: "normalized", yAxisIndex: 0, unit: "" };
  if (metric.startsWith("integer_aim")) return { family: "aim", axisGroup: "normalized", yAxisIndex: 0, unit: "" };
  if (metric.startsWith("integer_motion")) return { family: "motion", axisGroup: "raw", yAxisIndex: 1, unit: "" };
  if (metric.startsWith("psnr_")) return { family: "psnr", axisGroup: "raw", yAxisIndex: 1, unit: "dB" };
  return null;
}
```

后续实现时，Local Zoom 的 series 应只来自 metadata 可识别的 active extra metrics，并为每条 series 设置对应的 `yAxisIndex`。默认选择逻辑应基于 `DEFAULT_DETAIL_METRICS` 和 `sharedMetrics()` 生成初始 active metrics，而不是依赖 API 返回顺序。

## 验收点

- Per-frame VMAF 只显示主 VMAF 曲线，不受 Local Zoom 子指标 toggle 影响。
- Local Zoom 不显示 Primary VMAF，也不显示 `vmaf*` 模型分数。
- ADM/VIF/AIM/EGL 变体在左轴显示。
- 新 comparison 默认选中第一个可用 ADM、第一个可用 VIF、第一个可用 Motion。
- PSNR 不默认选中。
- Motion 与 PSNR 在右轴显示，且二者互斥。
- 点击不同 raw family 的 toggle 会自动切换 raw family。
- 未知指标不出现在 Local Zoom toggle 中。
- tooltip 和 chip 能明确表达指标属于哪个坐标轴或单位。
