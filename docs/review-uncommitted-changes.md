# 代码评审报告：未提交更改（download 抽取 + TUI 流水线 + runner 流式回调）

- 评审对象：工作树未提交更改（`git diff HEAD`）
- 评审模式：最大召回（high effort，5+5 角度 × 候选 → 1-vote 验证 → 推荐）
- 评审日期：2026-07-18
- 受影响文件：
  - `src/vmaf_workflow/download.py`（新增，从 `cli.py::_download` 抽取）
  - `src/vmaf_workflow/pipeline.py`（新增，交互式流水线）
  - `src/vmaf_workflow/tui.py`（新增，Textual TUI）
  - `src/vmaf_workflow/runner.py`（重构，增加输出回调 / 取消 / 流式读取）
  - `src/vmaf_workflow/cli.py`（瘦身，`download` 改为调用 `download_sources`，新增 `interactive`/`auto`）
  - `pyproject.toml`、`src/vmaf_workflow/README.md`、`tests/test_workflow_*.py`

## 0. 复核与修复结果（2026-07-18）

本报告后续的缺陷描述保留为**修复前记录**。逐条复核后，代码已经完成以下处理：

- runner：stdin 提前关闭时保留真实返回码；进程注册时原子检查取消请求；所有异常路径
  统一收尾读取线程；读取线程 join、terminate 后 wait、kill 后 wait 均有超时；关闭管道
  后禁止迟到输出；内部 `ProcessInterrupted` 不再触发第二次 terminate。
- pipeline：running 安全检查先于下载恢复判定；恢复项目可补充未绑定站点，并强制从
  `download` 开始；实际执行参数与界面展示命令共用 `_download_inputs()`；空恢复项目和
  后续需要 prepare 却缺少参考视频的请求在执行前失败；manifest 在控制器会话中缓存。
- TUI：两个站点分别锁定已有身份；密集输出合并后约 30 Hz 刷新；无换行尾部在阶段
  结束时进入历史；`RichLog` 与会话缓存均严格限制为最近 5,000 行。
- 复用与清理：新增共享项目构造器和 `videoN` 枚举规则；pipeline 复用既有 manifest/
  status JSON loader；重建历史步骤记录以清除旧计时字段；删除未读取的 `_run_started`。
- C7 属于既定设计而非缺陷：纯文件操作在当前函数返回后响应取消，且不会继续下一阶段。
  C6 的原 Windows 句柄解释缺少依据，但仍补上了 kill 后二次等待超时作为防御性加固。
- 复核额外发现并修复：新增站点虽然已解析却被恢复起点跳过、下载不完整时绕过 running
  确认、展示命令与实际重试来源不一致、空恢复项目缺少前置校验。
- easyVmaf 默认线程数已按项目决定确认为 `8`，对应测试断言同步更新。

最终验证：`uv run pytest -q` 全部通过，`uv run ruff check src/vmaf_workflow tests`
通过，`git diff --check` 通过。新增回归测试覆盖 BrokenPipe、Popen 注册竞态、读取线程
超时、kill 超时、回调异常隔离、新增站点恢复、running 优先阻断、前置校验、等效命令、
runner 注入、manifest 缓存、单站点输入锁定、输出合并、尾部输出和 5,000 行上限。

## 1. 修复前总体结论

整体重构方向是合理的：把下载编排从 `cli.py` 抽到 `download.py::download_sources`，又新增 `WorkflowPipeline` 与 Textual TUI 复用同一套阶段函数，不通过子进程递归调用 CLI。`CLAUDE.md`/`AGENTS.md` 中的关键约束（文件读写显式 `encoding="utf-8"`、`vmaf-workflow = vmaf_workflow.cli:main` 入口点未变、下载前先做 `validate_source_identity`、行为改动均有测试）均未违反，本次未发现这类违规。

真正的问题集中在 `runner.py` 的**新增流式回调路径** `_run_with_output`。旧 `run()` 用 `subprocess.run(capture_output=True)`，异常处理简单；新路径使用 `Popen` + 两条守护读取线程 + 写 stdin + `wait()`，但 `try` 体内只捕获了 `KeyboardInterrupt`，把多条原本不该出现的失败语义漏了出去，且与取消逻辑产生竞态。多处缺陷独立地被不同评审角度重现，属于结构性问题，建议在合并前修复。

下方按严重程度排列，共 17 项（15 项已进入主清单，2 项来自 Angle A 的补充，单独列出）。

