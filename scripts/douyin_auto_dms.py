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
        self.start_time = time.time()
        self.replied_history = []  # 记录已经回复过的内容，避免重复回复

        # 连接已登录Chrome，和评论机器人共用配置
        self.cookie_file = Path(__file__).parent.parent / "data" / "cookies" / "douyin.json"
        # 本地文件保存最后一次回复
        self.last_reply_file = Path(__file__).parent.parent / "data" / "last_reply.txt"
        # 启动时加载历史回复
        if self.last_reply_file.exists():
            try:
                with open(self.last_reply_file, 'r', encoding='utf-8') as f:
                    last_reply = f.read().strip()
                    if last_reply:
                        self.replied_history.append(last_reply)
                        print(f"📝 已加载历史最后回复: {last_reply[:60]}{'...' if len(last_reply) > 60 else ''}", flush=True)
            except:
                pass

        # 用户指定坐标
        self.COORDINATES = {
            'dms_button': (1800, 15),
            'conversation': (1800, 159),
            'input_box': (1500, 700),
            'send_button': (1850, 710)
        }

        # 配置检查
        self.check_interval = self.config.get('dms_check_interval', 3)  # 检查新私信间隔（秒）
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
                await asyncio.sleep(10)  # 等待10秒让页面完整渲染

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

            # 合并后台监控任务：每1秒检查一次，清理页面+杀掉多余进程
            async def background_cleanup():
                import os
                import signal
                import requests
                print("🚀 后台监控已启动，每秒检查一次 [页面清理 + 进程清理]", flush=True)
                while True:
                    # === 1. 页面清理：只保留一个抖音页面 ===
                    closed_count = 0
                    total_pages = 0
                    douyin_pages = 0
                    try:
                        resp = requests.get('http://127.0.0.1:9222/json', timeout=5)
                        targets = resp.json()
                        # 找出所有抖音页面
                        douyin_targets = []
                        other_targets = []
                        for target in targets:
                            if target['type'] != 'page':
                                continue
                            total_pages += 1
                            if 'douyin.com' in target['url']:
                                douyin_pages += 1
                                douyin_targets.append(target)
                            else:
                                other_targets.append(target)
                        # 关闭所有非抖音
                        for target in other_targets:
                            requests.get(f'http://127.0.0.1:9222/json/close/{target["id"]}', timeout=2)
                            closed_count += 1
                        # 如果有多个抖音页面，只保留第一个，关闭其他
                        if len(douyin_targets) > 1:
                            for target in douyin_targets[1:]:
                                requests.get(f'http://127.0.0.1:9222/json/close/{target["id"]}', timeout=2)
                                closed_count += 1
                    except Exception as e:
                        pass
                    # === 2. 进程清理：只保留9222端口的主进程 ===
                    total_main = 0
                    kept = 0
                    killed_count = 0
                    try:
                        # Get all PIDs of chrome and chromium - use two separate pgrep to avoid regex issues
                        pids = []
                        with os.popen('pgrep -f chrome 2>/dev/null') as f:
                            output = f.read().strip()
                            if output:
                                pids.extend([int(p) for p in output.split()])
                        with os.popen('pgrep -f chromium 2>/dev/null') as f:
                            output = f.read().strip()
                            if output:
                                pids.extend([int(p) for p in output.split()])
                        # Remove duplicates
                        pids = list(set(pids))
                        # Check each process
                        for pid in pids:
                            if pid == os.getpid():
                                continue
                            # Read command line
                            try:
                                with open(f'/proc/{pid}/cmdline', 'rb') as f:
                                    cmd = f.read().decode(errors='ignore').replace('\x00', ' ')
                            except:
                                continue
                            # Only keep the main browser process that has remote-debugging-port=9222
                            if 'type=' not in cmd and ('chromium' in cmd or 'chrome' in cmd):
                                total_main += 1
                                if 'remote-debugging-port=9222' not in cmd:
                                    # Extra main browser process, kill immediately
                                    try:
                                        os.kill(pid, signal.SIGKILL)
                                        killed_count += 1
                                    except:
                                        pass
                                else:
                                    kept += 1
                    except Exception as e:
                        pass
                    # === 输出日志 ===
                    # 只有当有关闭操作才输出，减少噪音
                    if closed_count > 0 or killed_count > 0:
                        print(f"🧹 [后台] 页面: {total_pages} 页 → 关闭 {closed_count}，保留 {douyin_pages} 个抖音 | 进程: {total_main} 个 → 杀掉 {killed_count}，保留 {kept} 个", flush=True)
                    # 重新找抖音页面确保主流程有可用页面
                    found = False
                    for ctx in browser.contexts:
                        for pg in ctx.pages:
                            if 'douyin.com' in pg.url:
                                page = pg
                                found = True
                                break
                        if found:
                            break
                    await asyncio.sleep(1)

            cleanup_task = asyncio.create_task(background_cleanup())

            try:
                while True:
                    # 风控检查
                    if not self.risk_controller.can_operate():
                        sleep_time = random.randint(60, 300)
                        print(f"😴 风控限制，休眠{sleep_time}秒", flush=True)
                        await asyncio.sleep(sleep_time)
                        continue

                    # 每轮检查前重新导航到抖音主页
                    print(f"\n🔄 重新导航到抖音主页等待加载...", flush=True)
                    await page.goto('https://www.douyin.com/', timeout=60000, wait_until='domcontentloaded')
                    await asyncio.sleep(10)

                    await self._check_and_reply_dms(page)

                    print(f"\n⏳ 等待 {self.check_interval} 秒后检查下一批私信...", flush=True)
                    await asyncio.sleep(self.check_interval)

            except KeyboardInterrupt:
                print("\n🛑 机器人停止运行", flush=True)
                print(f"📊 本次运行共检查 {self.dms_processed} 条私信，回复 {self.dms_replied} 条", flush=True)
                await browser.close()

    async def _highlight_position(self, page: Page, x: int, y: int, name: str):
        """高亮显示点击位置（可视化）"""
        await page.evaluate(f"""
            () => {{
                // 移除旧高亮
                const oldHighlight = document.getElementById('auto-click-highlight');
                if (oldHighlight) oldHighlight.remove();

                // 添加新高亮
                const highlight = document.createElement('div');
                highlight.id = 'auto-click-highlight';
                highlight.style.position = 'fixed';
                highlight.style.left = ({x} - 20) + 'px';
                highlight.style.top = ({y} - 20) + 'px';
                highlight.style.width = '40px';
                highlight.style.height = '40px';
                highlight.style.border = '3px solid #ff0000';
                highlight.style.background = 'rgba(255, 0, 0, 0.3)';
                highlight.style.borderRadius = '50%';
                highlight.style.zIndex = '100000';
                highlight.style.pointerEvents = 'none';
                highlight.title = '{name} at ({x}, {y})';
                document.body.appendChild(highlight);
            }}
        """);
        await asyncio.sleep(0.5)

    async def _check_and_reply_dms(self, page: Page):
        """检查并回复新私信，使用用户指定坐标"""
        try:
            # 点击私信按钮打开私信列表 (1800, 15)
            print(f"\n📥 检查新私信...", flush=True)
            x, y = self.COORDINATES['dms_button']
            await self._highlight_position(page, x, y, "私信按钮");
            # 鼠标平滑移动到目标位置 - 纯真实鼠标点击，去掉JS点击
            await page.mouse.move(x, y, steps=random.randint(10, 20))
            await asyncio.sleep(random.uniform(0.2, 0.5))
            # 四次真实点击
            for i in range(4):
                await page.mouse.down()
                await asyncio.sleep(0.1)
                await page.mouse.up()
                await asyncio.sleep(0.2)
            await asyncio.sleep(2)  # 等待列表加载
            print("✅ 已四次真实点击私信按钮，私信列表打开", flush=True);

            # 点击第一个会话 (1800, 159) - 点击四次确保打开
            x, y = self.COORDINATES['conversation']
            await self._highlight_position(page, x, y, "会话");
            # 鼠标平滑移动到目标位置 - 纯真实鼠标点击
            await page.mouse.move(x, y, steps=random.randint(10, 20))
            await asyncio.sleep(random.uniform(0.2, 0.5))
            # 四次真实点击
            for i in range(4):
                await page.mouse.down()
                await asyncio.sleep(0.1)
                await page.mouse.up()
                await asyncio.sleep(0.2)

            await asyncio.sleep(2)  # 等待会话内容加载
            print("✅ 已四次点击会话，私信会话已打开", flush=True);

            # 获取完整聊天记录 - 直接提取componentsRightPanelnotHeaderArea全部文字
            chat_history = await page.evaluate("""
                () => {
                    // 直接找 componentsRightPanelnotHeaderArea 容器，提取全部文字
                    const container = document.querySelector('.componentsRightPanelnotHeaderArea');
                    if (!container) return null;

                    const fullText = container.textContent.trim();
                    if (!fullText) return null;

                    // 返回整个文本，AI会自己理解上下文
                    return [{
                        is_self: false,
                        text: fullText
                    }];
                }
            """);

            if not chat_history or len(chat_history) == 0:
                print("❌ 没找到任何消息，跳过", flush=True)
                return False

            # 获取完整聊天记录的全文
            full_chat_text = chat_history[0]['text']  # 因为我们直接把整个聊天容器放在一个msg里

            # 找最后一条对方消息（提取给AI）
            last_message = full_chat_text

            if not last_message:
                print("❌ 没找到对方新消息，跳过", flush=True)
                return False

            # 格式化聊天记录
            chat_context = "\n聊天历史记录：\n"
            for msg in chat_history:
                sender = "我" if msg['is_self'] else "对方"
                chat_context += f"{sender}: {msg['text']}\n"

            print(f"💬 共获取 {len(chat_history)} 条消息，完整聊天记录:", flush=True)
            for msg in chat_history:
                sender = "我" if msg['is_self'] else "对方"
                print(f"  [{sender}]\n{msg['text']}\n", flush=True)
            print(f"🔍 完整聊天全文: {last_message[:100]}{'...' if len(last_message) > 100 else ''}", flush=True)

            # 每轮重新加载本地保存的回复历史，然后检查是否已经回复过
            loaded_replied = []
            if self.last_reply_file.exists():
                try:
                    with open(self.last_reply_file, 'r', encoding='utf-8') as f:
                        last_reply = f.read().strip()
                        if last_reply:
                            loaded_replied.append(last_reply)
                            print(f"📖 从本地文件加载最后回复: {last_reply[:60]}{'...' if len(last_reply) > 60 else ''}", flush=True)
                except Exception as e:
                    print(f"⚠️  加载本地回复文件失败: {e}", flush=True)

            # 检查匹配：最新消息在顶部，提取开头 (回复长度 + 16) 字符，包含说明已经回复过
            found_match = False
            # 检查内存中历史 + 本地加载的
            all_replied = self.replied_history + loaded_replied
            for replied in all_replied:
                replied_stripped = replied.strip()
                if len(replied_stripped) > 0:
                    # 取开头 回复长度 + 16 字符检查
                    check_length = len(replied_stripped) + 16
                    start_part = full_chat_text.strip()[:check_length]
                    if replied_stripped in start_part:
                        print(f"✅ 匹配成功: 开头 {check_length} 字符包含已回复内容 '{replied_stripped}'，本轮跳过", flush=True)
                        found_match = True
                        break

            if found_match:
                return False

            # 整个容器内容就是对方群聊消息，直接给AI
            last_message = full_chat_text

            # 风控：违禁词检查最后一条消息
            if self.risk_controller._check_forbidden_keywords(last_message):
                print("⚠️  消息包含违禁关键词，跳过回复", flush=True)
                return False

            # AI生成回复，复用现有逻辑，将聊天记录作为视频标题传入提供上下文
            reply_text = generate_reply(last_message, chat_context, self.config)
            print(f"🤖 AI生成回复: {reply_text}", flush=True)

            # 点击输入框 (1500, 700)
            x, y = self.COORDINATES['input_box'];
            await self._highlight_position(page, x, y, "输入框");
            # 鼠标平滑移动 + 真实鼠标点击
            await page.mouse.move(x, y, steps=random.randint(10, 20))
            await asyncio.sleep(random.uniform(0.2, 0.5))
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
            # 真实鼠标按下+抬起，确保点击聚焦
            await page.mouse.down()
            await asyncio.sleep(0.1)
            await page.mouse.up();

            if not found:
                print("⚠️  找不到输入框", flush=True)
                return False

            await asyncio.sleep(2)
            print("✅ 已点击输入框", flush=True);

            # 输入回复内容：先输入"测试"再删除，确保彻底清空，再逐个字符输入
            await page.keyboard.type('测试');
            await asyncio.sleep(0.2);
            # 按 10 次 backspace 确保彻底清空
            for _ in range(10):
                await page.keyboard.press('Backspace');
                await asyncio.sleep(0.05);
            # 逐个字符输入，随机间隔模拟人类
            for char in reply_text:
                await page.keyboard.type(char);
                await asyncio.sleep(random.uniform(0.05, 0.15));

            await asyncio.sleep(10)
            print(f"✅ 回复已输入，等待10秒后发送", flush=True);

            # 点击发送按钮 (1850, 710) - 直接模拟鼠标点击
            x, y = self.COORDINATES['send_button'];
            await self._highlight_position(page, x, y, "发送按钮");
            # 鼠标平滑移动到位置
            await page.mouse.move(x, y, steps=random.randint(10, 20))
            await asyncio.sleep(random.uniform(0.2, 0.5))
            # 直接模拟鼠标点击，不使用JS点击避免类型错误
            await page.mouse.down()
            await asyncio.sleep(0.1)
            await page.mouse.up()
            sent = True;

            if sent:
                self.dms_replied += 1
                self.risk_controller.record_reply(None, last_message, reply_text)
                self.replied_history.append(reply_text)
                # 保存最后一次回复到本地文件
                try:
                    self.last_reply_file.parent.mkdir(parents=True, exist_ok=True)
                    with open(self.last_reply_file, 'w', encoding='utf-8') as f:
                        f.write(reply_text)
                    print(f"💾 已保存最后回复到本地文件", flush=True)
                except Exception as e:
                    print(f"⚠️  保存回复到本地文件失败: {e}", flush=True)
                print(f"✅ 成功发送回复!", flush=True)
            else:
                print("⚠️  点击发送按钮失败，你可以手动点击发送", flush=True)
                # 就算发送失败也算处理了
                pass

            self.dms_processed += 1
            # 发送完成后等待5秒刷新网页
            await asyncio.sleep(5)
            await page.reload(wait_until='domcontentloaded', timeout=60000)
            await asyncio.sleep(3)
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
