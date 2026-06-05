# OpenAver 自有分支迭代开发计划

## 目标

在 OpenAver 现有基础上演进为更适合个人本地媒体库管理的自有项目。第一阶段重点不是改 AI manifest，而是重做文件识别、刮削匹配、sidecar 存储和多版本展示模型，让系统能正确理解同一作品的不同文件变体，并把 NFO、封面、剧照等资料集中管理。

本计划以本机资料与参考工具目录为起点：

```text
D:\TV\Porn\Demo
```

该目录当前包含 Javinizer、JavLuv、Jvedio、pornboss、MDCx、tinyMediaManager、Jellyfin、Kodi 等同类型工具或安装包。后续只做行为与体验层面的借鉴，不复制第三方代码。

## 核心需求

1. 文件识别需要具备等同归一化能力：大小写、`-`、`_`、空格、无分隔符等形式应能归一到同一个搜索番號。
2. 刮削时应使用归一化后的番號搜索，而不是被原始文件名大小写或分隔符影响。
3. 番號需要支持同义词与来源查询别名，例如 `FC2PPV-1234567`、`FC2-1234567`、`FC2-PPV-1234567` 本质上应视为同一作品；但不同网站搜索时可能需要不同 query 形式。
4. NFO、封面、poster、fanart、剧照应可配置到独立路径，不强制和视频放在同一目录。
5. `-1`、`-a`、类似分段/版本标记应归为同一个作品，在展示页聚合为一个作品卡，详情页展示多个实际文件。
6. `-c` 表示中文字幕，`-u` 表示破解版，`-uc` 表示破解版 + 中文字幕。这里的 `u` 是破解版，不是无码。
7. Settings 需要能选择搜索信源：有码、无码、全部搜索、自定义来源组合。后续计划增加更多信源，不能把来源逻辑写死。
8. Scanner 的扫描文件夹需要在添加时标记本地目录类型：`censored`（有码）或 `uncensored`（无码）。这个标记只描述本地路径归类，不等于 `-u` 破解版，也不限制搜索信源；搜索仍可配置为全部来源。
9. 借鉴成熟同类型工具的成熟交互和目录策略，形成适合本项目的本地媒体库管理方案。

## 现状判断

OpenAver 已经有基础能力：

- `core/scrapers/utils.py` 有 `extract_number()`、`check_subtitle()`、`strip_subtitle_markers()`。
- `core/gallery_scanner.py` 有独立的 `VideoScanner.NUM_PATTERNS`。
- `core/organizer.py` 已有 `{suffix}`、NFO 生成、封面下载、Jellyfin poster/fanart、extrafanart。
- `web/config.default.json` 已有 `suffix_keywords`、`cover_filename`、`jellyfin_mode` 等配置。

当前主要缺口：

- 番號解析逻辑分散在 scraper utils、gallery scanner、search 判断中，缺少统一模型。
- `-c`、`-u`、`-uc`、`-1`、`-a` 等语义混在 suffix 字符串里，没有结构化字段。
- DB 目前以单个视频文件为核心，缺少“作品”和“文件变体”的分层。
- sidecar 目标路径基本跟随视频文件目录，缺少独立资料根目录配置。
- Showcase 当前更接近“视频列表”，不是“作品聚合 + 文件变体详情”。

## 目标数据模型

新增统一识别结果模型，先在 Python 层落地，后续再扩展 DB：

```text
raw_name          原始文件名或用户输入
canonical_number 作品基准番號，例如 SONE-103
search_number    用于刮削的番號，通常等于 canonical_number
display_number   页面展示番號，通常等于 canonical_number
number_aliases    同义番號列表，例如 FC2PPV-1234567 / FC2-1234567 / FC2-PPV-1234567
source_queries    按来源生成的查询候选，例如 {fc2: [...], javdb: [...], metatube: [...]}
part_index       分段序号，例如 -1、-2、-A
variant_flags    结构化变体标记，例如 subtitle_cn、cracked
variant_label    展示标签，例如 中文字幕、破解、破解+中文字幕、Part 1
work_key         作品聚合 key，默认 canonical_number
file_key         单个文件唯一 key，默认 file path URI
```