## 2. 原始缺陷清单（修复前）

### 正确性（runner 流式路径）—— 优先级最高

#### C1. `process.stdin.write` 无保护，`BrokenPipeError` 把取消/早退误判为 FAILED

- 文件：`src/vmaf_workflow/runner.py:174-178`
- 代码：

```python
if stdin is not None:
    if process.stdin is None:
        raise RuntimeError("subprocess stdin is unavailable")
    process.stdin.write(stdin.encode("utf-8"))
    process.stdin.close()
returncode = process.wait()
...
except KeyboardInterrupt:
    self._terminate_process(process)
    raise
finally:
    self._clear_process(process)
```

- 触发：B 站交互下载（`download.py:295-301`）经 `_run_with_output` 传 `stdin=f"{selected_index}\n"`。若 bbdown 在读到 stdin 之前提前退出（选错流、网络错误）或用户在此时点取消（`cancel_current` 的终止守护线程 kill 了子进程），`process.stdin.write` 抛出 `BrokenPipeError`（`OSError` 的子类，**不是** `KeyboardInterrupt`）。
- 后果：`except KeyboardInterrupt` 不接，异常穿透 `finally`（`_clear_process` 只清引用，不终止子进程），再被 `pipeline._run_stage` 的宽泛 `except Exception`（`pipeline.py:280`）捕获，**阶段标记为 FAILED / 返回码 1**，而不是预期的 CANCELLED / 130；同时跳过了读取线程的 join、丢弃了已缓冲的 stdout/stderr、`CommandResult` 没有构造，manifest 里这条命令也丢失。TUI 显示“工作流失败”而非“已取消”。
- 建议修复：`except (KeyboardInterrupt, OSError)` 也走终止/清理分支；或在 `stdin.write` 外包一层把 `BrokenPipeError` 归一化为 `ProcessInterrupted`。

#### C2. 取消与“子进程尚未注册”的竞态，孤儿进程跑到自然结束

- 文件：`src/vmaf_workflow/runner.py:54-64`（`cancel_current`）与 `runner.py:130-139`（`_raise_if_cancelled`→`Popen`→`_set_process` 之间）
- 触发：用户在 `Popen` 进行中点取消。`cancel_current` 设置 `_cancelled`，读 `self._process` 时它还是 `None`，于是跳过终止守护线程。
- 后果：真正的子进程照常跑完（Popen + 读取线程启动），`process.wait()` 阻塞到自然结束，随后 `_cancelled.is_set()` 才抛 `ProcessInterrupted`。对于长 SSH / ytdlp 阶段，**取消在当前阶段内无效**，只是事后才报告取消。
- 建议修复：`cancel_current` 在 `_process is None` 时也要保证稍后 Popen 完成后能被终止（例如用一个“待终止”标志，`_set_process` 后立即检查并终止），或在 `_run_with_output` 进入循环前再查一次 `_cancelled`。

#### C3. `read_stream` 读取线程在孙进程持有管道时永久阻塞，`thread.join()` 无超时挂死

- 文件：`src/vmaf_workflow/runner.py:146-181`
- 触发：子进程派生了继承 stdout/stderr 管道句柄的孙子进程（SSH 包裹的 shell、`nohup`、ffmpeg wrapper 等常见）。父进程退出后 `process.wait()` 返回，但管道 EOF 要等孙子进程也关闭句柄才出现，`read1` 永不返回 EOF，`thread.join()`（无 timeout）阻塞工作线程。
- 后果：`_run_with_output` 永不返回，TUI 工作线程卡死，取消也无意义（工作线程到不了下一个 `_cancelled` 检查）。旧 `subprocess.run` 走 `communicate()`，没有这个暴露面——这是流式线程设计新引入的。
- 建议修复：`thread.join(timeout=...)`，超时后强制关闭管道/终止进程族；或对读取线程设可中断机制。

#### C4. 守护读取线程在任何异常退出时不被回收，溅射 `process-output` 事件到下一阶段

