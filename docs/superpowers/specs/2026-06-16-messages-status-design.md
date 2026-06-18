# `#messages` 常驻状态栏设计

## 背景

`#messages` 目前在比较加载完成且没有 warning 时会被清空。空容器没有稳定高度，导致下方 Summary 和图表区域上移；用户看到的是 loading 消失、图表跳动。

## 目标

- `#messages` 始终常驻显示，避免加载前后布局高度变化。
- `#messages` 始终只显示一个消息项，避免多条消息改变状态区高度。
- warning 和 error 优先显示；只有没有 warning/error 等更重要消息时，才用加载成功消息兜底。
- 加载完成且无 warning/error 时显示聚合结果：`Loaded N files, X common frames.`
- 所有界面显示文本使用英文，和现有 viewer UI 保持一致。
- 不改变比较接口、图表数据加载策略或 summary 表格行为。

## 状态文案

- 有文件但未选择：`Select 1-6 VMAF JSON files to compare.`
- 无文件：`No *_vmaf.json files found.`
- 加载中：`Loading comparison data...`
- 加载成功且无 warning/error：`Loaded N files, X common frames.`
- 加载成功但有 warning：显示第一条 warning，不显示加载成功消息。
- 加载失败：显示错误消息，不显示加载成功消息。

## 消息优先级

`#messages` 由单一消息选择逻辑驱动，每次渲染只生成一个 `.message` 元素：

1. error：用户操作失败、接口失败或异常捕获到的错误消息。
2. warning：`/api/compare` 返回的第一条 warning。
3. loading/status：加载中、无文件、未选择等当前操作状态。
4. success fallback：加载成功且没有 error/warning/status 时的聚合结果。

如果后端返回多条 warning，本次设计只显示第一条 warning，保持单消息规则不变。后续如果需要完整 warning 列表，应设计独立的详情入口，而不是把多条 warning 堆进 `#messages`。

## 数据来源

加载成功状态从 `/api/compare` 返回的 `summary` 派生：

- `N` 使用 `summary.length`。
- `X` 使用第一条 summary 的 `common_frames`。
- 如果 `summary` 为空或缺少帧数，则回退为 `0`。

## 前端实现边界

- 将 `renderMessages` 调整为接收单条消息对象，或新增 `renderMessage` / `setMessageState` helper 统一选择并渲染单条消息。
- `requestComparison` 成功后不再传空数组清空 `#messages`；先根据 error/warning/status/success fallback 优先级选出唯一消息，再渲染。
- `applyFilesResponse`、无选择状态和错误状态继续通过同一消息入口更新。
- CSS 为 `.messages` 提供稳定的最小高度，使不同状态切换时下方内容不跳动。

## 验证

- `node --check src/vmaf_viewer/static/app.js`
- `uv run pytest -q`
- 手动检查：选择 JSON 文件后，`Loading comparison data...` 出现；加载结束且没有 warning/error 时，`#messages` 保留并显示 `Loaded N files, X common frames.`；Summary 和图表不因状态区清空而上移。
- 手动检查：加载结束但有 warning 时，`#messages` 只显示一条 warning，不显示加载成功消息；Summary 和图表不因消息切换而移动。
