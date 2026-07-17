# Incremental Download Contract Design

## Goal

Make `vmaf-workflow download --project-dir <videoN>` a safe incremental
operation. A project may add a previously missing Bilibili or YouTube source,
or rerun the same source ID, without losing the other source's manifest data or
continuing with stale downstream artifacts.

## Source Identity Contract

Each project may contain at most one normalized Bilibili BVID and one
normalized YouTube URL.

- A source absent from the existing manifest may be added.
- A source already present may be rerun only with the same normalized ID.
- A different ID for an existing source is rejected with exit code `2`.
- Identity validation happens before downloader configuration files, manifest
  data, downstream artifacts, or downloaders are touched.
- An absent manifest is treated as a project that has not recorded download
  state yet. An unreadable or non-object manifest is rejected through the
  existing CLI state-error conventions rather than silently overwritten.

This prevents unrelated videos from being mixed in one `videoN` project while
preserving the current ability to download either site independently.

## Manifest Merge Contract

The existing manifest is the base for an incremental download.

- The source not requested in the current invocation is preserved unchanged.
- Each requested source becomes the latest snapshot produced by that download
  attempt: normalized identity, preflight streams, plan, downloads, and
  source-specific metadata are replaced together.
- Top-level command history is appended so earlier and current downloader
  commands remain auditable.
- The original `created_at` value is retained and `updated_at` records the
  latest invocation time.
- Project paths and generated downloader-config paths are refreshed from the
  current project.
- Existing unrelated top-level data is preserved unless it points to an
  invalidated downstream stage.

Replacing the requested source snapshot avoids duplicate stream entries when
the same source is rerun, while preserving the other site's completed record.

## Downstream Invalidation

Any accepted, non-dry-run download into an existing project invalidates stages
derived from the previous media set before a downloader is invoked. This also
keeps the project safe if a downloader adds only some files and then fails.

The following reproducible local artifacts are deleted when present:

- `.workflow/media-inventory.json`
- `.workflow/package-manifest.json`
- the default `.workflow/<videoN>-inputs.tar`
- `.workflow/remote-plan.json`
- `.workflow/remote-plan.sh`
- `.workflow/remote-state.json`
- `.workflow/remote-provenance.json`
- the default `.workflow/<videoN>-json.tar.gz`

The matching `reference`, `media_inventory`, `package`, `remote_plan`, and
`results` pointers are removed from the main manifest before the merged
download state is written.

The invalidation deliberately preserves:

- source and reference media files;
- installed `*_vmaf.json` result files;
- diagnostic logs;
- custom package outputs outside the default managed archive path;
- remote files, which remain isolated by the prior plan hash.

Dry runs never invalidate downstream artifacts or invoke downloaders. A fresh
project, including an existing directory with no manifest, still receives its
generated configs and base dry-run manifest. When a manifest already exists,
dry-run validates source identity but leaves that manifest and all downstream
state unchanged.

## Status Detection

`status` compares the normalized set of supported media paths on disk with the
paths recorded in `media-inventory.json`.

- Missing recorded media keeps the existing behavior.
- Extra supported media not present in the inventory is also treated as a
  stale inventory.
- Either mismatch reports `stage: downloaded`, `state: incomplete`, and
  recommends `prepare`.
- Media under the existing excluded directories remains ignored.

This is a defense-in-depth check. Normal incremental downloads remove the old
inventory, while manual file copies and interrupted legacy runs are still
detected.

## Error And Failure Behavior

- A source identity conflict is atomic: no files or manifest fields change and
  the runner is not called.
- Once a valid non-dry-run incremental attempt starts, downstream invalidation
  is durable even if preflight or download fails.
- The current download attempt is still written to the merged manifest with
  its success or failure decisions.
- Existing CLI return-code conventions remain unchanged: validation/state
  errors return `2`, downloader failures return `1`, and success returns `0`.

## Test Strategy

Tests follow red-green-refactor and cover:

1. Bilibili-first then YouTube incremental download preserves Bilibili data,
   records YouTube data, and appends command history.
2. Rerunning the same source ID replaces only that source snapshot without
   duplicating its stream records.
3. Supplying a different BVID or YouTube ID returns `2`, makes no writes, and
   never calls the runner.
4. A valid incremental attempt removes managed downstream artifacts and their
   main-manifest pointers while preserving media, installed JSON, logs, and a
   custom package output.
5. A downloader failure after invalidation cannot leave the old inventory or
   remote state active.
6. `status` returns to `downloaded / incomplete` when supported media exists on
   disk but is absent from the inventory.
7. The existing simulated end-to-end workflow and complete Python/frontend
   suites continue to pass.

## Documentation Updates

`src/vmaf_workflow/README.md` will document:

- Bilibili-only and YouTube-only commands;
- supplementing the missing site in an existing project;
- same-ID reruns and different-ID rejection;
- manifest merge semantics and downstream invalidation;
- the required restart at `prepare` after an incremental download;
- `--videos-dir` and the actual `--dry-run` behavior.

`AGENTS.md` will add the workflow entrypoint, lifecycle commands, key module
locations, required test expectations, and a link to the workflow README.

## Scope Boundaries

- No multi-BVID or multi-YouTube project schema is introduced.
- No general revision/fingerprint system is added across every stage.
- No remote deletion is performed.
- No installed VMAF result JSON or user-managed custom package is deleted.
- No new CLI flags are added.
