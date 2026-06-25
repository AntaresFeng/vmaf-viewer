# 设计/功能

- [ ] Local zoom 功能和 per-frame VMAF 功能略重复，需彻底区分。
  - per-frame VMAF 仅展示而且总是 VMAF 曲线，其他子指标不展示，不受下面子指标选择按钮控制。但仍然受到 videoLegend 按钮控制
  - Local Zoom -> Detail View，仅展示子指标，使用双纵坐标轴，区分值域是 0-1 的指标和其他指标（指标的值域见[text](sub_metric_ranges.md)）。依旧受metricToggles控制，受per-frame VMAF的videoLegend 按钮控制。改造方案见[local-zoom-dual-axis-design.md](local-zoom-dual-axis-design.md)。
  - 暂时不处理：zoom后按需加载相关问题
