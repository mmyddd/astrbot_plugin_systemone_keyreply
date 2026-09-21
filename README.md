# AstrBot TypeSafe AI 智能自动回复插件

[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-blue.svg)](https://github.com/AstrBotDevs/AstrBot)
[![TypeSafe](https://img.shields.io/badge/TypeSafe-Jev%20System%20One-brightgreen.svg)](https://typesafe.ai)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> **AstrBot 的 AI 发言决策层：少打扰、能帮忙、像正常群成员一样自然交流。**

让 AstrBot 不再仅依赖死板的 `@机器人`、固定关键词、无脑概率或命令前缀。通过引入 **TypeSafe AI (Jev System One)** 模型作为轻量级消息路由器与意图判定器，精准识别群聊中哪些消息“真正值得/需要 AI 回复”，再调度 AstrBot 已配置的大语言模型生成自然流利的回复。

---

## 核心特性

- 🧠 **两阶段 AI 架构**：
  - **第一阶段（决策层）**：由 TypeSafe AI 进行毫秒级结构化裁决（是否主动回复、消息意图分类、置信度评估、紧急程度）。
  - **第二阶段（生成层）**：判定需要回复后，才调用 AstrBot 已配置的大语言模型（OpenAI、Gemini、DeepSeek、Claude、本地 Ollama 等）生成自然回复。
- 🛡️ **多级防乱插话机制**：
  - 会话级与用户级独立冷却时间（Cooldown）。
  - 最大连续主动回复次数限制（避免机器人自言自语刷屏）。
  - 黑白名单（会话/用户，黑名单 > 白名单）。
  - 消息长度、纯表情/图片过滤、指令过滤。
- 🎯 **14 种意图类型精确控制**：
  - 支持多选允许机器人介入的类型：明确提问、求助、技术问题、信息查询、建议请求、讨论等；自动静默闲聊、情绪宣泄、哈哈玩笑、简单打招呼等。
- ⚡ **无缝体验与 @ 穿透**：
  - 用户显式 `@机器人` 时默认绕过 TypeSafe 判断直接回复，不影响原本聊天体验。
  - 支持机器人名字与别名识别（如“小白你怎么看”）。
- 🎨 **丰富回复定制与中文可视化界面**：
  - WebUI 配置选项全面中文友好化，支持直观快速选择。
  - 支持自然、简洁、专业、友好、幽默、活泼、严谨、群友风格。
  - 支持极短、简短、正常、详细与自定义字数保护。
- ⏳ **模拟真人思考与打字延时**：
  - 支持可配置的 AI 回复延时（支持 1~5 秒区间随机延时或指定秒数固定延时），避免秒回显得过于机械突兀。
- 🛡️ **API 故障隔离保护**：
  - TypeSafe 超时、限流（429）或网络异常自动降级处理（默认静默），绝不影响 AstrBot 消息管道崩溃。
  - 内置每分钟滑动窗口请求限流与可选轻量内存缓存。

---

## 工作流程图

```text
             ┌──────────────┐
             │ 用户群聊消息 │
             └──────┬───────┘
                    ↓
             ┌───────────────┐
             │ 本地规则过滤器 │ (黑白名单/长度/关键词/指令过滤/防刷冷却)
             └──────┬────────┘
                    ↓
             ┌───────────────┐
             │   TypeSafe AI │ (Jev System One 结构化判断)
             │ 是否值得回复？ │
             └──────┬────────┘
                    ↓
               YES / NO
               ↓       ↓
          AstrBot LLM  静默
               ↓
            自然回复
```

---

## 安装说明

### 方式一：AstrBot WebUI 插件市场安装
在 AstrBot 控制面板中的“插件市场”搜索 `astrbot_plugin_typesafe_autoreply` 并点击安装。

### 方式二：手动安装
进入 AstrBot 的 `data/plugins/` 目录，克隆或解压本项目：
```bash
cd data/plugins/
git clone https://github.com/AstrBotDevs/astrbot_plugin_typesafe_autoreply.git
```
在 AstrBot 对应 Python 环境中安装依赖：
```bash
pip install -r astrbot_plugin_typesafe_autoreply/requirements.txt
```
重启 AstrBot 即可。

---

## 快速配置

在 AstrBot Web 控制台进入 **插件 -> TypeSafe 智能自动回复 -> 配置**：

1. **启用插件**：勾选开启。
2. **TypeSafe API Key**：填入在 [TypeSafe 控制台](https://console.typesafe.ai/) 获取的 API Key（如 `ts_...`）。
3. **允许自动回复类型**：勾选希望机器人参与的类型（推荐保持默认勾选明确提问、求助、技术问题等）。
4. **回复模型**：默认“跟随当前会话模型”，也可指定 Provider ID。
5. **回复风格与长度**：根据群聊氛围调整风格与最大回复字数。

---

## 插件指令

| 指令 | 权限 | 说明 |
|---|---|---|
| `/typesafe_status` | 所有人 | 查看当前插件运行状态、API 就绪情况与生效规则 |
| `/typesafe_test <消息内容>` | 所有人 | 在线测试 TypeSafe 对输入消息的意图分类判断（仅打印测试结果，不触发真实回复） |

---

## 建议与最佳实践

> [!TIP]
> 1. **关于 AstrBot 原生主动回复**：使用本插件时，建议在 AstrBot 会话设置中关闭原生随机主动回复，避免两套主动回复机制叠加触发。
> 2. **活跃群聊防刷屏**：对于发言非常频繁的群聊，建议适当调大“同一会话回复冷却”（如 60 秒）或降低“回复参与概率”（如 70%）。

---

## 开源协议

本项目采用 [MIT 协议](LICENSE) 开源。
