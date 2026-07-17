# WakeLite

AstrBot 的轻量唤醒插件。在群聊场景下决定 bot 要不要对一条消息做出回复，6 个唤醒信号 + 群白名单 + 唤醒 CD + 复读过滤 + 多 bot 分流。

不引入 Pipeline/BaseStep 框架，不分阶段、不阻塞、不沉默检测、不黑名单。只回答一个问题：**这条消息要不要唤醒 bot？**

## 唤醒源

按代码判定顺序（便宜的先判，任一命中即唤醒并 return）：

| # | 信号 | 触发条件 | 配置项 |
|---|---|---|---|
| 1 | 人格名 | 消息含当前实际生效人格的 `name`，按概率决定 | `persona_name_prob` |
| 2 | 概率 | 每条消息兜底随机唤醒 | `prob` |
| 3 | 答疑 | 消息的「提问意图」分数 > 阈值（请问/为什么/怎么等） | `ask_threshold` |
| 4 | 无聊 | 消息的「无聊表达」分数 > 阈值（好无聊/死群/有人吗） | `bored_threshold` |
| 5 | 兴趣 | 命中自定义关键词包，加权分数 > 阈值 | `interest_words` + `interest_threshold` |
| 6 | 相关性 | 消息与 bot 最近 N 条回复 TF-IDF cosine > 阈值 | `similar_threshold` |

**配置项语义分两类**：
- **【概率类】**（`persona_name_prob` / `prob`）：`0=关闭`，越大越易唤醒，范围 0-1
- **【阈值类】**（`ask/bored/interest/similar_threshold`）：`1=关闭`，越大越严，范围 0-1

## 辅助机制

- **群白名单**（`whitelist_groups`）：只有列表内的群聊走唤醒判定；不在列表的群完全不处理。
- **唤醒 CD**（`wake_cd`）：按会话 + 用户独立计时，CD 期内该用户在当前会话的所有判定都跳过。
- **复读过滤**：用户消息与 bot 最近 N 条回复（去标点后）完全相同时直接拦截，不进入唤醒判定。

## 依赖