- 文件：`src/vmaf_workflow/runner.py:180-195`
- 触发：try 体内任何非 `KeyboardInterrupt` 异常（C1 的 `BrokenPipeError`、`RuntimeError("subprocess stdin is unavailable")`、或 `_emit` 里回调抛错）。
- 后果：`finally` 只 `_clear_process`，两条守护读取线程仍在跑、仍往 `chunks` 追加并经 `_emit -> output_callback -> _on_process_output` 往 TUI 发 `process-output` 事件。此时阶段已被标记 FAILED/完成，这些迟到事件会以 `stage=None`（或错阶段）落到 `_apply_event`，污染下一个阶段的日志或出现在“全部步骤已完成”之后。
- 建议修复：把 `for thread in readers: thread.join(...)` 移进 `finally`（带超时与强制收尾），并在清理后置位标志让 `_emit` 丢弃后续输出。

#### C5. `ProcessInterrupted` 继承 `KeyboardInterrupt`，取消路径触发冗余终止竞态

- 文件：`src/vmaf_workflow/runner.py:16, 182-193`
- 触发：取消后，终止守护线程在 `process` 上跑 `terminate + wait(timeout=5)`；工作线程的 `process.wait()` 同时返回，`if self._cancelled.is_set(): raise ProcessInterrupted()` 抛在 try 体内，被 `except KeyboardInterrupt`（因为 `ProcessInterrupted(KeyboardInterrupt)`）再次捕获，再调一次 `_terminate_process(process)`。
- 后果：在 Windows 上同一进程句柄被两个 `wait()`/`terminate()` 调用竞争回收，脆弱且偶发错误；取消信号被“为用户 Ctrl-C 写的 handler”二次处理，语义被洗。
- 建议修复：`ProcessInterrupted` 不要继承 `KeyboardInterrupt`（或异常分支显式区分内部取消 vs 外部 Ctrl-C），避免双重终止。

#### C6. `_terminate_process` 在 `kill()` 后用无超时 `process.wait()`，Windows 句柄残留时永久卡死

- 文件：`src/vmaf_workflow/runner.py:224-228`
- 触发：Windows 下被 `kill()` 的进程被 console job 持有句柄而不被立即回收；`process.wait()`（无 timeout）阻塞。
- 后果：该调用发生在 `cancel_current` 派生的终止守护线程里，工作线程的 `process.wait()` 也同时阻塞 —— 取消永久停在“正在取消当前步骤…”。这是 Angle A 单独补充、且与 C2/C5 叠加的 Windows 平台特有问题。
- 建议修复：`kill()` 后的 `wait()` 设超时并兜底（忽略 `OSError` 或记录孤儿）。

### 正确性（流水线 / TUI）

#### C7. 非 runner 阶段（prepare / package / cleanup / 内部 ffmpeg）无法被取消

- 文件：`src/vmaf_workflow/pipeline.py:315-340`（PREPARE/PACKAGE/REMOTE_PLAN/CLEANUP 通过 `subprocess.run` 直接调用），见 `run_remote_project` 之外各阶段不出 `self.runner`、不注册 `_process`
- 触发：`prepare_project` 调 `packager` 里的 `subprocess.run`（ffprobe/ffmpeg），`package_project`、`cleanup_project` 同理，从不碰 `self.runner`。用户在打包大 ffmpeg 时点取消。
- 后果：`WorkflowPipeline.cancel()` 调 `runner.cancel_current()`，但 `_process is None`，子进程不受影响；这些阶段里也不查 `_cancelled`。阶段跑完才会到循环顶部的取消检查。README 称“取消会终止当前下载器或 SSH 子进程”准确，但 prepare/package/cleanup/remote-plan 的取消会**静默延迟到阶段结束**。建议在文档补一句或为这些阶段接 runner。

#### C8. 传入了带回调的 runner 时，`mirror_console` 未被关掉，污染 Textual 终端

- 文件：`src/vmaf_workflow/pipeline.py:206-211`

```python
self.runner = runner or SubprocessRunner(
    output_callback=self._on_process_output,
    mirror_console=False,
)
if runner is not None and runner.output_callback is None:
    runner.output_callback = self._on_process_output
```

- 触发：调用方传入 `SubprocessRunner(output_callback=fn)`（`mirror_console` 默认 `True`）。`__init__` 的 `if runner.output_callback is None` 为 False，既不重设回调也不把 `mirror_console` 设为 `False`。
- 后果：每个阶段经 `_run_with_output`，两条读取线程并发 `_emit` → 写 `sys.stdout`/`sys.stderr`，与持有终端的 Textual TUI 抢写、字节交错、UI 乱码，并竞态于 `sys.stdout.write`。TUI 自身走的默认路径（`mirror_console=False`）安全，问题仅在测试或库用法里“传入预绑回调的 runner”时出现。
- 建议修复：对传入的 runner 强制 `runner.mirror_console = False`，或要求调用方显式传 `mirror_console=False`。

