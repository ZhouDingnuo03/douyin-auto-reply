---
name: douyin-auto-reply
description: 抖音自动刷视频+评论回复机器人。支持自动滚动刷视频、智能识别评论内容、自动生成回复，支持模板话术和DeepSeek AI两种回复模式，内置完善风控机制。
---

# 抖音自动刷视频+评论回复机器人

## 功能概述
基于Playwright浏览器自动化实现的抖音运营工具，可实现自动刷抖音视频、智能回复网友评论，支持自定义回复策略和风控规则。

## 功能特性
- 🤖 自动刷视频：模拟真实用户行为自动滚动刷抖音视频
- 💬 智能评论回复：基于视频内容和评论内容自动生成回复
- 📝 多模式回复：支持固定话术库、DeepSeek AI智能生成两种回复模式
- ⚙️ 灵活配置：可设置回复频率、关键词过滤、跳过规则等
- 🛡️ 风控规避：内置随机等待、行为模拟、防检测机制
- 📊 数据统计：自动记录回复数据，支持导出统计报表

## 前置依赖
```bash
pip install playwright requests pyyaml
playwright install chromium
```

## 快速开始
1. 首次登录获取Cookie：`python scripts/get_cookie.py`
2. 复制配置文件：`cp config/config.yaml.example config/config.yaml`
3. 编辑配置：修改`config/config.yaml`设置回复策略、API密钥等
4. 启动机器人：`python scripts/douyin_auto_reply.py`

## 配置说明
详见`config/config.yaml.example`配置模板。
