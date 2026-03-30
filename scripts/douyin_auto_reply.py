#!/usr/bin/env python3
"""
抖音自动刷视频+评论回复机器人
GitHub: https://github.com/ZhouDingnuo03/douyin-auto-reply
"""
import asyncio
import random
import time
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright, Page
from utils.config_loader import load_config
from utils.comment_analyzer import analyze_comment
from utils.reply_generator import generate_reply
from utils.risk_control import RiskController

class DouyinAutoReplyBot:
    def __init__(self, config_path: str = "../config/config.yaml", debug: bool = True):
        self.config = load_config(config_path)
        self.debug = debug
        self.risk_controller = RiskController(self.config)
        self.reply_count = 0
        self.start_time = time.time
        # 保存所有视频列表，包含ID和标题，按顺序处理
        self.videos = []  # 每个元素 {id: string, title: string}
        self.current_video_index = 0

        # 由于连接到已有的Chrome浏览器实例，浏览器已经保存了登录Cookie
        # 不需要从文件加载Cookie，浏览器本身已经保持登录状态
        self.cookie_file = Path(__file__).parent.parent / "data" / "cookies" / "douyin.json"

    async def run(self):
        """启动机器人"""
        print("🚀 抖音自动回复机器人启动", flush=True)
        print(f"📝 配置：回复模式={self.config['reply_mode']}, 每小时最大回复={self.config['max_replies_per_hour']}", flush=True)
        print("=" * 60, flush=True)

        async with async_playwright() as p:
            # 强制连接到自带的谷歌浏览器CDP实例（已登录，真实浏览器，更好绕过反爬）
            print("[调试] 连接自带谷歌浏览器 CDP: http://127.0.0.1:9222...", flush=True)
            browser = await asyncio.wait_for(p.chromium.connect_over_cdp('http://127.0.0.1:9222'), timeout=120)
            print("[调试] ✅ CDP连接成功（使用自带谷歌浏览器实例）", flush=True)
            # 获取第一个上下文（自带浏览器已经有上下文）
            if len(browser.contexts) > 0:
                context = browser.contexts[0]
            else:
                context = await browser.new_context()
            # 创建新页面
            page = await context.new_page()
            print("[调试] 创建新页面成功，浏览器已自带登录Cookie", flush=True)

            # 拦截打开外部链接弹窗，防止触发xdg-open
            await page.add_init_script("""
                // 阻止打开外部App链接的弹窗
                document.addEventListener('click', function(e) {
                    const target = e.target.closest('a');
                    if (target && target.href) {
                        // 如果是douyin:// scheme或者外部链接，阻止默认行为
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
                # 直接进入指定搜索页面，关键词后面加随机三位数避免重复
                random_3digits = random.randint(100, 999)
                self.search_keyword = f"三角洲行动{random_3digits}"
                encoded_keyword = self.search_keyword.replace(' ', '%20')
                target_url = f'https://www.douyin.com/search/{encoded_keyword}?type=video'
                page_name = f'搜索结果页({self.search_keyword} 视频)'

                print(f"[调试] 搜索关键词: {self.search_keyword}", flush=True)
                print(f"[调试] 正在加载: {target_url}", flush=True)
                await page.goto(target_url, timeout=180000, wait_until='domcontentloaded')
                print("[调试] domcontentloaded完成，等待JS渲染...", flush=True)
                await asyncio.sleep(10)  # 等待JavaScript渲染完成
                print(f"[调试] 页面加载完成，当前URL: {page.url}", flush=True)
                print(f"[调试] 页面标题: {await page.title()}", flush=True)

                # 一次性提取页面上所有视频（ID+标题），按顺序逐个处理（不用滚动，不重复）
                print("[调试] 提取页面上所有视频（ID+标题）...", flush=True)
                self.videos = await page.evaluate("""
                    () => {
                        const result = [];
                        const seen = new Set();
                        const banList = ['我的喜欢', '我的收藏', '观看历史', '稍后再看', '精选', '推荐', '搜索', '关注', '朋友', '直播'];
                        // 收集所有视频卡片
                        const containers = [
                            '[data-e2e="search-card-container"]',
                            '.search-video-card',
                            '.video-card',
                            '.card-container',
                            '.result-item',
                            '[data-e2e="feed-item"]'
                        ];
                        // 先按整个卡片提取
                        for (const containerSelector of containers) {
                            const cards = document.querySelectorAll(containerSelector);
                            for (let card of cards) {
                                // 找视频链接提取ID
                                let videoId = null;
                                const links = card.querySelectorAll('a[href*="/video/"]');
                                for (let link of links) {
                                    const href = link.getAttribute('href');
                                    if (href) {
                                        const match = href.match(/\/video\/([0-9]{10,})/);
                                        if (match && match[1].length > 5 && !seen.has(match[1])) {
                                            videoId = match[1];
                                            seen.add(match[1]);
                                            break;
                                        }
                                    }
                                }
                                if (!videoId) continue;

                                // 获取视频标题
                                let title = '';
                                const titleSelectors = [
                                    '[data-e2e="video-title"]',
                                    '.title',
                                    '.video-title',
                                    '.search-card-title',
                                    '.desc',
                                    'div[title]'
                                ];
                                for (const selector of titleSelectors) {
                                    const titleEl = card.querySelector(selector);
                                    if (titleEl) {
                                        const text = titleEl.textContent.trim();
                                        if (text.length > 3 && !banList.some(ban => text.includes(ban))) {
                                            title = text;
                                            break;
                                        }
                                    }
                                }

                                // 检查卡片可见
                                const rect = card.getBoundingClientRect();
                                if (rect.width > 100 && rect.height > 100) {
                                    result.push({id: videoId, title: title});
                                }
                            }
                        }

                        // 如果没找到，回退到只提取ID
                        if (result.length === 0) {
                            const selectors = [
                                'a[href*="/video/"]'
                            ];
                            for (const selector of selectors) {
                                const links = document.querySelectorAll(selector);
                                for (let link of links) {
                                    const href = link.getAttribute('href');
                                    if (href) {
                                        const match = href.match(/\/video\/([0-9]{10,})/);
                                        if (match && match[1].length > 5 && !seen.has(match[1])) {
                                            const rect = link.getBoundingClientRect();
                                            if (rect.width > 100 && rect.height > 100) {
                                                result.push({id: match[1], title: ''});
                                                seen.add(match[1]);
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        return result;
                    }
                """);
                print(f"✅ 提取完成，共找到 {len(self.videos)} 个视频，开始按顺序处理", flush=True)
                print("=" * 60, flush=True)

                # 按顺序逐个处理每个视频
                while self.current_video_index < len(self.videos):
                    video = self.videos[self.current_video_index];
                    video_id = video['id'];
                    video_title_from_search = video['title'];
                    print(f"\n📼 处理第 {self.current_video_index + 1}/{len(self.videos)} 个视频，ID: {video_id}", flush=True)
                    if video_title_from_search:
                        print(f"📹 标题: {video_title_from_search[:60]}{'...' if len(video_title_from_search) > 60 else ''}", flush=True)

                    # 风控检查
                    if not self.risk_controller.can_operate():
                        sleep_time = random.randint(60, 300)
                        print(f"😴 风控限制，休眠{sleep_time}秒", flush=True)
                        await asyncio.sleep(sleep_time)
                        continue

                    # 直接导航到视频详情页
                    video_detail_url = f"https://www.douyin.com/video/{video_id}"
                    await page.goto(video_detail_url, wait_until='domcontentloaded', timeout=180000)
                    await asyncio.sleep(8)

                    # 处理当前视频
                    processed = await self._process_current_video(page, video_title_from_search)

                    if not processed:
                        print(f"⚠️  视频 {video_id} 处理失败，跳过", flush=True)

                    # 移动到下一个视频
                    self.current_video_index += 1

                    # 不需要返回搜索页，直接导航下一个视频（更流畅，避免标题缓存问题）
                    # 随机等待后进入下一个
                    wait_time = random.uniform(*self.config['scroll_interval'])
                    await asyncio.sleep(wait_time + 2)

            except KeyboardInterrupt:
                print("\n🛑 机器人停止运行")
                print(f"📊 本次运行共回复了{self.reply_count}条评论")
                await browser.close()

    async def _process_current_video(self, page: Page, video_title_from_search: str) -> bool:
        """处理当前视频，返回是否成功处理"""
        try:
            print(f"\n🎬 正在播放当前视频，停留{self.config['scroll_interval'][0]}~{self.config['scroll_interval'][1]}秒...", flush=True)

            # 等待视频加载
            await asyncio.sleep(random.uniform(*self.config['scroll_interval']))

            # 使用搜索页面一次性提取的标题，如果提取不到标题就用当前搜索关键词
            if video_title_from_search and len(video_title_from_search.strip()) > 0:
                video_title = video_title_from_search.strip()
            else:
                # 提取失败，直接使用搜索关键词作为标题
                video_title = self.search_keyword
            print(f"📹 当前视频标题: {video_title[:60]}{'...' if len(video_title) > 60 else ''}", flush=True)

            # 检测并关闭"打开外部链接"弹窗（抖音打开其他链接时会弹出）
            try:
                popup_closed = await page.evaluate("""
                    () => {
                        // 查找打开外部链接确认弹窗，点击取消/关闭
                        const closeSelectors = [
                            '[data-e2e="cancel"]',
                            '.cancel-btn',
                            'button:has-text("取消")',
                            'button:has-text("关闭")',
                            '.modal-close',
                            '.close-btn',
                            '[aria-label="关闭"]'
                        ];
                        for (const selector of closeSelectors) {
                            const btn = document.querySelector(selector);
                            if (btn && getComputedStyle(btn).display !== 'none') {
                                btn.click();
                                console.log('Closed external link popup');
                                return true;
                            }
                        }
                        return false;
                    }
                """);
                if popup_closed:
                    print("[调试] 检测到外部链接弹窗并已关闭", flush=True);
            except Exception as e:
                pass

            # 已经直接导航到视频详情页，评论区默认已打开
            # 无需点击视频，无需按x，直接处理评论
            print("[调试] 已在视频详情页，评论区已打开，直接处理评论", flush=True)

            # 检查是否应该回复（按概率）
            if random.random() > self.config['reply_probability']:
                return True  # 已经成功处理（只是不回复）

            # 风控检查：这一轮是否允许回复
            if not self.risk_controller.can_reply():
                return True

            # 检查关键词过滤，如果包含跳过关键词则不处理
            if self._check_skip_keywords(video_title):
                if self.debug:
                    print(f"[调试] 视频包含跳过关键词，跳过回复: {video_title[:30]}...", flush=True)
                return True

            # 展开评论区 - 在详情页评论区已经自动加载
            comments = await self._get_comments(page)
            if not comments:
                if self.debug:
                    print("[调试] 当前视频没有找到评论，跳过回复", flush=True)
                return True  # 视频处理成功，只是没有评论

            print(f"💬 找到 {len(comments)} 条评论，准备回复", flush=True)

            # 随机选一条评论回复
            comment = random.choice(comments)
            comment_text = comment['text']
            comment_element = comment['element']

            print(f"🗨️ 选择评论: {comment_text[:40]}{'...' if len(comment_text) > 40 else ''}", flush=True)

            # 关键词过滤：包含违禁词不回复
            if self._check_forbidden_keywords(comment_text):
                if self.debug:
                    print(f"[调试] 评论包含违禁关键词，跳过回复", flush=True)
                return True

            # 分析评论
            analysis = analyze_comment(comment_text)
            if self.debug:
                print(f"[调试] 评论分析: 情感={analysis['sentiment']}, 类型={analysis['type']}", flush=True)

            # 生成回复
            reply_text = generate_reply(comment_text, video_title, self.config)
            print(f"🤖 生成回复: {reply_text}", flush=True)

            # 执行回复
            success = await self._send_reply(page, comment_element, reply_text)
            if success:
                self.reply_count += 1
                self.risk_controller.record_reply(None, comment_text, reply_text)
                print(f"✅ 成功发送回复！", flush=True)
            else:
                print(f"⚠️  发送回复失败", flush=True)

            # 不需要返回搜索页，主循环会直接导航下一个视频
            return True

        except Exception as e:
            print(f"❌ 处理视频出错: {str(e)[:100]}", flush=True)
            return False

    async def _get_video_title(self, page: Page) -> str:
        """获取当前视频标题 - 适配抖音网页版最新结构 2025，支持搜索结果页和视频详情页"""
        try:
            # 适配多种页面结构：视频详情页/搜索结果页/推荐页/精选页
            result = await page.evaluate("""
                () => {
                    const candidates = [];
                    const banList = ['我的喜欢', '我的收藏', '观看历史', '稍后再看', '精选', '推荐', '搜索', '关注', '朋友', '直播', '放映厅', '短剧', '下载抖音'];
                    // *** 优先找视频详情页的标题（我们现在直接导航到详情页）***
                    const detailSelectors = [
                        '.video-info .title',
                        '.video-detail .title',
                        '[data-e2e="video-title"]',
                        '.desc',
                        '.video-desc',
                        '.zCT1e3Zf',
                        'h1.title',
                        '.container h1',
                        '.content-title'
                    ];
                    for (const selector of detailSelectors) {
                        const elements = document.querySelectorAll(selector);
                        for (let el of elements) {
                            const rect = el.getBoundingClientRect();
                            const text = el.textContent.trim();
                            // 详情页标题应该在可视区域上半部分
                            // 排除太短的，作者名一般 < 8个字，视频标题一般更长
                            // 要求rect.bottom > 0，确保元素在可视区域内 - 过滤SPA滚动出去的旧标题
                            // 排除display: none，过滤掉已经隐藏的旧标题
                            if (getComputedStyle(el).display !== 'none'
                                && rect.top > 10 && rect.top < window.innerHeight * 0.4
                                && rect.bottom > 0
                                && rect.height > 10
                                && rect.width > 100
                                && text.length > 8
                                && !banList.some(ban => text.includes(ban))) {
                                // 保存所有候选
                                candidates.push({
                                    text: text,
                                    top: rect.top,
                                    len: text.length
                                });
                            }
                        }
                    }
                    // 回退：找所有 zCT1e3Zf
                    const titles = Array.from(document.querySelectorAll('.zCT1e3Zf'));
                    for (const title of titles) {
                        const rect = title.getBoundingClientRect();
                        const text = title.textContent.trim();
                        // 排除太短的，作者名一般 < 8个字，视频标题一般更长
                        // 要求rect.bottom > 0，确保元素在可视区域内 - 过滤SPA滚动出去的旧标题
                        // 排除display: none，过滤掉已经隐藏的旧标题
                        if (getComputedStyle(title).display !== 'none'
                            && rect.top > 10 && rect.top < window.innerHeight * 0.4
                            && rect.bottom > 0
                            && rect.height > 10
                            && text.length > 8
                            && !banList.some(ban => text.includes(ban))) {
                            candidates.push({
                                text: text,
                                top: rect.top,
                                len: text.length
                            });
                        }
                    }
                    // 方法三：搜索结果页 - 找各种title选择器
                    const searchSelectors = [
                        '[data-e2e="video-title"]',
                        '.search-video-card .title',
                        '.video-card .title',
                        '.search-card-title',
                        '.video-title',
                        'a[href*="/video/"] div'
                    ];
                    for (const selector of searchSelectors) {
                        const elements = document.querySelectorAll(selector);
                        for (let el of elements) {
                            const rect = el.getBoundingClientRect();
                            const text = el.textContent.trim();
                            const banList = ['我的喜欢', '我的收藏', '观看历史', '稍后再看', '精选', '推荐', '搜索', '关注', '朋友', '直播', '放映厅', '短剧', '下载抖音'];
                            // 排除太短的，作者名一般 < 8个字，视频标题一般更长
                            // 要求rect.bottom > 0，确保元素在可视区域内 - 过滤SPA滚动出去的旧标题
                            if (rect.top > 10 && rect.top < window.innerHeight * 0.4
                                && rect.bottom > 0
                                && rect.height > 10
                                && text.length > 8
                                && !banList.some(ban => text.includes(ban))) {
                                candidates.push({
                                    text: text,
                                    top: rect.top,
                                    len: text.length
                                });
                            }
                        }
                    }
                    // 方法四：终极方案，遍历所有可见元素找长文本
                    const allElements = document.querySelectorAll('div, p, span, a');
                    for (let el of allElements) {
                        const rect = el.getBoundingClientRect();
                        // 排除display: none，过滤掉已经隐藏的旧标题
                        if (getComputedStyle(el).display !== 'none'
                            && rect.top > 10 && rect.top < window.innerHeight * 0.4
                            && rect.bottom > 0
                            && rect.height > 10 && rect.width > 100
                            && el.textContent) {
                            const text = el.textContent.trim();
                            const banList = ['我的喜欢', '我的收藏', '观看历史', '稍后再看', '精选', '推荐', '搜索', '关注', '朋友', '直播', '放映厅', '短剧', '下载抖音'];
                            if (text.length > 10 && text.length < 200
                                && !banList.some(ban => text.includes(ban))) {
                                candidates.push({
                                    text: text,
                                    top: rect.top,
                                    len: text.length
                                });
                            }
                        }
                    }
                    // 如果有候选，排序优先级：
                    // 1. 更长文本优先（视频标题比作者名长）
                    // 2. 更靠近页面顶部优先
                    if (candidates.length > 0) {
                        candidates.sort((a, b) => {
                            // 更长的排在前面
                            if (b.text.length !== a.text.length) {
                                return b.text.length - a.text.length;
                            }
                            // 长度相同，更靠上的排在前面
                            return a.top - b.top;
                        });
                        console.log('Selected title:', candidates[0].text, '(length:', candidates[0].text.length, 'top:', candidates[0].top, ')');
                        return candidates[0].text;
                    }
                    return null;
                }
            """);

            if result:
                return result;

            # Fallback to more selectors
            selectors = [
                '.swiper-slide-active .video-info .desc',
                '.swiper-card-active .title',
                '.feed-card-active .desc',
                '[data-e2e="video-desc"]',
                '.video-desc',
                '.video-info .title',
                '.title',
                '.desc',
                '[data-e2e="search-card-container"] .title',
                '.result-item .title'
            ]
            for selector in selectors:
                element = await page.query_selector(selector)
                if element:
                    text = await element.text_content()
                    if text and len(text.strip()) > 5:
                        return text.strip()
            return ""
        except:
            return ""

    def _check_skip_keywords(self, text: str) -> bool:
        """检查是否包含跳过关键词"""
        if not self.config['keyword_filter']['enabled']:
            return False
        skip_keywords = self.config['keyword_filter']['skip_keywords']
        text_lower = text.lower()
        return any(keyword.lower() in text_lower for keyword in skip_keywords)

    def _check_forbidden_keywords(self, text: str) -> bool:
        """检查是否包含违禁关键词"""
        if not self.config['keyword_filter']['enabled']:
            return False
        forbidden_keywords = self.config['keyword_filter']['forbidden_keywords']
        text_lower = text.lower()
        return any(keyword.lower() in text_lower for keyword in forbidden_keywords)

    async def _get_comments(self, page: Page):
        """获取可见的评论列表 - 适配抖音网页版最新结构 2025"""
        try:
            # 已经在视频详情页，评论区默认已经打开
            print("[调试] 视频详情页评论区已打开，直接提取评论", flush=True)
            await asyncio.sleep(2)

            # 抖音网页版评论在右侧侧边栏，需要滚动评论容器才能加载
            # 先找评论容器
            comment_container = await page.query_selector('.comment-list, .comments-container, [data-e2e="comment-list"]')
            if comment_container:
                # Scroll the comment container itself
                for i in range(10):
                    await comment_container.evaluate("el => el.scrollTop = el.scrollHeight")
                    await asyncio.sleep(0.5)

            # 滚动页面继续加载更多评论
            for i in range(5):
                await page.mouse.wheel(0, 150)
                await asyncio.sleep(0.8)

            # 使用JS提取评论，适配最新DOM结构
            # In current抖音 webpage, comments are in [data-e2e="comment-item"]
            comment_elements = await page.query_selector_all('[data-e2e="comment-item"]')
            if not comment_elements or len(comment_elements) == 0:
                comment_elements = await page.query_selector_all('.comment-item')

            # Get final list
            final_comments = []
            for elem in comment_elements:
                text_found = False
                # Try selectors first
                text_selectors = [
                    '[data-e2e="comment-text"]',
                    '.comment-content',
                    '.content',
                    '.text-content',
                    '.comment-text'
                ]
                text = None
                for text_sel in text_selectors:
                    text_elem = await elem.query_selector(text_sel)
                    if text_elem:
                        text = await text_elem.text_content()
                        text = text.strip()
                        if len(text) >= 2:
                            break
                # If no selector found, get text from comment-item directly
                if not text or len(text) < 2:
                    text = await elem.text_content()
                    text = text.strip()
                if text and len(text) >= 2:
                    # Remove trailing metadata: "...1月前·河南 4 分享 回复 展开N条回复"
                    # Stop at first date/time marker like "X月前", "X天前", "X小时前", "X分钟前"
                    import re
                    # Extract the actual comment text before the timestamp
                    match = re.search(r'(.+?)(\d+\s*(月|天|小时|分钟)前.*)', text)
                    if not match:
                        # Try without space
                        match = re.search(r'(.+?)(\d+(月|天|小时|分钟)前.*)', text)
                    if match:
                        cleaned = match.group(1).strip()
                    else:
                        cleaned = text
                    # Remove "回复" at the end that's part of the UI text
                    cleaned = re.sub(r'\s*回复\s*(展开\d+条回复)?$', '', cleaned)
                    # Only filter out "查看更多回复" system text
                    if '查看更多回复' not in cleaned and len(cleaned) >= 2:
                        final_comments.append({
                            'element': elem,
                            'text': cleaned
                        })
                text_found = True

            # 过滤掉已回复过的评论（风控）
            if self.config['risk_control']['avoid_repeat_reply'] and len(final_comments) > 10:
                # 只取前10条，随机选
                final_comments = final_comments[:10]

            return final_comments

        except Exception as e:
            if self.debug:
                print(f"[调试] 获取评论出错: {e}", flush=True)
            return []

    async def _send_reply(self, page: Page, comment_element, reply_text: str) -> bool:
        """发送回复"""
        try:
            # 在抖音网页版，需要点击评论下方的"回复"按钮才能唤起回复输入框
            # 先点击回复按钮
            await comment_element.evaluate("""
                (commentEl) => {
                    // 找到最后一个回复按钮
                    const allElements = commentEl.querySelectorAll('*');
                    let replyBtn = null;
                    for (let el of allElements) {
                        if (el.textContent?.trim() === '回复') {
                            replyBtn = el;
                        }
                    }
                    if (replyBtn) {
                        replyBtn.scrollIntoView({block: 'center'});
                        setTimeout(() => replyBtn.click(), 300);
                        return true;
                    }
                    return false;
                }
            """);
            print("[调试] 点击了评论下方的回复按钮", flush=True)
            await asyncio.sleep(random.uniform(3, 5))  # 等待回复框完全弹出

            # 直接在当前页面查找回复输入框（修复上下文失效问题）
            input_box = None
            is_content_editable = False
            input_selectors = [
                # 抖音网页版最新回复输入框选择器
                'textarea[placeholder^="说点什么"]',
                'textarea[placeholder*="友善的评论是交流的起点"]',
                'textarea[data-e2e="reply-input"]',
                '.comment-input textarea',
                '.reply-editor textarea',
                '#comment-content textarea',
                # 支持contenteditable类型的输入框
                '[contenteditable="true"][placeholder*="说点什么"]',
                '.public-DraftEditor-content[contenteditable="true"]',
                '.reply-input [contenteditable="true"]'
            ]

            print("[调试] 开始查找回复输入框...", flush=True)
            # 重试几次找输入框
            found_selector = None
            for retry in range(4):
                for selector in input_selectors:
                    try:
                        input_box = await page.wait_for_selector(selector, timeout=3000, state="visible")
                        if input_box:
                            found_selector = selector
                            break
                    except Exception as e:
                        continue
                if input_box:
                    break
                # 刷新一下，可能弹窗被挡住了
                await page.keyboard.press('Escape')
                await asyncio.sleep(1)
                # 再点一次回复按钮
                await comment_element.evaluate("""
                    (commentEl) => {
                        const btns = commentEl.querySelectorAll('*');
                        for (let el of btns) {
                            if (el.textContent?.trim() === '回复') {
                                el.click();
                                break;
                            }
                        }
                    }
                """);
                await asyncio.sleep(2)

            if self.debug and found_selector:
                print(f"[调试] 找到输入框，选择器: {found_selector}", flush=True)

            if not input_box:
                if self.debug:
                    print("[调试] 未找到回复输入框，跳过回复", flush=True)
                # 按ESC清理弹窗
                await page.keyboard.press('Escape')
                return False

            print(f"[调试] 找到回复输入框，准备输入内容: {reply_text}", flush=True)
            # 检查输入框类型是不是contenteditable
            tag_name = await input_box.evaluate('el => el.tagName.toLowerCase()')
            is_content_editable = await input_box.evaluate('el => el.isContentEditable')
            print(f"[调试] 输入框类型: {tag_name}, 可编辑: {is_content_editable}", flush=True)

            # 点击输入框激活，然后真正逐字输入，保证框架能检测到每个字符
            print(f"[调试] 开始输入回复内容: {reply_text}", flush=True)
            try:
                # 先点击激活
                await input_box.click()
                await asyncio.sleep(random.uniform(0.3, 0.7))
                # 全选清空原有内容
                await page.keyboard.press('Control+A')
                await asyncio.sleep(0.2)
                await page.keyboard.press('Backspace')
                await asyncio.sleep(0.2)
                # 真正逐字输入，每个字符都会触发input事件，框架肯定能检测到
                await input_box.type(reply_text, delay=random.randint(60, 150))
                await asyncio.sleep(random.uniform(0.5, 1))
                # 不需要再输入一遍，JS已经设置好了。只是放在这里模拟一下人类点击延迟
                await asyncio.sleep(random.uniform(0.5, 1))
            except Exception as e:
                if self.debug:
                    print(f"[调试] JS设置失败，回退到普通输入: {str(e)[:100]}", flush=True)
                # 回退到原来的方式：先全选清除
                try:
                    await input_box.click()
                    await asyncio.sleep(random.uniform(0.3, 0.7))
                    await page.keyboard.press('Control+A')
                    await asyncio.sleep(0.2)
                    await page.keyboard.press('Backspace')
                    await asyncio.sleep(0.2)
                    if tag_name == 'textarea' or tag_name == 'input':
                        await input_box.fill(reply_text)
                    else:
                        escaped_reply = reply_text.replace('"', '\\"').replace('\n', '\\n')
                        await input_box.evaluate(f'(el) => el.textContent = "{escaped_reply}"')
                        # 触发input事件让框架检测到变化
                        await input_box.evaluate('''
                            (el) => {
                                const event = new InputEvent('input', { bubbles: true, cancelable: true });
                                el.dispatchEvent(event);
                            }
                        ''');
                    await asyncio.sleep(0.3)
                    await input_box.click()
                    await asyncio.sleep(0.2)
                    # 用户建议：按一下空格再删除，确保触发最后一个input事件
                    await page.keyboard.press('Space')
                    await asyncio.sleep(0.2)
                    await page.keyboard.press('Backspace')
                    await asyncio.sleep(0.2)
                except:
                    pass

            # 检查输入的内容是否正确
            try:
                if tag_name == 'textarea' or tag_name == 'input':
                    input_value = await input_box.input_value()
                else:
                    input_value = await input_box.evaluate('el => el.textContent')
                print(f"[调试] 输入完成，输入框内容: {input_value.strip()}", flush=True)

                # 宽松匹配，忽略前后空格和结尾标点差异
                input_clean = input_value.strip().rstrip('~！。，.?!')
                reply_clean = reply_text.strip().rstrip('~！。，.?!')
                if input_clean != reply_clean:
                    print(f"[调试] 输入内容不匹配，重新输入...", flush=True)
                    if tag_name == 'textarea' or tag_name == 'input':
                        await input_box.fill(reply_text)
                    else:
                        await input_box.evaluate(f'(el) => el.textContent = "{reply_text}"')
                    await asyncio.sleep(0.5)
            except Exception as e:
                if self.debug:
                    print(f"[调试] 获取输入内容失败: {str(e)[:50]}", flush=True)

            # 用户调试确认：发送按钮固定坐标 x=1406，y=距离容器底部24像素
            # 显示点击标记帮助确认
            await input_box.evaluate("""
                (inputEl) => {
                    const FIXED_X = 1406;
                    const Y_OFFSET_FROM_BOTTOM = 24;

                    let container = inputEl;
                    for (let i = 0; i < 6 && container; i++) {
                        if (container.className.includes('comment-input')) {
                            break;
                        }
                        container = container.parentElement;
                    }
                    const rect = (container || inputEl).getBoundingClientRect();
                    const actualY = rect.bottom - Y_OFFSET_FROM_BOTTOM;

                    // 在点击位置显示红色视觉标记，方便确认位置
                    const marker = document.createElement('div');
                    marker.style.position = 'fixed';
                    marker.style.left = FIXED_X + 'px';
                    marker.style.top = actualY + 'px';
                    marker.style.width = '20px';
                    marker.style.height = '20px';
                    marker.style.marginLeft = '-10px';
                    marker.style.marginTop = '-10px';
                    marker.style.background = 'rgba(255, 0, 0, 0.6)';
                    marker.style.border = '2px solid white';
                    marker.style.borderRadius = '50%';
                    marker.style.zIndex = '999999';
                    marker.style.pointerEvents = 'none';
                    document.body.appendChild(marker);
                    // 3秒后消失
                    setTimeout(() => marker.remove(), 3000);

                    // 点击这个位置
                    const elem = document.elementFromPoint(FIXED_X, actualY);
                    if (elem) {
                        try {
                            const event = new MouseEvent('click', {
                                bubbles: true,
                                cancelable: true,
                                view: window
                            });
                            elem.dispatchEvent(event);
                            if (typeof elem.click === 'function') {
                                elem.click();
                            }
                            console.log(`Clicked send button at fixed position x=${FIXED_X}, y=${actualY}, tag=${elem.tagName}`);
                        } catch (e) {
                            console.error('Click failed:', e);
                        }
                    }
                }
            """);
            print("[调试] ✅ 已点击固定发送按钮位置: x=1406, y=距离底部24px", flush=True);
            # 等待发送完成
            await asyncio.sleep(random.uniform(1.5, 2.5))

            # 检查是否发送成功
            try:
                # After sending, Douyin should have cleared the input box and it should be empty now
                # We already typed the content, so if sending succeeded, it should be empty now
                still_visible = await input_box.is_visible()
                final_send_success = False

                # Check for "发送成功" toast notification - use JS to find since :has-text is not native
                has_success_toast = await page.evaluate("""
                    () => {
                        const elems = document.querySelectorAll('[role="status"], [class*="toast"], .toast');
                        for (const el of elems) {
                            if (el.textContent.includes('发送成功')) {
                                return true;
                            }
                        }
                        return false;
                    }
                """);
                if has_success_toast:
                    print("✅ 检测到抖音发送成功提示", flush=True)
                    final_send_success = True
                else:
                    # If no toast visible, check that input is empty after sending
                    if still_visible:
                        if tag_name == 'textarea' or tag_name == 'input':
                            input_value_after = await input_box.input_value()
                        else:
                            input_value_after = await input_box.evaluate('el => el.textContent');

                        if input_value_after.strip() == '':
                            print("✅ 回复发送成功！输入框已清空", flush=True)
                            final_send_success = True
                        else:
                            print(f"⚠️ 输入框还有内容: {input_value_after.strip()}，可能发送失败", flush=True)
                    else:
                        print("✅ 回复发送成功！输入框已消失", flush=True)
                        final_send_success = True

            except Exception as e:
                if self.debug:
                    print(f"[调试] 检查发送状态失败: {str(e)[:50]}", flush=True)
                # 没报错就默认成功
                final_send_success = True

            # 关闭回复框
            try:
                await page.keyboard.press('Escape')
                await asyncio.sleep(0.5)
            except:
                pass

            return final_send_success

        except Exception as e:
            if self.debug:
                print(f"[调试] 发送回复出错: {e}", flush=True)
            # 出错时按ESC清理
            try:
                await page.keyboard.press('Escape')
            except:
                pass
            return False

    async def _scroll_to_next(self, page: Page):
        """模拟人类滚动到下一个视频"""
        if self.config['risk_control']['human_like_scroll']:
            # 模拟人类滚动：先小幅滚动，再大幅滚动 - increased distance to ensure new video loads
            await page.mouse.wheel(0, random.randint(500, 700))
            await asyncio.sleep(random.uniform(0.3, 0.8))
            await page.mouse.wheel(0, random.randint(700, 1000))
            await asyncio.sleep(0.5)
            # One more big scroll to be safe
            await page.mouse.wheel(0, random.randint(300, 500))
        else:
            await page.mouse.wheel(0, 1500)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="抖音自动刷视频+评论回复机器人")
    parser.add_argument("--config", default="../config/config.yaml", help="配置文件路径")
    parser.add_argument("--no-debug", dest="debug", action="store_false", help="关闭调试模式")
    args = parser.parse_args()
    # 默认开启调试（输出DOM结构、截图）
    if not hasattr(args, 'debug'):
        args.debug = True

    try:
        bot = DouyinAutoReplyBot(args.config, args.debug)
        asyncio.run(bot.run())
    except Exception as e:
        print(f"❌ 启动失败: {e}")
        exit(1)

if __name__ == "__main__":
    main()
