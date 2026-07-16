# VMAF Download Workflow

本目录说明 `vmaf-workflow` 的下载与 VMAF 任务准备流程。示例命令按 Git Bash 写法；仓库根 README 不记录这套本地工作流。

## 环境假设

- 当前仓库：`D:\BiliDown\vmaf_compare`
- 本机下载器：
  - `D:\BiliDown\BBDown.exe`
  - `D:\YTDown\yt-dlp.exe`
- 本机可用：`uv`、`ffprobe`、`tar`、`scp`、`ssh`
- 远端 3080 上有 easyVmaf，默认路径：`/home/fzx/easyVmaf`

这些默认值在 `src/vmaf_workflow/config.py` 中配置。

## 一条完整流程

先进入仓库：

```bash
cd /d/BiliDown/vmaf_compare
```

下载 B 站和 YouTube 同一视频的不同版本：

```bash
uv run vmaf-workflow download \
  --bvid BV1i7jc6BEwf \
  --ytid Xiap0npVRCE
```

默认会创建下一个 `videos/videoN`。如果测试或重跑时要复用已有目录，显式指定：

```bash
uv run vmaf-workflow download \
  --project-dir videos/video10 \
  --bvid BV1i7jc6BEwf \
  --ytid Xiap0npVRCE
```

登记参考视频并生成媒体清单：

```bash
uv run vmaf-workflow prepare \
  --project-dir videos/video10 \
  --reference "/d/Downloads/reference.mp4"
```

如果参考视频不在 `videoN` 内，命令会复制一份到项目目录；如果已经在目录内，则只登记。支持扫描 `.mp4`、`.webm`、`.mkv`、`.mov`，并排除 `.workflow` 和 `.yt-dlp-temp`。

打包待上传输入：

```bash
uv run vmaf-workflow package --project-dir videos/video10
```

默认输出：

```text
videos/video10/.workflow/video10-inputs.tar
```

生成远端 easyVmaf 执行计划：

```bash
uv run vmaf-workflow remote-plan --project-dir videos/video10
```

如果远端 easyVmaf 不在默认路径：

```bash
uv run vmaf-workflow remote-plan \
  --project-dir videos/video10 \
  --easyvmaf-repo /home/fzx/easyVmaf
```

该命令只生成计划，不执行 ssh/scp。输出：

```text
videos/video10/.workflow/remote-plan.json
videos/video10/.workflow/remote-plan.sh
```

上传输入包和脚本到 3080：

```bash
uv run vmaf-workflow upload --project-dir videos/video10
```

默认目标来自 `RemoteSettings`：SSH 主机别名 `3080`，远端目录
`/home/fzx/vmaf_compare`。只在 upload 阶段允许覆盖并固化到
`remote-state.json`：

```bash
uv run vmaf-workflow upload \
  --project-dir videos/video10 \
  --host other-gpu \
  --remote-dir /srv/vmaf_compare
```

upload 先上传小脚本并运行 `--environment-only`，确认 FFmpeg、libvmaf、
easyVmaf 和 Git 分支正确后才上传大输入包；上传完成后再执行完整预检。
本地和远端 SHA-256 一致时跳过重复传输。

前台执行远端 VMAF：

```bash
uv run vmaf-workflow run --project-dir videos/video10
```

输出会实时显示并写入 `.workflow/remote-run.log`。脚本会解包输入、逐个
调用 easyVmaf、验证每个预期 JSON，并生成 `video10-json.tar.gz`。

拉回、验证并自动安装结果：

```bash
uv run vmaf-workflow fetch-results --project-dir videos/video10
```

fetch 会核对远端和本地 SHA-256、精确归档成员、普通文件类型和 JSON
可解析性。验证完成后，归档保存到 `.workflow`，JSON 安装到 `video10`。
它也可以接管手工运行后遗留的、但能通过当前计划校验的远端结果。

打开 viewer：

```bash
uv run vmaf-viewer --data-dir videos/video10
```

## 下载规则

B 站下载逻辑：

- 先用 BBDown 预检可用流。
- 选择平台画质标签为 1080P 及以上的流。
- 如果同一编码已有 `1080P 高帧率` 或 `1080P 高码率`，忽略对应的 `1080P 高清`。
- 对计划中的每个流重新预检并用 `-ia` 输入精确序号下载。
- 文件名模板为 `<bvid>-<dfn>-<videoCodecs>`。

YouTube 下载逻辑：

- yt-dlp 选择器为 `all[height>=1080][vcodec!=none][acodec=none]`。
- 输出模板为 `%(id)s-%(format_note)s-%(vcodec)s.%(ext)s`。
- 同时写入 `yt-dlp.after_video.jsonl` 和 `yt-dlp-infojson/`，用于记录实际下载的流。

