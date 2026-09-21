# AstrBot TypeSafe AI 智能自动回复插件

## 1. 项目概述

### 1.1 插件名称

建议：

`astrbot_plugin_typesafe_autoreply`

中文名称：

**TypeSafe 智能自动回复**

### 1.2 核心目标

让 AstrBot 不再依赖简单的：

- @机器人
- 固定关键词
- 固定概率
- 命令前缀

来决定是否回复消息。

插件监听用户消息后，首先调用 **TypeSafe AI** 对消息进行结构化判断：

> **“这条消息是否值得/需要机器人主动回复？”**

只有 TypeSafe AI 判定需要回复时，才调用 AstrBot 已配置的 LLM Provider 生成真正的回复。

核心流程：

用户消息  
↓  
黑白名单 / 基础规则检查  
↓  
TypeSafe AI 意图判断  
↓  
是否需要 AI 回复？  
├─ 否 → 静默  
└─ 是  
　↓  
选择 AstrBot LLM  
　↓  
根据风格、长度、上下文生成回复  
　↓  
发送消息

这样可以显著降低群聊中机器人乱插话、无意义回复以及不必要的 LLM Token 消耗。

---

# 2. 技术方案

## 2.1 技术栈

| 模块 | 方案 |
|---|---|
| 插件框架 | AstrBot Star Plugin |
| 开发语言 | Python |
| 判断引擎 | TypeSafe AI |
| TypeSafe SDK | `typesafe-sdk` |
| 回复模型 | AstrBot 已配置 LLM Provider |
| 插件配置 | `_conf_schema.json` |
| 配置读取 | `AstrBotConfig` |
| LLM 调用 | `context.llm_generate()` |
| 插件入口 | `main.py` |
| 依赖管理 | `requirements.txt` |

TypeSafe AI 只负责：

**判断要不要回复 / 消息属于什么类型。**

真正生成回答仍然使用 AstrBot 中已经配置好的模型。

---

# 3. 设计原则

插件采用“两阶段 AI”设计。

## 第一阶段：TypeSafe AI

TypeSafe AI 作为轻量级的**消息路由器 / 意图分类器**。

例如输入：

> 有人知道 Windows 11 怎么关闭 BitLocker 吗？

TypeSafe 返回结构化结果：

```text
should_reply = true
category = question
confidence = high
```

然后插件才进入 AstrBot LLM。

如果用户说：

> 哈哈哈哈哈

则：

```text
should_reply = false
category = casual_chat
confidence = high
```

插件直接结束。

这样避免每条群消息都调用完整 LLM。

---

# 4. TypeSafe AI 判断模型

建议不要只判断：

```text
true / false
```

而是一次得到多个结构化字段。

建议设计为：

```text
should_reply
reply_type
confidence
reason
urgency
```

例如：

```json
{
  "should_reply": true,
  "reply_type": "question",
  "confidence": "high",
  "reason": "用户提出了明确问题",
  "urgency": "normal"
}
```

其中 `reason` 默认只用于日志和调试，不发送给用户。

---

# 5. 自动回复类型

插件设置中提供：

**自动回复类型**

用户可以多选需要机器人介入的消息类型。

建议默认类型：

| 类型 | 示例 | 默认 |
|---|---|---|
| 明确提问 | “这个怎么设置？” | 开 |
| 求助 | “有人能帮我看看吗？” | 开 |
| 技术问题 | “Docker 为什么启动失败？” | 开 |
| 信息查询 | “今天有什么更新？” | 开 |
| 建议请求 | “大家推荐什么 NAS？” | 开 |
| 讨论 | “你们觉得这个方案怎么样？” | 可选 |
| 闲聊 | “今天好累啊” | 关 |
| 情绪表达 | “气死我了” | 关 |
| 玩笑 | “哈哈哈哈” | 关 |
| 陈述 | “我已经处理好了” | 关 |
| 打招呼 | “早上好” | 可选 |
| 感谢 | “谢谢” | 关 |
| 告别 | “晚安” | 关 |

用户可以决定：

> 哪些类型允许机器人主动参与。

---

# 6. TypeSafe AI 设置

AstrBot WebUI：

插件 → TypeSafe 智能自动回复 → 配置

建议提供：

### TypeSafe API

**启用 TypeSafe AI**

默认：

`true`

---

**TypeSafe API Key**

密码类型配置。

例如：

```text
ts_xxxxxxxxx
```

---

**TypeSafe API 超时时间**

默认：

```text
10 秒
```

---

**失败处理**

