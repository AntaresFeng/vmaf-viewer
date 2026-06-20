# 设计/功能

- [ ] Local zoom 功能和 per-frame VMAF 功能略重复，需彻底区分。
  - per-frame VMAF 仅展示而且总是 VMAF 曲线，其他子指标不展示，不受下面子指标选择按钮控制。但仍然受到 videoLegend 按钮控制
  - Local Zoom -> Detail View，仅展示子指标，使用双纵坐标轴，区分值域是 0-1 的指标和其他指标。依旧受metricToggles控制，受per-frame VMAF的videoLegend 按钮。
- [x] 用帧计数做 x 轴的图表，tooltip 的标题是帧数，但有两位小数 .00 ，去掉
- [ ] 用帧计数做 x 轴的图表，现在只能显示帧数，我希望加入时间显示，在tooltip上同时显示帧数和时间时间。因为vmaf json里没有记录视频fps的字段，所有需要用户手动输入。在Thresholds左侧加入 FPS [<输入框>]。如果输入值不合法fps，则不生效。如果合法，tooltop标题应该是 示例："256 00:04.16"(帧256是第4秒第16帧) "216,001 1:00:00.01"(帧216001=60*3600+1 是整一小时第1帧)（fps=60为例子）