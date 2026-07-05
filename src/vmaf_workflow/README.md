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
scp videos/video10/.workflow/video10-inputs.tar 3080:~/vmaf_compare/
scp videos/video10/.workflow/remote-plan.sh 3080:~/vmaf_compare/
```

在 3080 上执行：

```bash
ssh 3080
cd ~/vmaf_compare
bash remote-plan.sh
```

脚本会解包 `video10-inputs.tar`，逐个 distorted 文件调用 easyVmaf，然后打包结果：

```text
video10-json.tar.gz
```

回到本机拉回结果并解压：

```bash
scp 3080:~/vmaf_compare/video10-json.tar.gz videos/
tar -xzf videos/video10-json.tar.gz -C videos
```

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

## 当前边界

- 已自动化：下载、参考视频登记、媒体清单、输入打包、远端执行脚本生成。
- 尚未自动化：scp 上传、ssh 执行、结果拉回、结果完整性检查。
- `remote-plan.sh` 是可审核脚本；运行前可以先打开查看。
- `ffprobe` 缺失或探测失败不会阻塞 `prepare`，但 4K/HD 模型选择会更多依赖文件名中的 `4K` 或 `2160p`。

## 常见错误

- `package` 报 `media-inventory.json is required`：先运行 `prepare`。
- `remote-plan` 报 `package-manifest.json is required`：先运行 `package`。
- `prepare` 报 reference destination already exists：项目目录里已有同名文件，手动改名或直接指定目录内那个参考文件。
- viewer 没有看到结果：确认 `videoN-json.tar.gz` 是用 `tar -xzf ... -C videos` 解压，JSON 应该进入 `videos/videoN/`。
