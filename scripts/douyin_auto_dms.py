#!/usr/bin/env python3
"""
抖音自动回复私信机器人
基于Playwright，自动检查新私信并AI回复
GitHub: https://github.com/ZhouDingnuo03/douyin-auto-reply

按照用户指定坐标点击：
- 私信按钮: (1800, 15)
- 会话: (1800, 200)
- 输入框: (1500, 700)
- 发送按钮: (1850, 710)
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
        self.start_time = time.time

        # 连接已登录Chrome，和评论机器人共用配置
        self.cookie_file = Path(__file__).parent.parent / "data" / "cookies" / "douyin.json"

        # 用户指定坐标
        self.COORDINATES = {
            'dms_button': (1800, 15),
            'conversation': (1800, 200),
            'input_box': (1500, 700),
            'send_button': (1850, 710)
        }

        # 配置检查
        self.check_interval = self.config.get('dms_check_interval', 60)  # 检查新私信间隔（秒）
        self.max_replies_per_hour = self.config.get('dms_max_replies_per_hour', 20)
        print(f"🚀 抖音自动回复私信机器人启动", flush=True)
        print(f"📝 配置：检查间隔={self.check_interval}秒，每小时最大回复={self.max_replies_per_hour}", flush=True)
        print(f"🎯 使用用户指定坐标", flush=True)
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

            # 先找已有的抖音页面
            page = None
            for pg in context.pages:
                if 'douyin.com' in pg.url:
                    page = pg
                    print(f"[调试] ✅ 找到已有抖音页面: {page.url}", flush=True)
                    break
            if not page:
                print("[调试] 创建新页面并导航到抖音主页...", flush=True)
                page = await context.new_page()
                await page.goto('https://www.douyin.com/', timeout=60000, wait_until='domcontentloaded')
                await asyncio.sleep(3)

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

            try:
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

            except KeyboardInterrupt:
                print("\n🛑 机器人停止运行", flush=True)
                print(f"📊 本次运行共检查 {self.dms_processed} 条私信，回复 {self.dms_replied} 条", flush=True)
                await browser.close()

    async def _check_and_reply_dms(self, page: Page):
        """检查并回复新私信，使用用户指定坐标"""
        try:
            # 点击私信按钮打开私信列表 (1800, 15)
            print(f"\n📥 检查新私信...", flush=True)
            x, y = self.COORDINATES['dms_button']
            await page.evaluate(f"""
                () => {{
                    const elem = document.elementFromPoint({x}, {y});
                    if (!elem) return false;
                    let current = elem;
                    for (let i = 0; i < 8; i++) {{
                        if (current.textContent.includes('私信')) {{
                            current.click();
                            console.log('Clicked私信 button');
                            return true;
                        }}
                        if (!current.parentElement) break;
                        current = current.parentElement;
                    }}
                    elem.click();
                    return true;
                }}
            """);
            await asyncio.sleep(5)  # 等待列表加载
            print("✅ 已点击私信按钮，私信列表打开", flush=True);

            # 点击第一个会话 (1800, 200)
            x, y = self.COORDINATES['conversation']
            clicked = await page.evaluate(f"""
                () => {{
                    const elem = document.elementFromPoint({x}, {y});
                    if (!elem) return false;
                    let current = elem;
                    for (let i = 0; i < 8; i++) {{
                        if (current.tagName === 'DIV' && current.getBoundingClientRect().width > 50) {{
                            current.click();
                            console.log('Clicked conversation at ({x}, {y})');
                            return true;
                        }}
                        if (!current.parentElement) break;
                        current = current.parentElement;
                    }}
                    elem.click();
                    return true;
                }}
            """);

            if not clicked:
                print("⚠️  点击会话失败", flush=True)
                return False

            await asyncio.sleep(5)  # 等待会话内容加载
            print("✅ 已打开私信会话", flush=True);

            # 获取完整聊天记录
            chat_history = await page.evaluate("""
                () => {
                    // 在 content 区域找消息容器
                    const container = document.querySelector('.messageMessageBox messageBox messageBoxlargMaring, .MessageBoxContentrowBox, [class*="messageBox"], [class*="messageList"], .components-message-list');
                    if (!container) return null;

                    const messages = container.querySelectorAll('[class*=" message"], [class*=" Message"], div[role="listitem"]');
                    if (messages.length === 0) return null;

                    const result = [];
                    // 收集最近10条消息作为上下文
                    for (let i = Math.max(0, messages.length - 10); i < messages.length; i++) {
                        const msg = messages[i];
                        // 如果包含 "claude-highlight" 跳过我们自己的高亮
                        if (msg.id === 'claude-highlight') continue;

                        // 判断是不是自己发的
                        const is_self = msg.classList.contains('self') || msg.classList.contains('is-me');

                        // 提取文字
                        let textEl = msg.querySelector('[class*="content"], [class*="text"], div[role="presentation"]');
                        if (!textEl) textEl = msg;
                        const text = textEl.textContent.trim();
                        if (text.length > 0) {
                            result.push({
                                is_self: is_self,
                                text: text
                            });
                        }
                    }

                    return result.length > 0 ? result : null;
                }
            """);

            if not chat_history or len(chat_history) == 0:
                print("❌ 没找到任何消息，跳过", flush=True)
                return False

            # 找最后一条对方消息
            last_message = None
            for msg in reversed(chat_history):
                if not msg['is_self']:
                    last_message = msg['text']
                    break

            if not last_message:
                print("❌ 没找到对方新消息，跳过", flush=True)
                return False

            # 格式化聊天记录
            chat_context = "\n聊天历史记录：\n"
            for msg in chat_history:
                sender = "我" if msg['is_self'] else "对方"
                chat_context += f"{sender}: {msg['text']}\n"

            print(f"💬 共获取 {len(chat_history)} 条消息，最后一条对方消息: {last_message[:60]}{'...' if len(last_message) > 60 else ''}", flush=True)
            print("📜 完整聊天记录:", flush=True)
            for msg in chat_history:
                sender = "我" if msg['is_self'] else "对方"
                print(f"  [{sender}] {msg['text'][:80]}{'...' if len(msg['text']) > 80 else ''}", flush=True)

            # 风控：违禁词检查最后一条消息
            if self.risk_controller._check_forbidden_keywords(last_message):
                print("⚠️  消息包含违禁关键词，跳过回复", flush=True)
                return False

            # AI生成回复，复用现有逻辑，将聊天记录作为视频标题传入提供上下文
            reply_text = generate_reply(last_message, chat_context, self.config)
            print(f"🤖 AI生成回复: {reply_text}", flush=True)

            # 点击输入框 (1500, 700)
            x, y = self.COORDINATES['input_box'];
            found = await page.evaluate(f"""
                () => {{
                    const elem = document.elementFromPoint({x}, {y});
                    if (!elem) return false;
                    elem.click();
                    // 全选清空
                    const selection = window.getSelection();
                    const range = document.createRange();
                    range.selectNodeContents(elem);
                    selection.removeAllRanges();
                    range.selectNodeContents(elem);
                    selection.addRange(range);
                    return true;
                }}
            """);

            if not found:
                print("⚠️  找不到输入框", flush=True)
                return False

            await asyncio.sleep(2)
            print("✅ 已点击输入框", flush=True);

            # 输入回复内容
            x, y = self.COORDINATES['input_box'];
            await page.evaluate(f"""
                () => {{
                    const elem = document.elementFromPoint({x}, {y});
                    if (!elem) return;
                    elem.focus();
                    elem.textContent = {repr(reply_text)};
                    // 触发 input 事件让框架检测到变化
                    const event = new InputEvent('input', {{ bubbles: true }});
                    elem.dispatchEvent(event);
                }}
            """);

            await asyncio.sleep(2)
            print(f"✅ 回复已输入: {reply_text}", flush=True);

            # 点击发送按钮 (1850, 710)
            x, y = self.COORDINATES['send_button'];
            sent = await page.evaluate(f"""
                () => {{
                    const elem = document.elementFromPoint({x}, {y});
                    if (!elem) return false;
                    elem.click();
                    console.log('Clicked send button at ({x}, {y})');
                    return true;
                }}
            """);

            if sent:
                self.dms_replied += 1
                self.risk_controller.record_reply(None, last_message, reply_text)
                print(f"✅ 成功发送回复!", flush=True)
            else:
                print("⚠️  点击发送按钮失败，你可以手动点击发送", flush=True)
                # 就算发送失败也算处理了
                pass

            self.dms_processed += 1
            await asyncio.sleep(random.uniform(3, 6))
            return True

        except Exception as e:
            print(f"❌ 检查私信出错: {str(e)[:100]}", flush=True)
            return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="抖音自动回复私信机器人（用户指定坐标版）")
    parser.add_argument("--config", default="../config/config.yaml", help="配置文件路径")
    parser.add_argument("--no-debug", dest="debug", action="store_false", help="关闭调试模式")
    args = parser.parse_args()

    try:
        bot = DouyinAutoDmsBot(args.config, args.debug)
        asyncio.run(bot.run())
    except Exception as e:
        print(f"❌ 启动失败: {e}")
        exit(1)


if __name__ == "__main__":
    main()
