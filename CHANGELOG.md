# 更新日志 (CHANGELOG)

本插件为 **astrbot_plugin_systemone_keyreply**（v1.0.3 起由 `astrbot_plugin_typesafe_keyreply` 改名而来），在 KeyReply 与 TypeSafe 智能自动回复两个项目基础上重构而来，
详见 [README 的致谢与来源声明](README.md#致谢与来源声明)。以下记录从本插件自身 1.0.0 起的变更。

## [1.0.4] - 2026-09-23

### 修复

- **插件名与插件页面名在 WebUI 里显示成原始 id**：`metadata.yaml` 补齐 `display_name` 与 `short_desc`。
  AstrBot 的展示名解析顺序是「i18n 的 `metadata` 覆盖 → `metadata.yaml` 的
  `display_name` / `short_desc` / `desc` → 机器标识 `name`」，此前第二环缺失，
  只要 i18n 未命中（版本不支持、界面语言不匹配或安装副本不同步），展示名就会掉到最后一环。
- 语言文件补全 `metadata.short_desc` 与 `metadata.desc`（zh-CN 与 en-US），
  英文界面下不再回落到中文描述。

### 测试

- 新增 `tests/test_i18n_consistency.py`（37 项）：守住展示名链条、i18n 顶层键白名单、
  `pages/<目录>` 与 i18n `pages` 键双向对齐、`_page.json` 的 `i18n_key` 可解析，
  以及 zh-CN 的 metadata 与 metadata.yaml 不漂移。

## [1.0.3] - 2026-09-23

### 改名

- 插件名 `astrbot_plugin_typesafe_keyreply` → **`astrbot_plugin_systemone_keyreply`**：仓库地址、
  数据目录、Web API 路由与热重载所用的插件名同步更新，插件名收敛为 `main.py` 里的单一常量 `PLUGIN_NAME`。
  > 术语说明：**SystemOne 是 TypeSafe 提出的大模型规范**（接口路径 `/v1/systemone`，模型 Jev System One），
  > 不是品牌；厂商、SDK、控制台与官方域名仍写作 TypeSafe。
- 插件显示名改为「SystemOne 智能自动回复」，配置中心页面 id `typesafe-console` → `systemone-console`。
- 配置键 `typesafe_api_key` / `typesafe_base_url` / `typesafe_timeout` / `typesafe_model` /
  `typesafe_custom_model` → `systemone_*`；`_conf_schema.json` 与配置中心里的文案同步改为 SystemOne。
- 指令 `/typesafe_status`、`/typesafe_test` → **`/systemone_status`**、**`/systemone_test`**。
- 类与模块：`TypeSafeAutoReplyPlugin` → `SystemOneKeyReplyPlugin`、
  `TypeSafeClientWrapper` → `SystemOneClientWrapper`、`typesafe_client.py` → `systemone_client.py`，
  日志前缀 `[TypeSafe]` → `[SystemOne]`。
- API Key 示例由 `ts_...` 改为 `sk_...`。

### 升级兼容

- **老配置自动迁移**：启动时若发现旧键（`typesafe_*`）而对应新键为空，会就地搬迁并保存配置，
  老用户升级后无需重填；旧键保留不删，新键已有值时以新键为准。
- **旧数据目录自动迁移**：启动时若 `data/plugin_data/astrbot_plugin_typesafe_keyreply/` 存在
  而新目录还没有问答表，会自动复制过去（只补缺、不覆盖），旧文件保留。
- 指令名**不保留旧别名**：升级后请改用 `/systemone_status` 与 `/systemone_test`。

### 测试

- 新增改名迁移测试 `tests/test_migration.py`：覆盖配置键迁移、新旧键优先级、数据目录迁移、
  已有数据不被覆盖与全新安装路径。
- 原有测试、页面冒烟与配置三方一致性测试已全部跟随新命名更新。

## [1.0.2] - 2026-09-22

### 新增

- **TypeSafe API Base URL**：新增 `typesafe_base_url` 配置，可把请求指向自建反代或网关。
  该地址只需填到域名（或反代根路径），**插件会自动在其后追加 `/v1/systemone` 后缀**——
  配置项的提示与页面说明都写明了这一点。误填完整接口地址、漏写 `https://` 或带了结尾斜杠
  都会被自动纠正；留空则使用官方地址 `https://api.typesafe.ai`。
  运行状态页与 `/typesafe_status` 会展示实际生效的 API 地址；旧版 typesafe-sdk 不认识该参数时
  会自动回退官方地址并在页面与日志中提示，不会导致插件不可用。
- 插件简介明确为「固定问答表驱动的自动关键词回复插件。消息先经本地正则召回，
  命中后由 TypeSafe AI 判定是否真提问，再回复标准答案。」

### 测试

- 新增 Base URL 专项测试（43 项）与配置三方一致性、页面冒烟的对应断言；
  测试桩改为自带（`tests/_stubs.py`），无需再手工准备 PYTHONPATH。

## [1.0.0] - 2026-09-22

首个版本。固定问答表驱动的自动回复插件：**本地正则召回 → Jev 相关性判定 → 回复标准答案**。

### 核心流程

- **本地正则召回**：用问答表中的问题 Q 做正则匹配，支持 KeyReply 风格的 `%` 通配符；
  未命中直接静默，**不调用 Jev、不调用 LLM**，零 API 开销。
- **Jev 相关性判定**：命中后把问题、答案与附加说明一并交给 TypeSafe AI（Jev System One），
  二分类判断当前消息是**真提问**还是**假命中**（仅字面上包含关键词）。判定失败一律判为不相关，宁可静默。
- **回复标准答案**：答案 A 是唯一事实来源，可由 LLM 围绕其生成自然表述，也可原样发送；
  生成失败时自动回退为原样发送 A。

### 问答表管理

- 支持 KeyReply 的 `%` 通配语义，其余字符按字面量匹配；导入历史数据时用 `ast.literal_eval` 安全解析其 repr 键。
- **答案为中心**的编辑方式：一个答案只写一次，其下可挂多个问法（多 Q 一 A）。
- **附加判定增强（hint）**：为每条答案补充只给 Jev 看的语境说明，帮助区分容易混淆的情况。
- **逐条 Jev 判定开关**：每条问答对可单独选择「Jev 判定」或「直接回复」，后者完全不产生 API 调用。
- **三级作用域**：全局默认表、按群独立表、按私聊独立表；一张表可同时服务多个群，未单独配置的会话回退全局表。
- **一键复制 KeyReply 问答表**：自动探测其 `triggers.yml` 并复制到本插件数据目录，之后不再依赖对方。
- **图片答案**：答案可只含图片或图文并存，折叠态显示缩略图，加载失败退化为链接文字。

### WebUI（插件页）

- **配置中心**：9 个分组共 38 项配置，支持改动高亮、单项/整组恢复默认、保存后热重载；API Key 以掩码读取，回传掩码即不修改。
- **固定问答**：答案为中心的分组卡片、多问法管理、Jev 判定开关、KeyReply 一键复制、命中测试。
- **运行状态**：等价于 `/typesafe_status` 的结构化面板，含限流用量与一键连通性测试。
- **在线试判**：输入消息真实跑一遍召回与判定，展示命中条目、判定结论、置信度与理由。
- 页面内确认框替代被沙箱拦截的 `window.confirm`，删除等危险操作可正常二次确认。

### 容错与频控

- TypeSafe 超时、限流（429）、鉴权失败或网络异常自动降级，不影响 AstrBot 消息管道。
- 会话级与用户级独立冷却、最大连续主动回复次数限制、黑白名单（黑名单优先）、消息长度与纯媒体过滤。
- 每分钟滑动窗口限流与可选 TTL 结果缓存。

### 指令

- `/typesafe_status`：查看运行状态、API 就绪情况与生效规则。
- `/typesafe_test <消息>`：测试召回与判定结果，不触发真实回复。

### 与来源项目的关系

- 保留 KeyReply 的基础能力（正则命中回复固定答案），并以「直接回复」模式提供。
- 保留 TypeSafe 智能自动回复的插件骨架、Jev 接入与容错、频控体系，以及回复风格/长度/延时定制。
- 与原「大模型自由回复」流程的主要差异：**回复来源不再由大模型自由生成**，
  而是必须先命中问答表；未命中一律静默。
