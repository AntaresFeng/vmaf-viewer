# `#messages` 常驻状态栏设计

## 背景

`#messages` 目前在比较加载完成且没有 warning 时会被清空。空容器没有稳定高度，导致下方 Summary 和图表区域上移；用户看到的是 loading 消失、图表跳动。

## 目标

- `#messages` 始终常驻显示，避免加载前后布局高度变化。
- 加载完成后显示聚合结果：`已加载 N 个文件，共同比较帧 X 帧`。
- 保留现有 warning 和 error 的可见性。
- 不改变比较接口、图表数据加载策略或 summary 表格行为。

## 状态文案

- 有文件但未选择：`请选择 1-6 个 VMAF JSON 文件进行比较。`
- 无文件：`未找到 *_vmaf.json 文件。`
- 加载中：`正在加载比较数据...`
- 加载成功：`已加载 N 个文件，共同比较帧 X 帧`
- 加载成功且有 warning：先显示加载成功消息，再显示 warning。
- 加载失败：显示错误消息。

## 数据来源

加载成功状态从 `/api/compare` 返回的 `summary` 派生：

- `N` 使用 `summary.length`。
- `X` 使用第一条 summary 的 `common_frames`。
- 如果 `summary` 为空或缺少帧数，则回退为 `0`。

## 前端实现边界

- 将 `renderMessages` 调整为始终渲染至少一条消息，或通过专门的状态 helper 传入默认消息。
- `requestComparison` 成功后不再传空数组清空 `#messages`；改为渲染加载成功消息，并追加 warnings。
- `applyFilesResponse`、无选择状态和错误状态继续通过同一消息入口更新。
- CSS 为 `.messages` 提供稳定的最小高度，使不同状态切换时下方内容不跳动。

## 验证

- `node --check src/vmaf_viewer/static/app.js`
- `uv run pytest -q`
- 手动检查：选择 JSON 文件后，loading 出现；加载结束后 `#messages` 保留并显示加载文件数和共同比较帧数；Summary 和图表不因状态区清空而上移。