#### C9. `_load_project` 同时禁用两个来源输入，违反 AGENTS.md 的“可补缺失站点”约束

- 文件：`src/vmaf_workflow/tui.py:516-519`

```python
identities_bound = bool(defaults.bvid or defaults.ytid)
bvid_input.disabled = identities_bound
ytid_input.disabled = identities_bound
reference_input.disabled = defaults.reference is not None
```

- 触发：恢复一个只绑了 bvid、未绑 YouTube 的项目，`identities_bound = bool(bvid or None) = True`，于是 ytid 输入框也被禁用。
- 后果：用户无法在 TUI 里补填 YouTube 来源，哪怕 CLI（`_download_inputs` / `download_sources` 含 `update_youtube=True`）和 `AGENTS.md` 明确允许“a missing site may be added”。**这是对 `AGENTS.md` 下载身份规则的直接 UX 违背**。
- 建议修复：按站点分别禁用：`bvid_input.disabled = defaults.bvid is not None`；`ytid_input.disabled = defaults.ytid is not None`。这正是 `download_state.validate_source_identity` 已强制实施的同一条规则，TUI 不应另行重推。

#### C10. resume 缺 manifest 的空项目，在最深处才报“至少填一个来源”

- 文件：`src/vmaf_workflow/pipeline.py:132-157`（`validate_pipeline_request` 仅在 `project_dir is None` 时要求源）、`pipeline.py:353-390`（`_resume_stage`→`_download_inputs`→`download_sources`）
- 触发：上一次运行创建了 `videos/videoN/` 但还没写 manifest 就中断；TUI `_project_options` 因匹配 `video\d+` 列出它。用户在 resume 模式空着 bvid/ytid/reference 选了它。
- 后果：`validate_pipeline_request` 跳过来源检查（project_dir 非 None）和参考视频检查；`_resume_stage` 落到 `status` 的 `'new'` → DOWNLOAD；`_download_inputs` 在 `manifest is None` 分支返回 `(None, None)`；`download_sources` 抛 `DownloadInputError`，被当作“下载阶段失败，请检查下载器输出”呈现，而不是前置的“请提供来源”验证提示。
- 建议修复：在 resume 校验阶段或在 `_download_inputs` 之前显式检查源可发现性，给出准确提示。

### 效率 / 清理 / 复用

#### E1. 每次 resume 重复解析 manifest JSON 达 3-4 次

- 文件：`src/vmaf_workflow/pipeline.py:356, 375-376, 170-173`
- 链：`_resume_stage`（`_load_json(manifest)` + `requested_incomplete_sources`）→ DOWNLOAD 的 `_download_inputs`（再 `_load_json(manifest)`）→ `load_resume_defaults`（第三次 `_load_json(manifest)` + inventory）。构成每次 resume/重试启动 3 次解析 manifest、1-2 次解析 inventory。
- 后果：大下载 manifest（多 bbdown/yt-dlp 流）下 resume 启动有明显延迟与冗余磁盘 I/O。
- 建议修复：把已解析的 manifest 缓存在 `self._manifest`/经参数贯穿；`load_resume_defaults` 增 `manifest=` 关键字避免重复读盘。

#### E2. 每个输出块触发一次 worker→UI 线程跳转 + 逐行写 RichLog

- 文件：`src/vmaf_workflow/tui.py:367-411`
- 触发：`_on_process_output → _receive_event → call_from_thread(_apply_event) → _consume_output`（逐行 `RichLog.write`）。
- 后果：yt-dlp/远端计算的密集输出会产生大量线程跳转 + N 次控件写，Textual 消息队列串行化，TUI 在重流下发卡。
- 建议修复：在 `_on_process_output` 缓冲合并大块；按 ~30Hz 节流刷新；缓存 `#log`/`#current-output` 控件引用避免每行 `query_one`。

#### E3. `StageRecord` 跨运行残留 `_started`/`elapsed_seconds`

- 文件：`src/vmaf_workflow/pipeline.py:392-403`（`_mark_prior_success` 原地改 `status`，不复位计时字段，而 `_reset_from` 重建记录）
- 后果：极端情况外部读取者可能读到 `status=RUNNING` 但 `_started=None` 的不一致快照（“—”耗时一闪而过）；CPython GIL 下不崩溃，属真实性低、偶发。
- 建议修复：两个 helper 共用同一个“重建 `StageRecord`”原语，避免计时字段跨运行存活。

