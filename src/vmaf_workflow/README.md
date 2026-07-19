# VMAF Workflow

本目录说明 `vmaf-workflow` 从下载、任务准备、远端计算、结果回收到本地清理的
完整流程。示例中的多行命令按 Git Bash 写法；PowerShell 请写成单行或将续行符
改为反引号。仓库根 README 不记录这套本地工作流。

## TL;DR

推荐直接启动交互式自动流程；`auto` 是 `interactive` 的等价别名：

```bash
uv run vmaf-workflow interactive
uv run vmaf-workflow auto
```

TUI 会收集 B站、YouTube 和参考视频输入，然后自动执行全部阶段。下面是与其等价
的手工命令；将项目目录、来源 ID 和参考视频路径替换为实际值：

```bash
cd /d/BiliDown/vmaf_compare

project=videos/video10
reference="/d/Downloads/reference.mp4"

uv run vmaf-workflow download --project-dir "$project" --bvid BV1i7jc6BEwf --ytid Xiap0npVRCE
uv run vmaf-workflow prepare --project-dir "$project" --reference "$reference"
uv run vmaf-workflow package --project-dir "$project"
uv run vmaf-workflow remote-plan --project-dir "$project"
uv run vmaf-workflow upload --project-dir "$project"
uv run vmaf-workflow run --project-dir "$project"
uv run vmaf-workflow fetch-results --project-dir "$project"
uv run vmaf-workflow cleanup --project-dir "$project"
uv run vmaf-workflow status --project-dir "$project"

uv run vmaf-viewer --data-dir "$project"
```

`download` 至少提供 `--bvid` 或 `--ytid` 之一；两者都提供时会下载到同一个项目。
`cleanup` 只删除已经验证、且与现有媒体和 JSON 重复的归档，不删除媒体文件或
已安装的 `*_vmaf.json`。

## 用户指南