重要规则：

- `sone_103`、`SONE-103`、`sone103` 应归一为 `SONE-103`。
- `SONE-103-C`、`SONE_103_c` 应归一为作品 `SONE-103`，并标记 `subtitle_cn=true`。
- `SONE-103-U` 应归一为作品 `SONE-103`，并标记 `cracked=true`。
- `SONE-103-UC` 应归一为作品 `SONE-103`，并标记 `subtitle_cn=true`、`cracked=true`。
- `SONE-103-1`、`SONE-103-A` 应归一为作品 `SONE-103`，并标记为分段或文件变体，不拿 `SONE-103-1` 去刮削。
- `u` 不得映射为 uncensored，无码仍然由 source 类型、scraper 结果或独立字段表达。
- `FC2PPV-1234567`、`FC2-1234567`、`FC2-PPV-1234567` 应归一为同一作品。推荐 canonical 使用 `FC2-PPV-1234567`，同时保留 aliases。
- 搜索时不只发送 canonical number，而是由 source adapter 生成该来源最可能命中的 query 形式。

## 阶段计划

### Phase 0: 样本与参考工具审计

目标：先明确要兼容哪些文件名和哪些同类工具行为。

任务：

- 扫描 `D:\TV\Porn\Demo`，列出可参考工具：Javinizer、JavLuv、Jvedio、pornboss、MDCx、tinyMediaManager、Jellyfin、Kodi。
- 建立 `docs/references/media-manager-behavior-notes.md`，记录可借鉴点。
- 建立 `tests/fixtures/filename_variants/` 样本清单，覆盖大小写、`-`、`_`、无分隔符、`-c`、`-u`、`-uc`、`-1`、`-a`。
- 明确不扫描 `D:\TV\Porn\Demo` 当作真实媒体库，除非用户后续把视频样本也放进去。

验收：

- 有一份参考工具行为清单。
- 有一份文件名样本矩阵。
- 所有后续开发都以样本矩阵作为契约来源。

### Phase 1: 统一文件识别与番號归一化

目标：新增单一真理来源，替代分散的番號解析规则。

建议新增模块：

```text
core/filename_identity.py
```

核心 API：

```python
parse_media_identity(name: str) -> MediaIdentity
normalize_work_number(value: str) -> str | None
build_search_candidates(identity: MediaIdentity) -> list[str]
build_source_queries(identity: MediaIdentity, source_id: str) -> list[str]
```

迁移范围：

- `core/scrapers/utils.py::extract_number`
- `core/gallery_scanner.py::VideoScanner.parse_filename`
- `core/scraper.py::is_number_format`
- `web/routers/filename.py`
- Search、Scanner、Scraper 中所有“从文件名推番號”的入口

验收：

- `SONE-103`、`sone_103`、`sone103` 搜索同一个作品。
- `SONE-103-C/U/UC/1/A` 刮削时都使用 `SONE-103`。
- `FC2PPV-1234567`、`FC2-1234567`、`FC2-PPV-1234567` 聚合为同一作品，并能按来源生成不同搜索 query。
- `-u` 被标记为破解版，不进入 uncensored 判断。
- 原有 FC2、HEYZO、D2Pass 日期格式不能回归。

### Phase 1.5: 信源选择与来源查询策略

目标：Settings 中可选择搜索范围，并为新增信源预留扩展点。

现有 OpenAver 已有 `sources` 配置和 `SourceConfig`，但下一阶段需要更明确地区分“用户想搜哪些来源”和“某个番號在某个来源应如何表达”。

配置建议：

```json
{
  "search": {
    "source_mode": "enabled | censored | uncensored | all | custom",
    "custom_source_ids": ["javbus", "javdb", "fc2"],
    "try_all_aliases": true,
    "max_sources_per_search": 10,
    "max_queries_per_source": 3
  }
}
```

来源策略：