## 生成文件

每个 `videos/videoN` 下会有一个 `.workflow` 目录：

```text
.workflow/
  bbdown.config
  yt-dlp.conf
  yt-dlp.preflight.raw.json
  yt-dlp.after_video.jsonl
  yt-dlp-infojson/
  manifest.json
  media-inventory.json
  package-manifest.json
  videoN-inputs.tar
  remote-plan.json
  remote-plan.sh
  remote-state.json
  remote-upload.log
  remote-run.log
  remote-fetch.log
  videoN-json.tar.gz
```

常用检查命令：

```bash
jq '.bilibili.downloads | length' videos/video10/.workflow/manifest.json
jq '.youtube.downloads | length' videos/video10/.workflow/manifest.json
jq '.files[] | {path, role, resolution, codec}' videos/video10/.workflow/media-inventory.json
jq '.commands[] | {model, distorted: .distorted.path}' videos/video10/.workflow/remote-plan.json
```

## 重跑策略

默认下载会新建下一个 `videos/videoN`。重跑同一个任务时用 `--project-dir`：

```bash
uv run vmaf-workflow download \
  --project-dir videos/video10 \
  --bvid BV1i7jc6BEwf \
  --ytid Xiap0npVRCE
```

这样不会创建新目录，并且让 BBDown/yt-dlp 自己跳过已存在的下载文件。`prepare`、`package`、`remote-plan` 都必须显式传 `--project-dir`。

## 远程状态

- `upload`、`run`、`fetch-results` 通过 `.workflow/remote-state.json` 传递
  主机、目录、哈希和阶段状态。
- `run` 和 `fetch-results` 不接受主机覆盖；目标只能由 upload 固化。
- `remote-plan.json` 变化后必须重新 upload，避免运行或拉回过期计划。
- `run` 是前台流式命令，Ctrl+C 会把状态记录为 `interrupted`。
- fetch 只替换计划内的 JSON，不删除项目目录中的其他 JSON。
- `remote-plan.sh` 是可审核脚本；运行前可以先打开查看。
- `remote-plan.sh --environment-only` 不检查输入包，用于大文件上传前验证环境。
- `remote-plan.sh --preflight-only` 只检查远端环境和输入包，不解包、不运行 VMAF。
- FFmpeg/FFprobe 最低主版本在 `EasyVmafSettings.ffmpeg_min_major` 中配置，默认是 5。
- easyVmaf 期望分支在 `EasyVmafSettings.required_branch` 中配置，默认是 `master`。
- 远端非交互 SSH 环境必须自行提供正确的 `PATH` 和动态库搜索路径。
- `ffprobe` 缺失或探测失败不会阻塞 `prepare`，但 4K/HD 模型选择会更多依赖文件名中的 `4K` 或 `2160p`。

## 常见错误

- `package` 报 `media-inventory.json is required`：先运行 `prepare`。
- `remote-plan` 报 `package-manifest.json is required`：先运行 `package`。
- `remote-plan` 提示 package manifest 与 inventory 不一致：媒体清单在打包后发生变化，重新运行 `package`。
- `upload` 提示远端计划或 package 漂移：重新运行 `package` 和 `remote-plan`。
- 远端预检提示 FFmpeg 版本过低或缺少 `libvmaf`：修正 3080 非交互 Bash 环境后重新预检。
- 远端预检提示 easyVmaf 分支不匹配：手动确认仓库状态并切换到配置要求的分支，脚本不会自动切换。
- `run` 或 `fetch-results` 提示 remote plan changed：重新运行 `upload`。
- `fetch-results` 提示归档成员不匹配：保留远端文件排查，现有本地结果不会被替换。
- `prepare` 报 reference destination already exists：项目目录里已有同名文件，手动改名或直接指定目录内那个参考文件。
- viewer 没有看到结果：确认 `videoN-json.tar.gz` 是用 `tar -xzf ... -C videos` 解压，JSON 应该进入 `videos/videoN/`。

## 手工排障

自动化失败时可以直接使用 state 中记录的主机和目录：

```bash
scp videos/video10/.workflow/video10-inputs.tar 3080:/home/fzx/vmaf_compare/
scp videos/video10/.workflow/remote-plan.sh 3080:/home/fzx/vmaf_compare/
ssh 3080 "cd /home/fzx/vmaf_compare && bash remote-plan.sh --preflight-only"
ssh 3080 "cd /home/fzx/vmaf_compare && bash remote-plan.sh"
```
