# Changelog

## 1.3.2 — 2026-08-30

### 变更
- **多 bot 分流按群重设计**：`bots`（platform_id:self_id 全局池）废弃，键名改为 `group_bots`，每项 `self_id` 或 `self_id:群号`（单段/空群号 = 该 Bot 在所有群参与分流）；分流池按当前群构建，单 Bot 群 100% 响应、多 Bot 群按消息哈希轮流；旧 `bots` 配置不再读取（由 AstrBot 按新 schema 自动清理）
- **拒绝工具按唤醒源可配**：`enable_reject_tool` 改为总开关，新增 `reject_tool_scopes` 多选（默认仅弱信号：概率/无聊/兴趣/相关性）；人格名/答疑默认不注入，唤醒提示与工具注入联动
- **日志前缀两段式**：`log_with_bot_id` 启用后前缀改为 `[WakeLite][bot-self_id]`，模块名段与 bot 标识段并存
- **删除失效的日志配置迁移**：`log_config` 组迁移代码实际未生效（AstrBot 加载前已按新 schema 清理旧组），已删除迁移逻辑与对应兼容承诺；升级后请在 WebUI 确认日志前缀开关
- **健壮性与小优化**：事件级标记统一走 set_extra/get_extra；历史条数上限为 0 时跳过查询；func_tool 非 ToolSet 时安全跳过注入；工具描述补充调用前不输出约束

测试：行为级 55 项全部通过（含拒绝工具链路 15 项、消息入口分支 5 项）

## 1.3.0 — 2026-08-23

### 新增
- **唤醒拒绝工具**：新增 `enable_reject_tool`（默认 false），开启后仅在智能唤醒的本轮向 LLM 注入 `wakelite_decline_reply` 工具；LLM 判断该话题实际不需要自己回复（如用户明确在询问群里另一个人）时可调用放弃本轮回复，发送前拦截、本轮不输出任何内容；@ 或指令唤醒不注入

### 变更
- **日志配置升级**：`log_config` 组扁平化为顶层 `log_with_bot_id`（schema 首位），旧组值在加载时一次性迁移并入顶层并写回删除
- **移除 debug 提级开关**：AstrBot WebUI 插件详情页已支持按插件独立调整日志等级（运行期即时生效），debug 细节统一走 debug 级别输出
- **日志前缀格式调整**：`log_with_bot_id` 启用后前缀从 `[WakeLite:self_id]` 变为 `[WakeLite:bot-self_id]`，bot 标识与模块名并存，定位能力更强
- **移除人格名缓存配置**：`persona_name_cache_ttl` 及其插件级内存缓存删除，人格名每轮实时解析（AstrBot 内部已有 persona 内存列表，查询为毫秒级 SQLite 点查）；人格改名/切换立即生效
- **移除过期时间配置**：`bot_msgs_ttl` 删除（旧配置残留键被忽略），候选不再按时间硬丢弃，只受条数上限约束，旧回复靠时间衰减压低权重
- **候选范围语义调整**：`bot_msgs_maxlen` 显示名改为「历史条数上限」并移至候选范围之后，默认值 5 → 15；全量消息按条数截取最近回复、对话轮按轮数截取最近几轮；新增最大 50 的硬上限保护并更新建议值（全量消息 10-30、对话轮 3-10）；已保存配置保留旧值，可在 WebUI 恢复默认获得新默认
- **相关性计分优化**：候选按年龄做时间衰减（越新权重越高，年龄约 10 分钟权重降半）并加有界分词缓存；配置 hint 全部精简为操作说明（不暴露内部字段）

### 兼容
- 旧 `log_config.log_with_bot_id` 显式设置（含 false）在迁移时保留，不会因新 schema 默认值注入而静默改变
- 迁移失败不阻断插件加载：读时继承旧值兜底，写回失败仅记录 WARNING，下次启动自动重试

测试：行为级 47 项全部通过（含日志配置迁移 7 项、拒绝工具 8 项、时间衰减与候选窗口 7 项、消息入口分支 5 项）

## 1.2.1 — 2026-08-06

### 新增
- 六类唤醒均向当前 LLM 请求追加不写入历史的临时提示：上下文中的其他用户发言（包括鲜明的人物设定、角色扮演和固定口吻）只作背景，不得被模仿、接管或续写；回复以当前生效的人设为准。

## 1.2.0 — 2026-07-17

### 修复
- **人格名跟随当前实际 Persona**：改用 AstrBot `resolve_selected_persona`，按 session 强制人格 → conversation 人格 → provider 默认人格解析；保留默认人格回退
- **相关性恢复窗口 TF-IDF**：把当前消息分别与近期 Bot 回复计算 TF-IDF cosine 并取最高分；每次基于当前候选窗口重建文档频率，不再因 IDF 状态从未更新而退化成普通词频 cosine
- **TTL 时区安全**：优先读取 ChatMemory 的 `created_at_utc`，旧 naive 时间按 UTC 解释，不再依赖宿主机本地时区
- **答疑语气词恢复**：`吗` 不再同时出现在停用词和提问词表；改用插件私有 jieba Tokenizer，并注册内置短语以减少分词拆散
- **配置容错**：概率/阈值自动限制到 0-1，CD 限制到 0-10，负数条数/TTL 归零，非字符串兴趣项被安全忽略
- **上下文标记清洗收紧**：只移除明确的 reasoning 标签，不再删除任意尖括号内容

### 新增
- **历史范围配置**：新增 `history_scope=group/user`，默认 `group`；它只控制候选 Bot 回复来自全群还是当前用户，不读取历史用户消息，也不切分对话段落
- **会话级 CD**：冷却 key 改为 UMO + user_id，避免同一用户在不同群或平台之间互相影响
- **运行状态清理**：定期惰性清理过期 CD 与 Persona 缓存，避免长期运行状态无限增长
- **自动化测试**：新增 10 项测试，覆盖 Persona 解析、ChatMemory scope、UTC TTL、CD 隔离、配置容错、答疑否定与 TF-IDF 相关性