- `enabled`：沿用当前启用来源，作为默认兼容模式。
- `censored`：只搜索有码来源，例如 DMM、JavBus、Jav321、JavDB。
- `uncensored`：只搜索无码来源，例如 FC2、HEYZO、D2Pass、AVSOX。
- `all`：搜索所有可用来源，适合用户不想手动选择时使用。
- `custom`：用户自定义来源组合，适合长期固定偏好。

来源扩展要求：

- 每个来源应有自己的 query adapter，负责把 `MediaIdentity` 转成该站最可能命中的查询列表。
- 新增来源时只新增 adapter 与 SourceConfig，不应修改核心解析逻辑。
- 所有来源仍需保留 availability、rate limit、manual_only、is_beta、requires_proxy 等 gate。
- `all` 模式不能无限 fan-out，需要受 `max_sources_per_search`、来源健康状态、速率限制保护。

FC2 查询策略示例：

```text
canonical: FC2-PPV-1234567
aliases:
- FC2-PPV-1234567
- FC2PPV-1234567
- FC2-1234567
- 1234567

fc2 source query order:
1. FC2-PPV-1234567
2. FC2PPV-1234567
3. 1234567

javdb / metatube query order:
1. FC2-PPV-1234567
2. FC2-1234567
3. FC2PPV-1234567
```

UI 需求：

- Settings 增加“搜索范围”分段控制：已启用 / 有码 / 无码 / 全部 / 自定义。
- 自定义模式下显示来源 checklist，并按有码、无码、外部服务分组。
- 搜索页保留单次 override，用户可以临时切换“只搜无码”或“全部搜”。
- capabilities manifest 需要暴露当前 source_mode 和实际会 fan-out 的来源快照。

验收：

- 用户不选择具体网站时，可以启用 `all` 或 `enabled` 自动搜索。
- 用户能明确只搜有码或只搜无码。
- FC2 同义番號不会导致重复作品，但能提升不同网站命中率。
- 新增信源时不需要改 filename parser。

### Phase 1.6: 扫描目录内容标签

目标：扫描来源可以全部搜索，但本地扫描文件夹本身要能标记为有码或无码，并让列表、Showcase 与 AI manifest 都能读到这个本地分类。

配置建议：

```json
{
  "gallery": {
    "directories": [
      "D:/TV/Porn/Censored",
      "D:/TV/Porn/Uncensored"
    ],
    "directory_labels": {
      "D:/TV/Porn/Censored": "censored",
      "D:/TV/Porn/Uncensored": "uncensored"
    }
  }
}
```

规则：

- 默认值为 `censored`，旧配置不需要迁移即可继续工作。
- 添加扫描目录时先选择目录类型；添加后可以在目录列表中单独切换。
- 视频继承所在配置目录的标签；如果父子目录都被配置，优先使用更深层目录的标签。
- 该标签只影响本地展示、筛选和 manifest 输出，不改变来源 fan-out 策略。
- `uncensored` 不能由文件名 `-u` 推导；`-u` 仍然是 `variant_flags.cracked=true`。

验收：

- Scanner 添加路径时可选择有码/无码，并保存到 `gallery.directory_labels`。
- Showcase 作品卡、列表、详情页文件清单能显示目录标签。
- `/api/showcase/videos` 与 `/api/showcase/video` 的返回结构包含 `directory_label`。
- capabilities manifest 说明 `directory_label` 来自扫描目录，不代表破解版或搜索信源类型。

### Phase 2: 结构化变体标记

目标：把 suffix 从字符串提升为结构化语义。

新增字段建议：

```text
variant_flags.subtitle_cn boolean
variant_flags.cracked boolean
variant_flags.part string | null
variant_flags.raw_tokens array
```

规则：

- `-c`、`_c`、`中文字幕`、`中字` → `subtitle_cn=true`
- `-u`、`_u` → `cracked=true`
- `-uc`、`_uc` → `subtitle_cn=true` 且 `cracked=true`
- `-1`、`-2`、`-a`、`-b` → `part`
- 多 token 可组合，例如 `SONE-103-A-UC`

需要同步：

- NFO 写出：中文字幕继续写 `<tag>中文字幕</tag>`；破解版建议写 `<user_tag>破解</user_tag>` 或新配置决定写入位置。
- UI badge：展示“中字”“破解”“Part A”等短标签。
- capabilities manifest：更新 `parse_filename` 输出 schema，告诉 agent 有结构化 variant 信息。

