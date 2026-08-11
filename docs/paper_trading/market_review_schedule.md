# 大盘复盘任务调度指南

## 概述

本文件详细说明股票智能分析系统中大盘（Market Review）任务的触发机制、调度配置和监控方式。

---

## 一、触发方式

大盘复盘支持三种触发途径：

### 1. GitHub Actions（推荐用于云部署）

在 `.github/workflows/` 中的每日任务工作流（如 `daily-stock-analysis.yml`），默认在工作日 **北京时间 18:00** 自动触发大盘复盘。

```yaml
# 示例：触发大盘复盘的 workflow step
- name: Run Market Review
  run: python main.py --market-review --schedule
  env:
    MARKET_REVIEW_REGION: cn
    SEND_NOTIFICATION: true
```

可自定义 cron 表达式修改触发时间：

```yaml
on:
  schedule:
    - cron: '0 18 * * 1-5'  # 工作日 18:00
```

### 2. Docker Cron 容器内定时

在 Docker 容器中启用系统 cron 服务，配置定时任务调用分析脚本：

```bash
# crontab -e
0 18 * * 1-5 /usr/local/bin/python3 /app/main.py --market-review --send-notification
```

容器内需确保时区正确设置：

```dockerfile
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
```

### 3. WebUI 手动触发

登录 Web工作台 → 点击「大盘复盘」按钮，立即触发一次大盘分析。此方式适用于：
- 非交易日临时查看市场回顾
- 测试调试分析逻辑
- 紧急情况下补充复盘报告

WebAPI 端点：`POST /api/v1/analysis/market-review`

---

## 二、关键环境变量配置

| 变量名 | 说明 | 默认值 | 是否必需 |
|--------|------|--------|----------|
| `MARKET_REVIEW_REGION` | 复盘区域：`cn` (A股) / `us` (美股) / `hk` (港股) | `cn` | 否 |
| `MARKET_REVIEW_SEND_NOTIFICATION` | 复盘完成后是否发送通知 | `true` | 否 |
| `MARKET_REVIEW_TRIGGER_SOURCE` | 触发来源标识（用于日志追踪） | `cron` 或 `manual` | 否 |
| `TRADING_DAYS_ONLY` | 仅在交易日执行复盘 | `true` | 否 |

在 `.env` 文件中设置：

```env
MARKET_REVIEW_REGION=us
MARKET_REVIEW_SEND_NOTIFICATION=true
TRADING_DAYS_ONLY=true
```

---

## 三、交易日判断逻辑

系统通过内置日历判断今日是否为交易日（需考虑 A/H/US 各自节假日），仅当对应市场为交易日时才执行复盘。节假日包括：
- 中国大陆法定节假日
- 香港公众假期
- 美国联邦假日

可通过调用 `src/data/trading_calendar.py` 中的辅助函数进行判断。

---

## 四、日志与监控

大盘复盘启动时记录以下信息：

```log
[MarketReview] component=market_review action=start region=cn source=cron trace_id=abc-123
```

任务完成后输出总结摘要：

```log
[MarketReview] component=market_review action=complete region=cn analyzed_indices=3 duration=45s trace_id=abc-123
```

可通过尾部日志或集成监控系统（如 Prometheus + Grafana）观察复盘任务的执行频率和成功率。

---

## 五、故障处理

| 问题现象 | 可能原因 | 排查步骤 |
|----------|----------|----------|
| 复盘未按时触发 | 时区配置错误 | 检查服务器/Docker 时区设置 |
| 复盘当天跳过 | 当日为非交易日 | 查询 trading calendar 确认 |
| 推送失败 | 通知渠道配置缺失 | 检查 WECHAT_WEBHOOK_URL / TELEGRAM_BOT_TOKEN 等 |
| 报 LLM API 错误 | 模型服务不可用 | 检查 ANSPIRE_API_KEYS / GEMINI_API_KEY 等 |

如需强制在非交易日运行，可设置 `TRADING_DAYS_ONLY=false`（不推荐常规使用）。

---

## 六、扩展计划

未来计划支持的调度方式：
- Kubernetes CronJob 编排
- 企业微信/飞书定时机器人触发
- 可视化任务配置面板（WebUI Settings 页面）

