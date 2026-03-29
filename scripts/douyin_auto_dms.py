#!/usr/bin/env python3
"""
抖音自动回复私信机器人
基于Playwright，自动检查新私信并AI回复
GitHub: https://github.com/ZhouDingnuo03/douyin-auto-reply
"""
import asyncio
import random
import time
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright, Page

from utils.config_loader import load_config
from utils.reply_generator import generate_reply
from utils.risk_control import RiskController


class DouyinAutoDmsBot:
    def __init__(self, config_path: str = "../config/config.yaml", debug: bool = True):
        self.config = load_config(config_path)
        self.debug = debug
        self.risk_controller = RiskController(self.config)
        self.dms_processed = 0
        self.dms_replied = 0
        self.start_time = time.time()

        # 连接已登录Chrome，和评论机器人共用配置
        self.cookie_file = Path(__file__).parent.parent / "data" / "cookies" / "douyin.json"

        # 配置检查
        self.check_interval = self.config.get('dms_check_interval', 60)  # 检查新私信间隔（秒）
        self.max_replies_per_hour = self.config.get('dms_max_replies_per_hour', 20)
        print(f"🚀 抖音自动回复私信机器人启动", flush=True)
        print(f"📝 配置：检查间隔={self.check_interval}秒，每小时最大回复={self.max_replies_per_hour}", flush=True)
        print("=" * 60, flush=True)

    async def run(self):
        """启动机器人循环检查私信"""
        async with async_playwright() as p:
            # 连接到已打开的Chrome实例（已登录）
            print("[调试] 连接谷歌浏览器 CDP: http://127.0.0.1:9222...", flush=True)
            browser = await asyncio.wait_for(p.chromium.connect_over_cdp('http://127.0.0.1:9222'), timeout=120)
            print("[调试] ✅ CDP连接成功（使用已登录谷歌浏览器实例）", flush=True)

            if len(browser.contexts) > 0:
                context = browser.contexts[0]
            else:
                context = await browser.new_context()
            page = await context.new_page()

            # 拦截弹窗
            await page.add_init_script("""
                document.addEventListener('click', function(e) {
                    const target = e.target.closest('a');
                    if (target && target.href) {
                        if (target.href.startsWith('douyin://') || target.href.includes('external')) {
                            console.log('Blocked external link opening:', target.href);
                            e.preventDefault();
                            e.stopPropagation();
                        }
                    }
                }, true);
            """);

            try {
                while True:
                    # 风控检查
                    if not self.risk_controller.can_operate():
                        sleep_time = random.randint(60, 300)
                        print(f"😴 风控限制，休眠{sleep_time}秒", flush=True)
                        await asyncio.sleep(sleep_time)
                        continue

                    await self._check_and_reply_dms(page)

                    print(f"\n⏳ 等待 {self.check_interval} 秒后检查下一批私信...", flush=True)
                    await asyncio.sleep(self.check_interval)

            } except KeyboardInterrupt:
                print("\n🛑 机器人停止运行")
                print(f"📊 本次运行共检查 {self.dms_processed} 条私信，回复 {self.dms_replied} 条", flush=True)
                await browser.close()

    async def _check_and_reply_dms(self, page: Page):
        """检查并回复新私信"""
        try:
            # 导航到私信页面
            print(f"\n📥 检查新私信...", flush=True)
            await page.goto('https://www.douyin.com/message', timeout=180000, wait_until='domcontentloaded')
            await asyncio.sleep(8)  # 等待渲染

            # 获取私信会话列表
            conversations = await self._get_conversations(page)
            if not conversations:
                print("📭 没有找到私信会话", flush=True)
                return

            print(f"📭 找到 {len(conversations)} 个私信会话", flush=True)

            # 遍历会话，找未回复的
            for conv in conversations:
                if not self.risk_controller.can_reply():
                    print(f"⚠️  达到回复频率限制，跳过剩余会话", flush=True)
                    break

                if conv['is_unread'] or conv['has_new']:
                    await self._process_conversation(page, conv)
                    self.dms_processed += 1
                    await asyncio.sleep(random.uniform(2, 5))

        except Exception as e:
            print(f"❌ 检查私信出错: {str(e)[:100]}", flush=True)

    async def _get_conversations(self, page: Page):
        """获取私信会话列表"""
        conversations = await page.evaluate("""
            () => {
                const result = [];
                // 抖音私信会话列表选择器
                const itemSelectors = [
                    '[data-e2e="message-item"]',
                    '.conversation-item',
                    '.message-item',
                    '.im-item'
                ];

                for (const selector of itemSelectors) {
                    const items = document.querySelectorAll(selector);
                    if (items.length > 0) {
                        items.forEach((item, index) => {
                            // 获取未读状态
                            const isUnread = item.querySelector('.unread-dot, [data-unread="true"], .unread') !== null;
                            // 获取对方昵称
                            let nick = '';
                            const nickEl = item.querySelector('.nickname, .name, .title');
                            if (nickEl) nick = nickEl.textContent.trim();
                            // 获取会话ID
                            let convId = item.getAttribute('data-conv-id') || item.dataset.convId || index;

                            result.push({
                                conv_id: String(convId),
                                nick: nick,
                                is_unread: isUnread || item.classList.contains('unread')
                            });
                        });
                        break;
                    }
                }
                return result;
            }
        """);

        # 转成统一格式，标记未读
        result = []
        for conv in conversations:
            # 我们只处理未读
            if conv['is_unread']:
                result.append(conv)

        return result

    async def _process_conversation(self, page: Page, conv):
        """点击进入会话，获取最后一条消息，回复它"""
        print(f"\n✉️ 处理来自 {conv['nick']} 的未读私信", flush=True)

        try {
            # 点击进入这个会话
            clicked = await page.evaluate("""
                (convId) => {
                    const items = document.querySelectorAll('[data-e2e="message-item"], .conversation-item');
                    for (let item of items) {
                        const id = item.getAttribute('data-conv-id') || item.dataset.convId;
                        if (id === convId) {
                            item.scrollIntoView({block: 'center'});
                            setTimeout(() => item.click(), 200);
                            return true;
                        }
                    }
                    return false;
                }
            """, conv['conv_id']);

            if not clicked:
                print(f"⚠️  无法点击会话 {conv['conv_id']}", flush=True)
                return False

            await asyncio.sleep(5)  # 等待会话内容加载

            # 获取最后一条用户消息
            last_message = await page.evaluate("""
                () => {
                    // 找到消息列表
                    const container = document.querySelector('.message-container, .messages-container, .im-message-container');
                    if (!container) return null;

                    const messages = container.querySelectorAll('.message-item');
                    if (messages.length === 0) return null;

                    // 找最后一条不是自己发的消息
                    for (let i = messages.length - 1; i >= 0; i--) {
                        const msg = messages[i];
                        // 如果不是我发的，就是对方发的
                        if (!msg.classList.contains('self') && !msg.classList.contains('is-me')) {
                            let text = '';
                            const textEl = msg.querySelector('.content, .text-content, .message-text');
                            if (textEl) text = textEl.textContent.trim();
                            return text && text.length >= 2 ? text : null;
                        }
                    }
                    return null;
                }
            """);

            if not last_message:
                print("⚠️  找不到对方消息，跳过", flush=True)
                return False

            print(f"💬 对方消息: {last_message[:60]}{'...' if len(last_message) > 60 else ''}", flush=True)

            # 风控：检查违禁词
            if self.risk_controller._check_forbidden_keywords(last_message):
                print("⚠️  消息包含违禁关键词，跳过回复", flush=True)
                return False

            # 用AI生成回复，复用现有逻辑，传入视频标题为空（因为私信）
            reply_text = generate_reply(last_message, "", self.config)
            print(f"🤖 AI生成回复: {reply_text}", flush=True)

            # 找到输入框发送回复
            sent = await self._send_reply(page, reply_text)
            if sent:
                self.dms_replied += 1
                self.risk_controller.record_reply(None, last_message, reply_text)
                print(f"✅ 成功发送回复给 {conv['nick']}", flush=True)

            await asyncio.sleep(random.uniform(3, 6))
            return sent

        } catch Exception e:
            print(f"❌ 处理会话 {conv['conv_id']} 出错: {str(e)[:100]}", flush=True)
            return False

    async def _send_reply(self, page: Page, reply_text: str):
        """发送回复"""
        try {
            # 查找输入框
            input_found = await page.evaluate("""
                () => {
                    const selectors = [
                        'textarea[placeholder^="说点什么"]',
                        '.message-input textarea',
                        '.reply-input textarea',
                        '[contenteditable="true"][placeholder*="说点什么"]',
                        '.public-DraftEditor-content[contenteditable="true"]'
                    ];

                    for (const selector of selectors) {
                        const el = document.querySelector(selector);
                        if (el && getComputedStyle(el).display !== 'none') {
                            el.scrollIntoView({block: 'center'});
                            return {found: true, selector: selector};
                        }
                    }
                    return {found: false};
                }
            """);

            if (!input_found['found']):
                print("⚠️  找不到私信输入框", flush=True)
                return False

            await asyncio.sleep(1)

            # 输入内容
            input_box = await page.query_selector(input_found['selector'])
            if not input_box:
                print("⚠️  查询输入框失败", flush=True)
                return False

            await input_box.click()
            await asyncio.sleep(0.5)
            await page.keyboard.press('Control+A')
            await asyncio.sleep(0.2)
            await page.keyboard.press('Backspace')
            await asyncio.sleep(0.2)
            await input_box.type(reply_text, delay=random.randint(60, 150))
            await asyncio.sleep(random.uniform(0.5, 1))

            # 抖音发送按钮一般在右下角，点击发送
            sent = await page.evaluate("""
                () => {
                    // 尝试各种发送按钮位置
                    const btnSelectors = [
                        '.send-btn',
                        '.message-send-btn',
                        'button:has-text("发送")',
                        '[data-e2e="send-btn"]',
                        // 右下角找
                        document.querySelector('.message-container button:last-child'),
                        document.querySelector('.input-container button:last-child')
                    ];

                    for (const selector of btnSelectors) {
                        let btn;
                        if (typeof selector === 'string') {
                            btn = document.querySelector(selector);
                        } else {
                            btn = selector;
                        }
                        if (btn && getComputedStyle(btn).display !== 'none') {
                            btn.scrollIntoView({block: 'center'});
                            setTimeout(() => btn.click(), 150);
                            return true;
                        }
                    }
                    return false;
                }
            """);

            if not sent:
                print("⚠️  自动点击发送按钮失败，请手动发送", flush=True)
                await asyncio.sleep(5)
                # 假设用户手动发送了
                return True

            await asyncio.sleep(2)
            return True

        } except Exception as e:
            print(f"❌ 发送回复出错: {str(e)[:100]}", flush=True)
            return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="抖音自动回复私信机器人")
    parser.add_argument("--config", default="../config/config.yaml", help="配置文件路径")
    parser.add_argument("--no-debug", dest="debug", action="store_false", help="关闭调试模式")
    args = parser.parse_args()

    try {
        bot = DouyinAutoDmsBot(args.config, args.debug)
        asyncio.run(bot.run())
    } except Exception as e:
        print(f"❌ 启动失败: {e}")
        exit(1)


if __name__ == "__main__":
    main()