可选：

```text
静默，不回复
按基础规则判断
直接交给 AstrBot
```

默认：

**静默，不回复**

避免 TypeSafe API 故障导致机器人突然回复整个群。

---

# 7. AstrBot LLM 设置

## 回复模型

提供：

```text
跟随当前会话模型
指定模型
```

默认：

**跟随当前会话模型**

AstrBot 当前支持获取当前会话使用的聊天 Provider ID，然后通过统一 `llm_generate()` 接口调用模型。

如果选择：

**指定模型**

则配置界面提供 AstrBot Provider 选择器。

例如：

```text
OpenAI GPT
Gemini
DeepSeek
Claude
本地 Ollama
……
```

具体显示 AstrBot 当前已经配置的 Provider。

不单独保存：

```text
API Key
Base URL
Model Name
```

避免重复维护模型配置。

---

# 8. 回复长度

提供预设：

```text
极短
简短
正常
详细
自定义
```

建议：

| 模式 | 目标 |
|---|---|
| 极短 | 20～50 字 |
| 简短 | 50～100 字 |
| 正常 | 100～250 字 |
| 详细 | 250～500 字 |
| 自定义 | 用户设置 |

另外提供：

**最大回复字数**

例如：

```text
200
```

注意：

LLM 无法保证严格字数，因此实现方式采用：

Prompt 约束 + 输出截断保护。

---

# 9. 回复风格

预设：

```text
自然
简洁
专业
友好
幽默
活泼
严谨
群友风格
自定义
```

例如：

### 自然

像普通群成员一样自然回答，不使用过于正式的表达。

### 简洁

优先直接回答问题，不解释无关背景。

### 专业

使用准确、结构化、专业的表达。

### 群友风格

降低机器人感，不主动长篇解释。

---

# 10. 自定义 Prompt

高级设置提供：

**回复附加提示词**

例如：

```text
你正在群聊中参与讨论。
回答尽量自然简洁。
除非必要，不使用 Markdown 标题。
不要主动说明自己是 AI。
```

该 Prompt 追加到自动回复 Prompt 中。

---

# 11. 黑白名单

建议不要只做一个名单，而是分为：

## 会话白名单

只在指定：

```text
群
频道
私聊
```

启用自动回复。

支持填写 AstrBot Session ID。

---

## 会话黑名单

指定群 / 频道永远不自动回复。

优先级：

```text
黑名单 > 白名单 > 普通规则
```

---

## 用户白名单

指定用户发送消息时允许自动回复。

---

## 用户黑名单

指定用户永远不触发自动回复。

例如：

```text
机器人账号
其他 Bot
管理员测试账号
```

---

# 12. 黑白名单模式

提供：

```text
全部允许
仅白名单
黑名单排除
白名单 + 黑名单
```

默认：

**黑名单排除**

这样安装插件后不需要逐个添加群。

---

# 13. 群聊 / 私聊控制

分别设置：

```text
启用群聊自动回复
启用私聊自动回复
```

默认建议：

```text
群聊：开启
私聊：关闭
```

因为私聊通常已经可以直接使用 AstrBot 的正常聊天逻辑。

---

# 14. @机器人行为

增加：

**被 @ 时是否绕过 TypeSafe 判断**

默认：

`true`

流程：

```text
@机器人
   ↓
直接进入 AstrBot LLM
```

而普通群消息：

```text
普通消息
 ↓
TypeSafe
 ↓
判断是否主动回复
```

这样不会影响 AstrBot 原本的正常聊天体验。

---

# 15. 回复机器人名称

增加：

**机器人名字/别名检测**

例如：

```text
小白
机器人
AI
助手
```

如果消息：

> 小白你怎么看？

可以视为高优先级回复。

配置：

```text
检测机器人名称：开启

别名：
小白
AI
机器人
助手
```

---

# 16. 上下文判断

这是插件比较重要的增强功能。

如果只发送当前消息给 TypeSafe：

> 怎么弄？

AI 很难判断。

因此提供：

**TypeSafe 上下文消息数量**

例如：

```text
0
3
5
10
```

默认：

`3`

TypeSafe 判断内容变为：

```text
最近群聊：

A：Docker升级后启动不了了
B：日志是什么？
A：提示 permission denied
C：怎么弄？

当前消息：
C：怎么弄？
```

TypeSafe 可以更准确判断机器人是否应该介入。

---

# 17. 上下文过滤

提供：

```text
忽略机器人自己的消息
忽略命令消息
忽略纯图片消息
忽略系统消息
```

