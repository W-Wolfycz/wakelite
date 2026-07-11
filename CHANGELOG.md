# Changelog

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
