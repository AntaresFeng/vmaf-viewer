# B站固定水印识别研究

## 范围

本研究阶段最初只验证水印识别算法。独立脚本位于
`devscripts/explore_watermark_detection.py`，所有诊断产物由调用者指定目录，
建议放在仓库外的临时目录。算法现已提取到
`src/vmaf_workflow/watermark_detection.py`，独立脚本和主流水线共用同一实现。

已知样本：

- `videos/video4`：参考 `TougenRenka.mp4`，B站源 `BV1Rt7w6CEVB-*`
- `videos/video6`：参考 `brain rot.mp4`，B站源 `BV1jm756EEzH-*`
- `videos/video7`：参考 `saihate.mp4`，B站源 `BV1zoV86XEcq-*`

## 当前算法

当前实现是有参考视频时的监督式固定覆盖物检测，不依赖文件名或 workflow
manifest：

1. 在视频时长的 8% 到 92% 之间均匀抽取 9 帧。
2. 使用 FFmpeg 将 distorted 和 reference 缩放为同一分析分辨率，默认
   `960x540`。
3. 每个 distorted 采样点在 reference 的前一帧、同一帧、后一帧中选择
   中央画面残差最小的帧，容忍约一个 60 fps 帧的时间偏差。三个连续的
   reference 候选帧由一次 FFmpeg 调用批量读取；最多同时处理 4 个采样点，
   汇总结果仍保持原采样时间顺序。
4. 用中央画面的稳健中位数和 MAD 估计全局亮度、对比度与编码噪声，得到
   标准化的正向亮度残差。白色半透明水印相对干净参考通常表现为持续正残差。
5. 对 9 帧残差取逐像素中位数，同时计算超过阈值的帧频率。
6. 只在画面四周 24% 的边缘带内搜索；当前门槛是中位数残差至少 3 个稳健
   标准差，并且至少 56% 的采样帧命中。
7. 对命中像素做小范围形态学闭运算和膨胀，将投稿者名与 bilibili logo
   合并为一个可复核候选框。

这不是 bilibili logo 模板匹配。它先利用干净参考验证“固定水印可以稳定从编码
残差中分离”这一前提，并为后续无参考模板检测提供可靠的水印位置和模板训练
样本。

## 使用方法

```powershell
uv run python devscripts/explore_watermark_detection.py `
  --distorted "videos\video6\BV1jm756EEzH-1080P 高帧率-AVC.mp4" `
  --reference "videos\video6\brain rot.mp4" `
  --output-dir "C:\tmp\vmaf-watermark-research\video6"
```

主要输出：

- `summary.json`：探测参数、逐采样点时间对齐结果和候选框
- `contact-sheet.png`：原帧、残差热力图、候选框三联图
- `candidate-overlay.png`：候选框叠加图
- `positive-z.png`：多帧持续正向残差
- `absolute-residual.png`：绝对残差
- `frequency.png`：逐像素命中频率
- `candidate-mask.png`：阈值后的原始候选像素

## 2026-07-18 实验结果

统一使用默认参数和 `960x540` 分析分辨率。

| 数据 | 正样本 | 命中 | 第一候选 |
| --- | ---: | ---: | --- |
| video4，1080p AVC/AV1/HEVC | 3 | 3 | 左上，`(12,16,163,36)` |
| video6，1080p/4K AVC/HEVC | 4 | 4 | 右上，约 `(789,16,159,36)` |
| video7，1080p/4K AVC/AV1/HEVC | 6 | 6 | 右上，约 `(789,16,159,36)` |

总计 13/13 个已知 B站变体命中。每个正样本都只有一个候选，且第一候选完整
覆盖投稿者名和 bilibili logo。同一项目的归一化位置在分辨率和编码格式之间
保持稳定。

负样本分别取三个项目的 YouTube 1080p AVC 编码，与相同参考比较；3/3 均无
候选通过当前阈值。

## 当前结论

- 在这三类内容上，多帧持续正向残差比单帧绝对差分可靠。画面本身有大量编码
  和缩放残差，但它们很少在同一边缘位置连续 9 个采样点保持同方向和强度。
- 必须保留一帧左右的局部时间搜索。三个项目实际选择过 `0` 或 `+1/60s`，
  video6 的部分帧还选择过 `-1/60s`。
- 候选框应覆盖整个平台覆盖物，而不只是 bilibili 图形；当前形态学分组可以把
  可变投稿者名和固定 logo 合并。
- 当前 13 个正样本只包含三个不同视频内容，不能据此确定最终阈值或宣称泛化
  完成。

## 2026-07-19 主流水线集成

有参考模式已作为 `prepare` 的内部步骤接入：

- 每个含 BVID 的项目只检测唯一的 B站 1080p H.264 代表文件。
- `present` 写入项目级归一化 exclusion；`absent` 继续全画面评分；两个及以上
  候选为 `uncertain`，保留诊断并阻断。
- 所有媒体先按真实 decoded 分辨率生成审计框；remote-plan 再按 easyVmaf 的
  HD/4K 强制拉伸生成最终 `drawbox`。
- package 对 inventory 和 analysis summary 记录 SHA-256，只上传 summary，不上传
  PNG；status、remote-plan 和远端 provenance 都记录或校验评分范围。
- 无参考识别、手工 bbox override 和 Viewer UI 展示不属于这次实现。

真实 `videos/video6` 验收仍得到右上角分析框 `(789,16,159,36)`，候选分数
`540.621499`。加入安全边距后，HD 排除框为 `(1570,24,334,88)`，4K 排除框为
`(3140,48,668,176)`；项目内全部 8 条比较命令使用相应目标框。