防止无意义上下文进入 TypeSafe。

---

# 18. 回复概率

即使 TypeSafe 判断：

```text
should_reply = true
```

仍然可以设置最终参与概率。

例如：

```text
100%
80%
50%
20%
```

主要适用于活跃群聊。

例如设置：

```text
80%
```

意味着 TypeSafe 判断应该回复后，机器人仍只有 80% 概率参与。

默认：

`100%`

---

# 19. 冷却时间

防止机器人连续刷屏。

提供：

**同一会话回复冷却**

例如：

```text
0
10
30
60
120 秒
```

默认：

`30 秒`

例如：

机器人刚主动回答一次。

30 秒内其他普通聊天不会再次主动触发。

但：

```text
@机器人
```

可以绕过冷却。

---

# 20. 用户冷却

另外增加：

```text
同一用户主动回复冷却
```

默认：

`30 秒`

避免同一个用户连续触发机器人。

---

# 21. 连续回复限制

配置：

```text
最大连续主动回复次数
```

默认：

`2`

例如机器人连续参与两轮后：

```text
机器人
用户
机器人
用户
机器人
```

下一次默认不主动介入。

只有：

```text
@机器人
```

或明确调用 AstrBot 时恢复。

---

# 22. 消息长度过滤

设置：

```text
最短检测长度
最长检测长度
```

例如：

```text
最短：2
最长：2000
```

像：

```text
嗯
哦
6
。
```

可以直接跳过 TypeSafe API。

节省请求。

---

# 23. 关键词规则

增加本地规则层，优先于 TypeSafe。

## 强制回复关键词

例如：

```text
有人知道
怎么解决
求助
请问
怎么办
```

匹配后：

```text
直接进入 TypeSafe
```

或者可设置：

```text
直接回复
```

---

## 禁止回复关键词

例如：

```text
机器人别说话
不用机器人回答
```

匹配后直接跳过。

---

# 24. 正则规则

高级用户可以设置：

```text
强制触发正则
忽略正则
```

例如：

```regex
^(有人知道|请问|求助)
```

用于降低 TypeSafe 请求数量。

---

# 25. TypeSafe 判断阈值

TypeSafe 返回：

```text
confidence
```

可以设置：

```text
最低回复置信度
```

例如：

```text
high
medium
low
```

默认：

`medium`

只有：

```text
should_reply = true
confidence >= medium
```

才进入 LLM。

---

# 26. TypeSafe 判断 Prompt

核心规则建议设计为：

```text
你负责判断群聊中的一条消息是否适合由 AI 助手主动回复。

需要回复：
- 用户提出明确问题
- 用户正在寻求帮助
- 用户询问信息
- 用户希望获得建议
- 用户的问题当前没有其他成员明显回答
- AI 能提供明显有价值的信息

通常不要回复：
- 普通闲聊
- 单纯情绪表达
- 哈哈、好的、谢谢等简单回应
- 用户之间已经正常交流
- 明显针对其他人的问题
- 不需要答案的陈述
- 无意义短消息
- 机器人插话会显得突兀的情况

原则：

宁可少回复，也不要乱插话。
只有机器人能够明显提供帮助时才主动参与。
```

实际调用时使用 TypeSafe 的结构化 Choice，而不是要求模型自己输出 JSON。

---

# 27. 回复 Prompt

如果 TypeSafe 判定需要回复，则构造：

```text
你正在参与一个真实的群聊。

当前用户消息：
{message}

最近聊天：
{context}

回复要求：

风格：
{reply_style}

目标长度：
{reply_length}

额外要求：
{custom_prompt}

请直接回复用户。

不要：
- 解释为什么你决定回复
- 输出分类结果
- 输出 TypeSafe 判断
- 使用“根据你的问题”等机械开场
- 主动说明自己是 AI

回复应该自然地融入当前群聊。
```

---

# 28. 消息处理完整流程

最终推荐流程：