本节面向运行工作流的用户，说明环境要求、各阶段操作、产物、重跑方式和常见
错误。实现细节和手工 SSH 排障见后面的[开发者参考](#开发者参考)。

### 交互式自动流程

启动新任务：

```bash
uv run vmaf-workflow interactive
```

也可以使用短别名：

```bash
uv run vmaf-workflow auto
```

设置页接受 B站 URL/BVID、YouTube URL/视频 ID 和参考视频路径。两个视频来源至少
填写一个；确认后会自动运行 download、prepare、package、remote-plan、upload、
run、fetch-results、cleanup 和最终 status。cleanup 默认开启，可在开始前关闭。

恢复已有项目时可以在界面选择 `videoN`，或启动时直接传入：

```bash
uv run vmaf-workflow interactive --project-dir videos/video10
uv run vmaf-workflow auto --videos-dir videos --project-dir videos/video10
```

恢复流程从本地状态判断最早未完成步骤；已经完成的步骤不会重复执行。若上次状态
仍为 `running`，界面不会自动重跑，必须先确认没有遗留远端任务。失败后可以重试
当前步骤；取消会终止当前下载器或 SSH 子进程，远程阶段记录为 interrupted/130。
已有项目按站点锁定已绑定的来源身份；如果只绑定了一个站点，另一个站点仍可填写，
确认后会先执行 `download` 补齐来源，再从 `prepare` 继续。空恢复项目或缺少后续
所需参考视频时会在设置页直接提示，不会先启动下载器。

运行页固定展示步骤状态、返回码、单步耗时、总耗时和实时输出。每个阶段开始前
都会显示对应的手工 CLI 命令。完成后界面给出 viewer 命令，但不会自动启动服务。

### 环境假设

- 当前仓库：`D:\BiliDown\vmaf_compare`
- 本机下载器：
  - `D:\BiliDown\BBDown.exe`
  - `D:\YTDown\yt-dlp.exe`
- 本机可用：`uv`、`scp`、`ssh`
- 本机 `ffprobe` 可选；缺失时 `prepare` 仍可生成 inventory，但媒体元数据会减少
- 远端可用：Bash、`tar`、`sha256sum`、Git、FFmpeg、FFprobe
- 远端 FFmpeg 必须包含 `libvmaf`，默认最低主版本是 5
- 远端 3080 上有 easyVmaf 仓库，默认路径：`/home/fzx/easyVmaf`
- easyVmaf 仓库默认必须位于 `master` 分支；脚本只检查，不自动切换分支

这些默认值在 `src/vmaf_workflow/config.py` 中配置。

### 分步运行完整流程

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

两个来源可以单独下载；`download` 至少需要其中一个：

```bash
uv run vmaf-workflow download --bvid BV1i7jc6BEwf
uv run vmaf-workflow download --ytid Xiap0npVRCE
```

默认会创建下一个 `videos/videoN`。如果测试或重跑时要复用已有目录，显式指定：

```bash
uv run vmaf-workflow download \
  --project-dir videos/video10 \
  --bvid BV1i7jc6BEwf \
  --ytid Xiap0npVRCE
```

`--videos-dir <path>` 可以修改自动创建 `videoN` 的根目录。`--dry-run` 在新项目
中只生成下载器配置和基础 manifest，不调用下载器；已有 manifest 的项目只校验
来源 ID，且不修改任何文件。

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

`package --output <path>` 可以生成自定义位置的输入包，后续 upload 也会读取该
路径；但 `cleanup` 只管理 `.workflow/videoN-inputs.tar`，不会删除自定义输出。

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

模型选择以 distorted 媒体为准：`height >= 1600` 使用 `4K`，其余使用 `HD`；
缺少 height 时，文件名包含 `4K` 或 `2160p` 才使用 `4K`。参考视频与 distorted
分辨率不一致时，命令成功生成计划，但会将 warning 写到 stderr。并发线程仅由
`EasyVmafSettings.threads` 配置；默认 `None`，不会向 easyVmaf 添加 `-threads`。

上传输入包和脚本到 3080：

```bash
uv run vmaf-workflow upload --project-dir videos/video10
```

默认目标来自 `RemoteSettings`：SSH 主机别名 `3080`，远端目录
`/home/fzx/vmaf_compare`。该目录是基目录；实际执行目录为
`<base>/<videoN>/<remote-plan-sha256>/`，不同项目和不同计划不会共享
脚本、输入包或结果包。只在 upload 阶段允许覆盖基目录并固化到
`remote-state.json`：

```bash
uv run vmaf-workflow upload \
  --project-dir videos/video10 \
  --host other-gpu \
  --remote-dir /srv/vmaf_compare
```

upload 先上传小脚本并运行 `--environment-only`，确认 FFmpeg、libvmaf、
easyVmaf 和 Git 分支正确后才上传大输入包；上传完成后再执行完整预检。
本地和远端 SHA-256 一致时跳过重复传输。upload 还会生成并上传
`remote-provenance.json`，绑定当前 plan、package 和 script 的 SHA-256。

前台执行远端 VMAF：

```bash
uv run vmaf-workflow run --project-dir videos/video10
```

输出会实时显示并写入 `.workflow/remote-run.log`。脚本会解包输入、逐个
调用 easyVmaf、验证每个预期 JSON，并生成 `video10-json.tar.gz`。run
开始 preflight 前会重新核对远端 script、package 和 provenance 哈希。

拉回、验证并自动安装结果：

```bash
uv run vmaf-workflow fetch-results --project-dir videos/video10
```

fetch 会核对远端和本地 SHA-256、精确归档成员、普通文件类型和 JSON
可解析性，并要求归档内 provenance 与当前 plan/package 完全一致。
验证完成后，归档保存到 `.workflow`，JSON 事务性安装到 `video10`；
任一文件替换失败会恢复全部旧结果。它也可以接管手工运行后遗留的、
但带有当前 provenance 的远端结果。

结果确认可用后，可以删除 `.workflow` 中与现有媒体和 JSON 内容重复的两个大
归档：

```bash
uv run vmaf-workflow cleanup --project-dir videos/video10
```

`cleanup` 仅删除默认的 `video10-inputs.tar` 和 `video10-json.tar.gz`。它要求
upload 和 `fetch-results` 已完成，并在移动任何文件前验证：

- 两个归档的路径、大小和 SHA-256 与 state 一致。
- inputs tar 中每个媒体成员与 `videoN` 中当前媒体逐字节一致。
- 已安装 JSON 的集合、大小和内容与 manifest 及结果归档一致。

验证通过后，归档先重命名到 `.workflow` 内的 staging 路径，再删除。第二个归档
无法 staging 时会回滚第一个；staging 文件暂时无法删除时，state 记录
`cleanup.status = pending`，重新运行同一 cleanup 命令即可继续。成功后 state 和
manifest 保留归档路径、大小、SHA-256 与清理时间。已经清理的归档不会重复计入
本次释放量；cleanup 后重新 fetch 结果，也可以再次 cleanup 只删除新结果包。

任何阶段都可以查看本地工作流状态：

```bash
uv run vmaf-workflow status --project-dir videos/video10
```

输出当前阶段、状态、缺失产物和建议执行的下一条命令。例如 cleanup 完成后：

```text
project: videos/video10
stage: cleaned
state: completed
missing artifacts: none
next command: uv run vmaf-viewer videos/video10
```

`status` 是快速只读检查：它读取 manifest、inventory、plan 和
`remote-state.json`，并检查关键本地文件是否存在；不会连接 SSH、重新计算大型
归档的 SHA-256 或修改任何状态。严格的内容和哈希校验仍由 package、upload、
run、fetch-results 和 cleanup 在真正执行时完成。工作流尚未完成或某个远端阶段
失败时，status 仍返回 0，并建议从最早失效阶段继续；已有 JSON 损坏或项目目录
不存在时返回 2。

如果目录中的受支持媒体集合与 inventory 不一致，包括手工复制或补下载产生了
未登记媒体，status 会退回 `downloaded / incomplete` 并建议重新 `prepare`。

如果输入包由 `package --output` 生成，cleanup 按设计不会管理该自定义文件；status
在结果已 fetched 后会直接建议打开 viewer，而不会建议一个必然失败的 cleanup。

打开 viewer：

```bash
uv run vmaf-viewer --data-dir videos/video10
```

### 下载规则

每个 `videoN` 最多绑定一个 BVID 和一个 YouTube 视频 ID。缺失来源可以稍后补充，
同一 ID 可以重跑；向已有来源传入不同 ID 会在写文件和调用下载器前返回错误码 2。

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

### 生成文件

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
  videoN-inputs.tar        # package 后存在，cleanup 后删除
  remote-plan.json
  remote-plan.sh
  remote-state.json
  remote-provenance.json
  remote-upload.log
  remote-run.log
  remote-fetch.log
  videoN-json.tar.gz       # fetch 后存在，cleanup 后删除
```

常用检查命令：

```bash
uv run vmaf-workflow status --project-dir videos/video10
jq '.bilibili.downloads | length' videos/video10/.workflow/manifest.json
jq '.youtube.downloads | length' videos/video10/.workflow/manifest.json
jq '.files[] | {path, role, resolution, codec}' videos/video10/.workflow/media-inventory.json
jq '.commands[] | {model, distorted: .distorted.path}' videos/video10/.workflow/remote-plan.json
jq '{upload: .upload.status, run: .run.status, fetch: .fetch.status, cleanup: .cleanup.status}' videos/video10/.workflow/remote-state.json
```

### 重跑策略

默认下载会新建下一个 `videos/videoN`。向已有项目补充缺失站点时，只传对应来源
和原项目目录。例如已有 B 站文件后补 YouTube：

```bash
uv run vmaf-workflow download \
  --project-dir videos/video10 \
  --ytid Xiap0npVRCE
```

反向补充 B 站时只传 `--bvid`。重跑同一来源也使用相同 ID 和 `--project-dir`：

```bash
uv run vmaf-workflow download \
  --project-dir videos/video10 \
  --bvid BV1i7jc6BEwf
```

增量下载保留未参与本次调用的站点记录，本次站点更新为最新快照，顶层命令历史
继续追加。不同 BVID 或 YouTube ID 不允许混入同一项目。

任何已接受的非 dry-run 增量下载都会在调用下载器前使旧下游状态失效：删除旧
inventory、package manifest、默认 inputs tar、remote plan、remote state、
provenance 和默认结果 tar，并移除主 manifest 中对应指针。媒体、已安装的
`*_vmaf.json`、日志和自定义位置的 package 保留。即使下载器中途失败，下游状态
也保持失效，避免部分新文件继续搭配旧 inventory 使用。

因此补下载或重跑完成后必须从 `prepare` 重新开始，再依次执行 `package`、
`remote-plan` 和 `upload`。除首次 download 可自动创建目录外，后续所有命令都
必须显式传 `--project-dir`。

cleanup 后的重跑规则：

- viewer 直接读取已安装 JSON，不需要结果 tar。
- 再次运行 `fetch-results` 会恢复结果 tar 并重新验证、安装 JSON；之后可再次
  cleanup，只清理新下载的结果包。
- 要重新 upload 输入，先重新运行默认 `package`；如果 inventory 或 package
  发生变化，再依次运行 `remote-plan` 和 `upload`。
- 使用过 `package --output` 时，cleanup 会拒绝删除该自定义文件；请由操作者
  明确管理它的生命周期。

### 常见错误

- `status` 报某个 JSON 不是有效对象：该状态文件已损坏，先恢复或重新生成对应
  阶段；缺少尚未生成的 JSON 不属于损坏，status 会直接给出下一条命令。
- `package` 报 `media-inventory.json is required`：先运行 `prepare`。
- `remote-plan` 报 `package-manifest.json is required`：先运行 `package`。
- `remote-plan` 提示 package manifest 与 inventory 不一致：媒体清单在打包后发生变化，重新运行 `package`。
- `upload` 提示远端计划或 package 漂移：重新运行 `package` 和 `remote-plan`。
- 远端预检提示 FFmpeg 版本过低或缺少 `libvmaf`：修正 3080 非交互 Bash 环境后重新预检。
- 远端预检提示 easyVmaf 分支不匹配：手动确认仓库状态并切换到配置要求的分支，脚本不会自动切换。
- `run` 或 `fetch-results` 提示 remote plan changed：重新运行 `upload`。
- `run` 提示远端 SHA-256 不匹配：远端脚本、输入包或 provenance 被修改，
  重新运行 `upload`。
- `fetch-results` 提示 provenance 缺失或不匹配：结果不是由当前
  plan/package 生成，重新运行 `run`。
- `fetch-results` 提示归档成员不匹配：保留远端文件排查，现有本地结果不会被替换。
- `cleanup` 提示媒体内容与 inputs tar 不一致：输入包不是当前媒体的重复副本，
  保留归档并确认媒体为何变化。
- `cleanup` 提示只支持 default input archive：当前 upload 使用了
  `package --output`，cleanup 不会删除该自定义文件。
- `cleanup` 返回 pending 或 staging 文件被锁定：关闭占用文件的程序后，重新
  运行相同 cleanup 命令继续。
- `prepare` 报 reference destination already exists：项目目录里已有同名文件，手动改名或直接指定目录内那个参考文件。
- `download` 报 BVID 或 YouTube URL conflicts with existing project source：该
  `videoN` 已绑定另一个视频；使用原 ID 重跑，或为新视频创建新的项目目录。
- viewer 没有看到结果：检查 manifest 的 `results.files` 是否存在，并确认 viewer
  的数据目录是对应 `videos/videoN`；cleanup 后结果 tar 不存在是正常状态。

CLI 返回码约定：本地参数或状态错误为 `2`，SSH/SCP、远端执行或本地删除失败
为 `1`，远程 upload/run/fetch 被 Ctrl+C 中断为 `130`。`status` 对正常的未完成
或失败阶段返回 `0`，仅在项目不存在或已有 JSON 损坏时返回 `2`。

## 开发者参考

本节面向维护和排障人员，说明跨阶段状态协议、远端约束，以及绕过自动化时必须
自行承担的校验责任。

### 交互流水线内部

- `interactive` 是规范子命令，`auto` 通过 argparse aliases 路由到同一入口。
- TUI 和传统 CLI 共用下载服务及其余阶段函数，不通过子进程递归调用 CLI。
- 流水线阶段状态和耗时只保存在当前 TUI 会话；不会生成 pipeline-state 文件。
  跨会话恢复仍以 manifest、inventory、package、plan 和 remote-state 为准。
- `SubprocessRunner` 的默认无参数行为保持原样。TUI 注入输出回调后，下载命令分别
  流式读取 stdout/stderr；SSH 合并流仍实时写入原有 remote 日志。进程注册与取消
  请求消除竞态，stdin 提前关闭会保留子进程真实返回码；terminate 和 kill 后的等待
  都有上限。Windows 上的 BBDown 输出按当前控制台输出代码页解码，解码后统一
  以 UTF-8 写入 manifest；yt-dlp 和其他命令仍默认使用 UTF-8。
- Textual 工作流在 thread worker 内执行，所有界面更新通过 `call_from_thread`。
  TUI 会将子进程的默认 stdin 连接到 `DEVNULL`，防止 SSH/SCP 与 Textual
  争抢鼠标和焦点事件；显式提供的 BBDown 流选择值仍通过独立 PIPE 传入。UI 取消
  请求会终止当前子进程；纯文件操作在当前函数返回后才停止后续阶段。
- 界面日志只保留最近 5,000 行；完整下载命令结果仍写入 manifest，远程输出仍由
  remote-upload.log、remote-run.log 和 remote-fetch.log 保存。密集输出合并后按约
  30 Hz 刷新，阶段结束时会收纳没有换行的尾部输出。

### 远程状态

- `status` 只读取本地文件和 state，不探测远端主机；它适合快速判断下一步，但不
  替代各执行命令自己的哈希、provenance 和内容校验。
- `upload`、`run`、`fetch-results`、`cleanup` 通过
  `.workflow/remote-state.json` 传递
  主机、目录、哈希和阶段状态。
- upload 配置的是远端基目录，state 另行记录按项目和 plan SHA-256 派生
  的实际执行目录。
- `run` 和 `fetch-results` 不接受主机覆盖；目标只能由 upload 固化。
- `remote-plan.json` 变化后必须重新 upload，避免运行或拉回过期计划。
- `upload`、`run`、`fetch-results` 遇到 Ctrl+C 都会把状态记录为
  `interrupted`，并清理已知的临时传输文件。
- 旧版本 plan/script 不会生成 provenance；升级后必须先重新
  `remote-plan`，再重新 `upload`。
- 旧结果归档没有 provenance，不能再由 fetch 接管，必须重新 run。
- fetch 只替换计划内的 JSON，不删除项目目录中的其他 JSON。
- cleanup 的 `pending` 状态可重试；不要手动删除 `.cleanup-*` staging 文件。
- `remote-plan.sh` 是可审核脚本；运行前可以先打开查看。
- `remote-plan.sh --environment-only` 不检查输入包，用于大文件上传前验证环境。
- `remote-plan.sh --preflight-only` 只检查远端环境和输入包，不解包、不运行 VMAF。
- FFmpeg/FFprobe 最低主版本在 `EasyVmafSettings.ffmpeg_min_major` 中配置，默认是 5。
- easyVmaf 期望分支在 `EasyVmafSettings.required_branch` 中配置，默认是 `master`。
- 远端非交互 SSH 环境必须自行提供正确的 `PATH` 和动态库搜索路径。
- `ffprobe` 缺失或探测失败不会阻塞 `prepare`，但 4K/HD 模型选择会更多依赖文件名中的 `4K` 或 `2160p`。

### 手工排障

自动化失败时，先从 state 读取已经固化的主机和 plan-hash 隔离目录。不要把
脚本重新传回共享基目录：

```bash
state=videos/video10/.workflow/remote-state.json
host=$(jq -r '.remote.host' "$state")
work_dir=$(jq -r '.remote.work_dir' "$state")
package_local=$(jq -r '.upload.package.local_path' "$state")
package_remote=$(jq -r '.upload.package.remote_path' "$state")
script_remote=$(jq -r '.upload.script.remote_path' "$state")
provenance_remote=$(jq -r '.upload.provenance.remote_path' "$state")

ssh "$host" "mkdir -p -- '$work_dir'"
scp videos/video10/.workflow/remote-plan.sh "$host:$script_remote"
scp videos/video10/.workflow/remote-provenance.json "$host:$provenance_remote"
scp "$package_local" "$host:$package_remote"
ssh "$host" "cd '$work_dir' && bash remote-plan.sh --preflight-only"
ssh "$host" "cd '$work_dir' && bash remote-plan.sh"
```

手工传输只用于排障，不具备 CLI 的临时名原子替换和传输前后哈希检查。结果回收
仍应优先运行 `fetch-results`，让 provenance、成员集合和 JSON 安装事务得到验证。