验收：

- `-u` 不再被任何逻辑误认为无码。
- `-uc` 同时具备破解和中文字幕两个语义。
- `-c` 不污染作品番號。

### Phase 3: 独立 sidecar 资料根目录

目标：NFO、封面、poster、fanart、extrafanart 可集中放到独立目录，视频目录保持干净。

配置建议：

```json
{
  "sidecar": {
    "mode": "alongside | centralized",
    "root_dir": "D:/TV/Porn/Metadata",
    "layout": "{maker}/{num}/",
    "nfo_filename": "{num}.nfo",
    "cover_filename": "cover.jpg",
    "poster_filename": "poster.jpg",
    "fanart_filename": "fanart.jpg",
    "extrafanart_dir": "extrafanart"
  }
}
```

涉及模块：

- `core/organizer.py`
- `core/enricher.py`
- `core/sidecar_paths.py`
- `core/gallery_scanner.py`
- `core/database.py`
- `core/path_utils.py`
- Settings 页面与 `zh_TW.json`

设计原则：

- 默认保持兼容：`mode=alongside`。
- 新模式 `centralized` 只在用户配置 root_dir 后启用。
- Settings 页面需要提供集中根目录选择、NFO/封面/poster/fanart/extrafanart 命名模板配置，以及保存前的路径预览。
- DB 存储本地 `file:///` URI，路径转换继续走 `core/path_utils.py`。
- NFO 内 `<thumb>` 可写相对路径或 file URI，需要明确配置。
- `organize_file()`、`enrich_single()`、`db_to_sidecar`、`refresh_full`、`fetch_samples_only()` 的写入与 overwrite 检查必须使用同一个 sidecar resolver，避免 UI 配置和实际落盘路径分裂。

验收：

- 新整理的视频文件可以留在视频目录，NFO/封面写到独立资料根目录。
- Showcase 和 Scanner 能正确读取集中 sidecar。
- `scrape_single`、`db_to_sidecar`、`refresh_full`、`fetch_samples` 都遵守 sidecar 配置。
- 集中模式下，封面、poster、fanart、extrafanart 与 NFO 的目标路径都可从 `sidecar` 配置推导，且父目录会按需创建。
- Settings 中切换到集中模式后可以选择根目录，并立即看到 NFO、封面、海报、背景图、剧照目录的最终路径预览。

### Phase 4: 作品聚合与多文件详情页

目标：Showcase 从“一个文件一张卡”升级为“一个作品一张卡，详情页列出多个文件”。

DB 方向：

```text
works
- id
- work_key
- canonical_number
- title
- actresses
- maker
- release_date
- cover_path

video_files
- id
- work_id
- path
- part
- variant_flags
- size_bytes
- duration
- mtime
```

过渡方案：

- 第一阶段不立刻拆表，可先在查询层按 `canonical_number` group。
- 后续 migration 再拆出 `works` / `video_files`。

UI 行为：

- Showcase 卡片显示作品封面、番號、标题、女优、主 badge。
- 卡片角标显示文件数，例如 `3 files`。
- 详情页展示文件列表：路径、大小、清晰度、part、中文字幕、破解状态、播放按钮。
- 搜索结果和本地状态也应支持“本地已有作品，但有多个文件”。

验收：

- `SONE-103.mp4`、`SONE-103-C.mp4`、`SONE-103-A.mp4` 只显示一张作品卡。
- 详情页能列出全部文件。
- 点击具体文件仍能播放或打开原路径。

### Phase 5: Scanner/Search/Scraper 工作流整合

目标：让新识别模型贯穿用户工作流。

任务：

- Scanner 扫描时保存 `canonical_number` 与 variant 信息。
- Scanner 扫描目录保存 `directory_labels`，列表生成时按路径继承 `censored` / `uncensored`。
- Search 搜索时使用 `search_number`，展示时保留原始文件 variant。
- Scraper 整理时根据 `canonical_number` 命名资料，不因 `-c/-u/-a` 重复刮削。
- `local_status` 支持按作品聚合返回。
- `batch_search` 可以对归一化后去重，避免同一作品重复请求外站。