#### R1. `WorkflowProject(dir, dir / ".workflow")` 在三处内联构造，被删的 `_explicit_project` 失去集中

- 文件：`download.py:74-75`、`pipeline.py:171`、`pipeline.py:214`（旧 `cli.py::_explicit_project` 已随抽取删除）
- 后果：若 `.workflow` 目录名或 `WorkflowProject` 增加必填字段，需同步改三个生产文件，漏改会静默指向错误 `workflow_dir`、读错所有 manifest/产物路径。
- 建议修复：在 `project.py` 放一个共享构造器（如 `explicit_project(project_dir)`），三处改为调用。

#### R2. `_load_json` 重复实现已存在的校验加载器

- 文件：`src/vmaf_workflow/pipeline.py:506-515`，重复 `download_state.load_download_manifest`（`download_state.py:25`）与 `status._load_optional_json_object`（`status.py`）
- 后果：三者仅异常类型不同。后续 `load_download_manifest` 增加 schema/编码校验时，pipeline 侧 `_load_json` 仍用宽松旧逻辑，resume 与 status/下载链对同一文件给出矛盾结论。
- 建议修复：复用 `load_download_manifest`（或抽出共享 helper）。

#### R3. `_project_options` 重复实现 `next_video_dir` 的 `video\d+` 扫描

- 文件：`src/vmaf_workflow/tui.py:522-538`，重复 `project.py::next_video_dir`（`project.py:90-99`）
- 后果：若项目命名规则改为零填充/加前缀且只改 `next_video_dir`，TUI 仍只列旧格式目录，恢复用户看不到新格式项目，两套清单漂移。
- 建议修复：抽出共享的 `video\d+` 枚举/解析助手给二者用。

#### S1. `self._run_started` 是死状态

- 文件：`src/vmaf_workflow/pipeline.py:223, 248`
- 现状：仅非重试运行赋值，`pipeline.py`/`tui.py`/测试从不读取；TUI 用自己的 `_started_at`。
- 后果：维护者若以为它反映运行起点而依赖它，在重试运行里它保持 `None`，可能引入不一致计时或 NoneType bug。
- 建议修复：删除该字段与赋值。

### 约定（约定评审未发现违规，记录已核查项）

- `download.py:339`、`pipeline.py:510` 显式 `encoding="utf-8"`，`tui.py` 无真实文件 I/O —— 满足 PowerShell 5.1 UTF-8 约束。
- `pyproject.toml:18` `vmaf-workflow = "vmaf_workflow.cli:main"` 仍在；diff 仅加 `textual` 与 `pytest-asyncio`。
- `download_sources` 在 `download.py:81-91` 内先归一化 + `validate_source_identity` 后才 `create_project`/写配置/写 manifest/跑下载器，满足“先校验身份再写盘/调 runner”。
- 行为改动均有测试：`test_workflow_cli.py::test_download_*`、`test_workflow_runner_manifest.py::test_subprocess_runner_*`（含流式与外部取消）、`test_workflow_pipeline.py`、`test_workflow_tui.py`（含取消），`uv run pytest -q` 通过。

## 3. 原始修复优先级建议

1. **C1 / C4 / C5**（runner 试中的异常/取消语义）一起改：把 `except` 扩到覆盖 `OSError`，`finally` 里 join 读取线程，`ProcessInterrupted` 不再继承 `KeyboardInterrupt`，`_terminate_process` 的 `kill()` 后 `wait()` 加超时（含 C6）。
2. **C9**（TUI 单 site 禁用）—— 与 `AGENTS.md` 直接冲突，改动小、收益清楚，建议尽快合并。
3. **C7**（非 runner 阶段不可取消）—— 决定是接 runner 还是在文档里写明“prepare/package/cleanup 取消延迟到阶段结束”。
4. **C2 / C3**（取消竞态与孙子进程死锁）—— Windows 上偶发，结合 C1/C6 一并加固 runner。
5. **E1 / E2 / R1 / R2**—— 整理与效率项，可在正确性补丁之后一并清理。
6. **C8 / C10 / E3 / R3 / S1**—— 边缘与清理项。

附：C9 与 C6 由“Angle A 逐行扫描”在主清单之外独立补充并经核对确认，与本报告其余项不重复。