```text
收到消息
 │
 ├─ 插件是否启用？
 │
 ├─ 是否机器人自己消息？
 │
 ├─ 是否系统消息？
 │
 ├─ 是否 AstrBot 命令？
 │
 ├─ 群聊/私聊是否启用？
 │
 ├─ 黑名单检查
 │
 ├─ 白名单检查
 │
 ├─ 用户黑名单
 │
 ├─ 消息长度检查
 │
 ├─ 忽略关键词/正则
 │
 ├─ 是否 @机器人？
 │       └─ 是 → 根据设置直接进入 LLM
 │
 ├─ 是否命中机器人名称？
 │
 ├─ 冷却检查
 │
 ├─ 获取最近聊天上下文
 │
 ▼
TypeSafe AI
 │
 ├─ should_reply = false
 │       ↓
 │      结束
 │
 ├─ 类型是否允许？
 │
 ├─ confidence 是否达标？
 │
 ├─ 回复概率检查
 │
 ▼
选择 AstrBot Provider
 │
 ▼
生成 Prompt
 │
 ▼
AstrBot LLM
 │
 ▼
长度保护
 │
 ▼
发送回复
 │
 ▼
更新冷却状态
```

---

# 29. WebUI 配置结构

建议按照以下顺序显示。

### 基础设置

```text
启用插件
群聊自动回复
私聊自动回复
```

### TypeSafe AI

```text
TypeSafe API Key
API 超时
失败处理
最低判断置信度
```

### 回复类型

```text
明确提问
求助
技术问题
信息查询
建议请求
讨论
闲聊
情绪
打招呼
其他
```

### 回复模型

```text
跟随当前模型 / 指定模型
AstrBot Provider
```

### 回复设置

```text
回复长度
最大字数
回复风格
自定义 Prompt
```

### 智能判断

```text
上下文消息数量
@机器人绕过判断
机器人名称检测
机器人别名
```

### 防刷屏

```text
会话冷却
用户冷却
最大连续主动回复
回复概率
```

### 黑白名单

```text
名单模式
会话白名单
会话黑名单
用户白名单
用户黑名单
```

### 高级过滤

```text
最短消息长度
最长消息长度
忽略命令
忽略 Bot
忽略纯媒体消息
强制触发关键词
忽略关键词
强制触发正则
忽略正则
```

### 调试

```text
调试日志
记录 TypeSafe 判断结果
记录跳过原因
```

---

# 30. 日志系统

建议日志：

```text
[TypeSafe] 收到消息: 怎么安装Docker？
[Filter] 会话允许
[TypeSafe] category=technical_question
[TypeSafe] should_reply=true
[TypeSafe] confidence=high
[LLM] provider=xxx
[Reply] generated=86 chars
```

不回复：

```text
[TypeSafe] 收到消息: 哈哈哈
[TypeSafe] category=casual_chat
[TypeSafe] should_reply=false
[Skip] no_reply
```

生产环境默认不输出完整消息正文，避免日志中积累隐私内容。

---

# 31. API 故障保护

TypeSafe API：

```text
Timeout
429
5xx
网络异常
API Key错误
```

均不能导致 AstrBot 消息处理崩溃。

采用：

```text
try
 ↓
TypeSafe
 ↓
except
 ↓
按照 failure_mode 处理
```

默认：

```text
静默跳过
```

---

# 32. API 请求节流

增加：

```text
最大 TypeSafe 请求/分钟
```

例如：

```text
60
```

防止大型群聊产生大量 API 调用。

达到限制：

```text
跳过自动回复
```

不影响 AstrBot 正常 @ / 命令功能。

---

# 33. TypeSafe 判断缓存

可以增加短时间缓存。

例如相同消息：

```text
Docker怎么安装？
```

在：

```text
60 秒
```

内重复出现时直接复用结果。

默认关闭或设置较短 TTL，避免不同上下文被错误复用。

---

# 34. 插件指令

建议提供：

```text
/typesafe_status
```

显示：

```text
TypeSafe 自动回复：开启
TypeSafe API：正常
群聊回复：开启
回复模型：当前会话模型
风格：自然
长度：简短
冷却：30 秒
```

管理员可使用：

```text
/typesafe_test 你好，请问Docker怎么安装？
```

返回：

```text
是否回复：是
类型：技术问题
置信度：高
```

只测试 TypeSafe，不真正回复。

---

# 35. 项目目录

建议：

```text
astrbot_plugin_typesafe_autoreply/
│
├── main.py
├── metadata.yaml
├── _conf_schema.json
├── requirements.txt
├── README.md
├── CHANGELOG.md
├── LICENSE
│
├── typesafe_client.py
├── classifier.py
├── reply_engine.py
├── filters.py
├── context_manager.py
└── utils.py
```

职责：

```text
main.py
AstrBot 生命周期、事件监听。

typesafe_client.py
TypeSafe API 封装。

classifier.py
消息分类和 should_reply 判断。

reply_engine.py
AstrBot LLM 调用。

filters.py
黑名单、白名单、关键词、正则、长度过滤。

context_manager.py
群聊上下文管理。

utils.py
冷却、概率、缓存等公共功能。
```