### 兼容
- ChatMemory 对接说明更新为正式版 `1.0+`
- `metadata.yaml` 增加 AstrBot 兼容范围 `>=4.16,<5`
- `jieba` 依赖固定为已验证的 `>=0.42.1,<0.43`

## 1.1.0 — 2026-07-11

### 修复
- **多 bot 分流 hash bug**：`_stable_hash` 输入含 `umo`（每 bot 不同）和 `message_id`（OneBot 不同实现间可能不一致），导致三个 bot 各算各的 hash，全部跳过判定（`mod 3` 独立事件，约 30% 概率三个都不中）。改用 `(group_id, sender_id, content)` 三个跨 bot 一致字段
- **log_with_bot_id 默认值不一致**：代码 default 是 False，schema default 是 True。统一为 True

### 变更
- **适配 chat_memory v2.3+**：v2.3 把 `tag` 列拆为 `llm_status` + `content_kind`，删除 `tag_filter` 参数。wakelite 改读 `llm_status` 字段（`llm_success` 进相关性唤醒基准，其他状态仅入复读检测）
- **`bots` 配置格式简化**：从 `[{platform_id, self_id}, ...]` dict 列表改为 `["platform_id:self_id", ...]` 字符串列表，与 UMO 第一段格式一致。格式错误的项打 warning 并跳过
- **日志前缀格式简化**：开启 `log_with_bot_id` 后从 `[WakeLite:platform_id:self_id]` 简化为 `[WakeLite:self_id]`（如 `[WakeLite:BOT1]`），与 splitter_w 等插件风格一致

### 新增
- **`log_config` 配置组**：
  - `log_with_bot_id`（默认 true）：日志前缀加 `:self_id`，多 bot 实例下区分来源
  - `debug_to_info`（默认 false）：debug 日志以 info 级别输出，无需调整后端日志级别即可查看拦截/分流判定

## 1.0.0 — 2026-07-04

首个版本。聚焦唤醒判定本身，舍弃名单过滤/阻塞/指令屏蔽/沉默/防抖/Pipeline 框架，提供 6 个唤醒源 + 群白名单 + 唤醒 CD + 复读过滤 + 多 bot 分流。

### 新增

- **6 个唤醒源**（按判定顺序，任一命中即唤醒并 return）：
  - **人格名唤醒**：消息含当前人格 `name` 时，按 `persona_name_prob` 概率决定是否唤醒。带 TTL 缓存
  - **概率唤醒**：每条消息按 `prob` 概率兜底
  - **答疑唤醒**：内置词表（请问/为什么/怎么等）+ 否定词衰减 + 反问句衰减 + sigmoid 归一化，分数 > `ask_threshold` 触发
  - **无聊唤醒**：检测「好无聊/死群/有人吗」等冷场信号，分数 > `bored_threshold` 触发
  - **兴趣唤醒**：用户自定义关键词包，按词长加权（1字0.8 / 2字1.2 / 3字1.5 / 4字+1.8），分数 > `interest_threshold` 触发
  - **相关性唤醒**：消息与 bot 最近 N 条回复的 TF-IDF cosine > `similar_threshold` 触发
- **群白名单 gate**（`whitelist_groups`）：仅列表内群聊走判定逻辑，不在列表的群完全不处理
- **唤醒 CD**（`wake_cd`）：每用户独立计时，CD 期内跳过所有判定
- **复读过滤**：用户消息与 bot 历史回复（去标点后）完全相同即拦截，覆盖 `non_llm` 标签的 bot 回复（避免 user/bot 互相复读无限循环）
- **chat_memory v2.3+ 集成**：bot 历史回复从 chat_memory 插件读取，按 `llm_status` 分流（复读用全量 assistant，相关性仅 `llm_success`）。未安装或 `use_chat_memory=false` 时回退到 AstrBot 自带 history
- **TTL 过滤**（`bot_msgs_ttl`）：bot 历史回复超过 N 分钟的记录被忽略。仅在 `use_chat_memory=true` 时生效
- **多 bot 分流**（`bots`）：多 bot 共用一份配置时，把概率/答疑/无聊/兴趣/相关性 5 个唤醒按消息哈希分流到指定 bot，避免一次提问多个 bot 同时响应。人格名/复读/CD/白名单 gate 不参与分流，所有 bot 都跑。配置项格式：每项一行 `platform_id:self_id` 字符串
- **配置项滑块**：概率/阈值类（0-1）+ `wake_cd`（0-10 秒）使用滑块
- **日志可观测性**（`log_config`）：
  - `log_with_bot_id`：日志前缀变为 `[WakeLite:self_id]`（如 `[WakeLite:BOT1]`），默认开启
  - `debug_to_info`：debug 日志提级到 info，便于查看拦截/分流判定

### 设计取舍

- **不引入 Pipeline/BaseStep**：只有一个 hook + 一个判定函数
- **不持久化状态**：重启即清
- **不实现黑名单 / 阻塞 / 指令屏蔽 / 沉默检测 / 防抖**：用户明确「其他暂时舍弃」
- **不重复实现 @ 唤醒 / 引用唤醒**：AstrBot 自带，本插件只补充智能唤醒

### 依赖

- **jieba**（必需）—— 答疑/无聊/兴趣/相关性都依赖中文分词
- **[chat_memory](https://github.com/W-Wolfycz/chat_memory) v2.3+**（可选，推荐）—— bot 历史回复的数据源
