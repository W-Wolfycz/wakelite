# WakeLite

AstrBot 的轻量唤醒插件。在群聊场景下决定 bot 要不要对一条消息做出回复，6 个唤醒信号 + 群白名单 + 唤醒 CD + 复读过滤 + 多 bot 分流。

不引入 Pipeline/BaseStep 框架，不分阶段、不阻塞、不沉默检测、不黑名单。只回答一个问题：**这条消息要不要唤醒 bot？**

## 唤醒源

按代码判定顺序（便宜的先判，任一命中即唤醒并 return）：

| # | 信号 | 触发条件 | 配置项 |
|---|---|---|---|
| 1 | 人格名 | 消息含当前人格 `name`，按概率决定 | `persona_name_prob` |
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
- **唤醒 CD**（`wake_cd`）：每用户独立计时，CD 期内该用户的所有判定都跳过。
- **复读过滤**：用户消息与 bot 最近 N 条回复（去标点后）完全相同时直接拦截，不进入唤醒判定。

## 依赖

- **jieba**（必需）—— 答疑/无聊/兴趣/相关性都依赖中文分词
- **[chat_memory](https://github.com/W-Wolfycz/chat_memory) v2.3+**（可选，推荐）—— bot 历史回复的数据源。未安装或 `use_chat_memory=false` 时回退到 AstrBot 自带 history（此时 `bot_msgs_ttl` 不生效）

## 安装

把本目录放到 AstrBot 的 `data/plugins/wakelite/`，重启或重载插件。依赖 `jieba` 会自动安装（见 `requirements.txt`）。

## 配置

15 个配置项，按相关性分组（WebUI 顺序一致）：

### 准入 / 数据源
- `whitelist_groups`（list）—— 群号列表。空列表 = 所有群都不处理
- `bots`（list of `platform_id:self_id` 字符串，默认空）—— 多 bot 分流配置。空 = 关闭分流。详见下方「多 bot 分流」段
- `use_chat_memory`（bool，默认 true）—— bot 历史是否走 chat_memory 插件

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
- `bot_msgs_maxlen`（int，默认 5）—— 用于复读检测和相关性的 bot 回复最大条数
- `bot_msgs_ttl`（分钟，默认 10）—— bot 回复过期时间，0 = 不过期。仅在 `use_chat_memory=true` 时生效

### 全局
- `wake_cd`（秒，默认 0.5）—— 同一用户两次唤醒的最小间隔，0 = 关闭

### 日志
- `log_config.log_with_bot_id`（bool，默认 true）—— 日志前缀变为 `[WakeLite:self_id]`（如 `[WakeLite:BOT1]`）
- `log_config.debug_to_info`（bool，默认 false）—— debug 日志以 info 级别输出，便于查看拦截/分流判定

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

**多 bot 共享状态语义**（影响调参决策）：

- **唤醒 CD 按用户计**：bot A 唤醒用户 X 后，CD 期内所有 bot（包括 B、C）对该用户的消息都会被拦截。默认 0.5 秒影响很小；调到 5 秒以上会让用户的连续消息只被第一个唤醒的 bot 处理
- **复读检测跨 bot 一致**：所有 bot 看到同一消息得到相同的判定结果
- **配置错误处理**：`self_id` 不在 bots 列表 → 该 bot 的所有阈值/概率判定都跳过；格式错误（不含冒号、字段为空）→ 该项被忽略并打 warning

## 设计取舍

- **不引入 Pipeline/BaseStep**：只有一个 hook + 一个判定函数
- **不持久化状态**：内存里的 `_last_wake` 和 `_persona_name_cache` 重启即清
- **不实现黑名单 / 阻塞 / 指令屏蔽 / 沉默检测 / 防抖**：交由其他插件处理
- **不重复实现 @ 唤醒 / 引用唤醒**：AstrBot 自带，本插件只补充「智能唤醒」