避免所有代码堆在 `main.py`。

---

# 36. requirements.txt

计划至少包含：

```text
typesafe-sdk
```

尽量避免增加不必要依赖。

HTTP、异步等优先使用 AstrBot/Python 环境已有能力。

---

# 37. metadata.yaml

插件信息：

```text
name:
astrbot_plugin_typesafe_autoreply

display_name:
TypeSafe 智能自动回复

description:
使用 TypeSafe AI 判断聊天消息是否需要 AI 主动参与，并调用 AstrBot 已配置的大语言模型生成自然回复。
```

AstrBot 版本范围将在实际开发时根据最终使用 API 确定。

---

# 38. 与参考插件的关系

参考：

astrbot-plugins-mj

主要借鉴：

```text
main.py
metadata.yaml
_conf_schema.json
WebUI 配置方式
事件监听结构
```

但本插件复杂度明显更高，因此不会直接把全部功能堆在一个 `main.py` 中。

参考插件本身属于：

```text
消息
 ↓
关键词匹配
 ↓
固定回复
```

本插件升级为：

```text
消息
 ↓
规则过滤
 ↓
上下文获取
 ↓
TypeSafe AI
 ↓
结构化意图判断
 ↓
AstrBot LLM
 ↓
自然回复
```

---

# 39. 与 AstrBot 原生主动回复的关系

AstrBot 本身已经存在主动回复相关配置。

本插件不建议修改 AstrBot 核心主动回复逻辑。

插件采用独立机制：

```text
AstrBot 原生聊天
+
TypeSafe 智能主动回复
```

这样：

- 不修改 AstrBot 源码
- 插件可以随时关闭
- AstrBot 升级影响较小
- 卸载插件即可恢复原状

建议用户使用本插件时关闭 AstrBot 原生概率主动回复，避免两套机制同时触发。

---

# 40. 第一版开发范围

建议 v1.0 一次完成核心功能，而不是制作残缺 Demo。

必须包含：

1. TypeSafe API Key 配置
2. TypeSafe 结构化消息判断
3. 自动回复类型选择
4. AstrBot 当前 LLM Provider
5. 指定 AstrBot LLM Provider
6. 回复长度
7. 回复风格
8. 自定义 Prompt
9. 群聊/私聊开关
10. 会话黑白名单
11. 用户黑白名单
12. @机器人绕过
13. Bot 名称检测
14. 上下文判断
15. 回复概率
16. 会话冷却
17. 用户冷却
18. 消息长度过滤
19. 关键词过滤
20. 正则过滤
21. TypeSafe API 异常保护
22. 请求频率限制
23. 调试日志
24. `/typesafe_status`
25. `/typesafe_test`

---

# 41. 后续版本可增加

后续 v1.1 / v1.2 可以考虑：

**分群配置**

例如：

```text
技术群
→ 专业
→ 200字
→ DeepSeek

闲聊群
→ 群友
→ 60字
→ Gemini

客服群
→ 正式
→ 300字
→ GPT
```

以及：

**时间段控制**

```text
工作时间开启
夜间关闭
```

**群活跃度感知**

群消息非常活跃时降低机器人插话概率。

群长时间没人回复问题时提高机器人介入概率。

**统计面板**

统计：

```text
检测消息数
TypeSafe 调用数
判定回复数
实际回复数
跳过数
API 错误数
各回复类型占比
```

如果以后需要统计面板，再使用 AstrBot Plugin Pages；v1.0 配置本身优先使用 `_conf_schema.json`，保持插件简单。

---

# 42. 最终定位

这个插件不是：

> “看到消息就让 AI 回答。”

而应该定位成：

> **AstrBot 的 AI 发言决策层。**

整体架构：

```text
             ┌──────────────┐
             │ 用户聊天消息 │
             └──────┬───────┘
                    ↓
            ┌───────────────┐
            │ 本地规则过滤器 │
            └──────┬────────┘
                   ↓
            ┌───────────────┐
            │   TypeSafe AI │
            │ 是否值得回复？ │
            └──────┬────────┘
                   ↓
              YES / NO
              ↓       ↓
         AstrBot LLM  静默
              ↓
           自然回复
```

设计核心原则：

**少打扰、能帮忙、像正常群成员一样参与讨论。**

TypeSafe AI 负责决定：

**“该不该说话。”**

AstrBot LLM 负责决定：

**“应该说什么。”**

这两个职责完全分离。