- **jieba**（必需）—— 答疑/无聊/兴趣/相关性都依赖中文分词
- **[chat_memory](https://github.com/W-Wolfycz/chat_memory) v1.0+**（可选，推荐）—— bot 历史回复的数据源。未安装或 `use_chat_memory=false` 时回退到 AstrBot 自带 history（此时 `bot_msgs_ttl` 不生效）

## 安装

把本目录放到 AstrBot 的 `data/plugins/wakelite/`，重启或重载插件。依赖 `jieba` 会自动安装（见 `requirements.txt`）。

## 配置

16 个配置项，按相关性分组（WebUI 顺序一致）：

### 准入 / 数据源
- `whitelist_groups`（list）—— 群号列表。空列表 = 所有群都不处理
- `bots`（list of `platform_id:self_id` 字符串，默认空）—— 多 bot 分流配置。空 = 关闭分流。详见下方「多 bot 分流」段
- `use_chat_memory`（bool，默认 true）—— bot 历史是否走 chat_memory 插件
- `history_scope`（`group` / `user`，默认 `group`）—— 决定候选 Bot 回复来自全群还是当前发送者；不读取历史用户消息，也不切分对话段落。仅影响 chat_memory 数据源

### 人格名唤醒
- `persona_name_prob`（float，默认 0.5）—— 命中人格名后的唤醒概率
- `persona_name_cache_ttl`（分钟，默认 1）—— 人格名查询缓存时长，0 = 不缓存

### 兜底 / 文本判定
- `prob`（float，默认 0）—— 概率唤醒，建议 0.01-0.1
- `ask_threshold`（float，默认 0.5）—— 答疑唤醒阈值
- `bored_threshold`（float，默认 0.5）—— 无聊唤醒阈值

### 兴趣 / 相关性
- `interest_words`（list）—— 关键词包列表，每项是一行空格分隔的关键词，例：`["原神 风 神 鸡", "崩坏 星穹铁道"]`
- `interest_threshold`（float，默认 0.5）—— 兴趣唤醒阈值
- `similar_threshold`（float，默认 0.5）—— 相关性唤醒阈值

### Bot 历史参数
- `bot_msgs_maxlen`（int，默认 5）—— 最多取多少条近期 Bot 回复作为候选，不包含用户消息
- `bot_msgs_ttl`（分钟，默认 10）—— bot 回复过期时间，0 = 不过期。使用 chat_memory 时优先按明确的 UTC 时间字段过滤

### 全局
- `wake_cd`（秒，默认 0.5）—— 同一会话中同一用户两次唤醒的最小间隔，0 = 关闭

### 日志
- `log_config.log_with_bot_id`（bool，默认 true）—— 日志前缀变为 `[WakeLite:self_id]`（如 `[WakeLite:BOT1]`）
- `log_config.debug_to_info`（bool，默认 false）—— debug 日志以 info 级别输出，便于查看拦截/分流判定

## 历史与相关性如何工作

WakeLite 不会把群聊自动切成“前段/中段/后段”，也不会把历史用户消息送进相关性计算。它只做以下步骤：

1. 根据 `history_scope` 取得近期 Bot 回复候选；
2. 受 `bot_msgs_maxlen` 和 `bot_msgs_ttl` 限制候选数量与时间；
3. 把当前用户消息分别与每条候选 Bot 回复计算当前窗口的 TF-IDF cosine；
4. 取最高相似度，与 `similar_threshold` 比较。

例如：

```text
用户 A：原神怎么配队？
Bot：可以围绕火水元素反应配队。
用户 B：今天天气不错。
Bot：确实适合出去走走。
用户 A：那水系角色选谁？
```

- `history_scope=group`：最后一条会同时与两条 Bot 回复比较，通常第一条取得更高分；
- `history_scope=user`：最后一条只与由用户 A 触发的第一条 Bot 回复比较；
- 用户 B 的“今天天气不错”本身不会进入比较，只有 Bot 对它的回复可能成为候选。

`history_scope` 只决定候选回复来自全群还是当前用户；TF-IDF 只负责给这些候选计算相关程度。

## 多 bot 分流

`bots` 配置项允许用户列出所有 bot 实例（每项一行 `platform_id:self_id` 字符串，如 `"BOT1:10001"`），让多 bot 共用一份配置时按消息哈希分流，避免一次提问多个 bot 同时响应。

**字段来源**：

- `platform_id`：AstrBot 配置里给平台实例起的 ID。event_bus 日志 `[X(aiocqhttp)]` 中括号里的 X 就是它
- `self_id`：bot 自身的 QQ 号。bot 发消息时，其他 bot 收到这条消息看到的 sender_id 就是该 bot 的 self_id

**示例**（多 bot 共用一个群）：

```json
["BOT1:10001", "BOT2:10002", "BOT3:10003"]
```

**分流规则**：

| 信号 | 是否参与分流 |
|---|---|
| 人格名唤醒 | ❌ 所有 bot 都跑（叫谁谁响应） |
| 概率/答疑/无聊/兴趣/相关性 | ✅ 哈希分流到唯一 bot |
| 复读过滤 / 唤醒 CD / 群白名单 gate | ❌ 所有 bot 都跑 |

**多 bot 状态语义**（影响调参决策）：

- **唤醒 CD 按 UMO + 用户计**：不同平台实例、群聊和私聊之间不共享 CD，避免用户在一个群触发后影响另一个群
- **历史范围可配置**：`history_scope=group` 时同一 UMO 内不同用户可接续 Bot 话题；`user` 时只看当前发送者触发的 Bot 回复。不同平台实例仍按各自 UMO 隔离
- **配置错误处理**：`self_id` 不在 bots 列表 → 该 bot 的所有阈值/概率判定都跳过；格式错误（不含冒号、字段为空）→ 该项被忽略并打 warning

## 设计取舍

- **不引入 Pipeline/BaseStep**：只有一个 hook + 一个判定函数
- **不持久化状态**：内存里的 `_last_wake` 和 `_persona_name_cache` 重启即清；运行中会惰性清理过期项
- **不实现黑名单 / 阻塞 / 指令屏蔽 / 沉默检测 / 防抖**：交由其他插件处理
- **不重复实现 @ 唤醒 / 引用唤醒**：AstrBot 自带，本插件只补充「智能唤醒」