验收：

- 同一作品多个变体不会重复打外站。
- 用户能清楚看到每个文件具体差异。
- 用户能在列表和详情中看出文件来自有码目录还是无码目录。
- AI agent 能通过 capabilities 知道“作品”和“文件变体”的区别。

### Phase 6: 成熟工具取长补短

目标：从同类工具提炼值得借鉴的稳定实践。

参考方向：

- Javinizer / MDCx：命名模板、资料源优先级、sidecar 目录布局。
- JavLuv / Jvedio：本地资料库、手工修正、重复项管理。
- tinyMediaManager：电影式作品模型、NFO 兼容、批量修复体验。
- Jellyfin / Kodi：poster、fanart、NFO 兼容、媒体库刷新逻辑。
- pornboss / 其他轻量工具：快速识别与低摩擦整理流程。

输出：

- `docs/references/media-manager-behavior-notes.md`
- `docs/references/sidecar-layout-comparison.md`
- `docs/references/showcase-variant-ui-notes.md`

验收：

- 每个借鉴点都落成 OpenAver 自己的需求，不直接复制实现。
- 至少形成 5 条可执行优化项。

## 测试计划

优先补单元与集成契约：

- `tests/unit/test_filename_identity.py`
- `tests/unit/test_sidecar_paths.py`
- `tests/integration/test_scanner_variants.py`
- `tests/integration/test_showcase_work_grouping.py`
- `tests/integration/test_api_filename.py`
- `tests/unit/test_capabilities.py`

样本矩阵最低覆盖：

```text
SONE-103.mp4
sone-103.mp4
SONE_103.mp4
sone103.mp4
SONE-103-C.mp4
SONE_103_c.mp4
SONE-103-U.mp4
SONE-103-UC.mp4
SONE-103-1.mp4
SONE-103-A.mp4
FC2-PPV-1723984.mp4
fc2_ppv_1723984.mp4
HEYZO-0981.mp4
120415_201.mp4
041417-413.mp4
```

## 风险与决策点

- `-a` 可能表示 part，也可能是版本标记，需要通过样本验证后决定默认策略。
- `-u` 的“破解”语义是本项目自定义规则，需要避免和既有 uncensored_mode 混淆。
- 集中 sidecar 会影响 Jellyfin/Kodi 兼容性，可能需要 alongside 与 centralized 双模式长期并存。
- 作品聚合如果直接改 DB 表，迁移风险较高，建议先查询层 group，再做 schema migration。
- capabilities manifest 需要同步更新，否则 AI agent 会继续按旧字段调用。

## 推荐实施顺序

1. 建样本矩阵和 `core/filename_identity.py`。
2. 替换所有番號解析入口，保证刮削使用 canonical number。
3. 引入结构化 variant flags，修正 `-c/-u/-uc` 语义。
4. 加 sidecar 配置和路径解析 helper。
5. 先在 Showcase 查询层做作品聚合。
6. 再设计 DB migration，把 works 和 video_files 拆开。
7. 更新 capabilities manifest，让 AI agent 能理解新模型。

## 当前迭代可交付物

当前迭代扩展为“可识别、可刮削、可集中存储、可按作品展示”：

- 新增统一文件识别模块。
- 补全文件名样本测试。
- 修改 `parse_filename` API 输出。
- 修改搜索/刮削入口使用 canonical number。
- 明确 `-c/-u/-uc/-1/-a` 的解析结果。
- 增加 sidecar 路径配置，并让整理、补全、补剧照写入链路都遵守同一套 resolver。
- Scanner 添加目录时可选择有码/无码，保存到 `gallery.directory_labels`。
- Showcase 查询层先做作品聚合与多文件详情，不立刻拆 DB 表。
- Showcase 和 manifest 暴露 `directory_label`，让 AI agent 能区分本地目录标签、文件变体和搜索信源。

本轮仍暂不做 `works` / `video_files` 大表迁移；等查询层和 UI 行为稳定后再做 schema migration。
