"""
回复生成器：支持模板回复和火山DeepSeek AI回复两种模式
"""
import random
import requests
from typing import List, Dict

class ReplyGenerator:
    def __init__(self, config: Dict):
        self.config = config
        self.reply_mode = config.get('reply_mode', 'template')
        self.templates = config.get('reply_templates', {})

        if self.reply_mode == 'ai':
            ai_config = config.get('ai_config', {})
            self.api_key = ai_config.get('api_key', '')
            self.base_url = ai_config.get('base_url', 'https://ark.cn-beijing.volces.com/api/v3/responses')
            self.ai_model = ai_config.get('model', 'deepseek-v3-2-251201')
            self.ai_prompt = ai_config.get('prompt', '生成抖音风格的短回复，不超过20字。')
            self.background_info = ai_config.get('background_info', '')
            self.reply_intention = ai_config.get('reply_intention', '友善互动，给视频评论区增加活跃气氛')
            self.max_length = ai_config.get('max_length', 20)
            self.enable_web_search = ai_config.get('enable_web_search', False)

    def generate(self, comment_text: str, video_title: str) -> str:
        """生成回复内容"""
        if self.reply_mode == 'template':
            return self._generate_template_reply(comment_text)
        elif self.reply_mode == 'ai':
            return self._generate_ai_reply(comment_text, video_title)
        else:
            return random.choice(self.templates.get('通用类', ['666', '不错~']))

    def _generate_template_reply(self, comment_text: str) -> str:
        """基于模板生成回复"""
        comment_lower = comment_text.lower()
        
        if any(key in comment_lower for key in ['谢谢', '感谢', '支持', '喜欢']):
            return random.choice(self.templates.get('感谢类', ['谢谢支持~']))
        elif any(key in comment_lower for key in ['对', '没错', '同意', '说得好']):
            return random.choice(self.templates.get('互动类', ['你说得对👍']))
        elif any(key in comment_lower for key in ['?', '？', '怎么', '什么', '为什么']):
            return random.choice(self.templates.get('提问类', ['这个问题很好哦~']))
        else:
            return random.choice(self.templates.get('通用类', ['哈哈太有意思了😂']))

    def _generate_ai_reply(self, comment_text: str, video_title: str) -> str:
        """基于火山引擎DeepSeek V3生成回复"""
        if not self.api_key:
            print("⚠️ 未配置API Key，使用模板回复")
            return self._generate_template_reply(comment_text)

        print(f"🤖 调用火山方舟AI API生成回复...")
        
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            prompt = f"""{self.ai_prompt}

我的背景信息：
{self.background_info}

回复要求：
{self.reply_intention}

重要提示：
- 请根据聊天上下文自然回复对方的消息，只回复一次即可
- 你知道我的租号需求，但这**不需要每条回复都主动提起**
- 只有当对方问到租号、收号、打游戏相关问题时，你再说明我的需求
- 正常聊天交流即可，保持自然，不要重复输出背景信息
- 直接给出你的回复，不要额外说明

{video_title}
用户最新消息：{comment_text}

请直接给出回复："""
            
            # OpenAI兼容格式
            payload = {
                "model": self.ai_model,
                "stream": False,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "max_tokens": self.max_length + 10,
                "temperature": 0.7
            }
            
            # 可选开启联网搜索
            if self.enable_web_search:
                payload["tools"] = [
                    {
                        "type": "web_search",
                        "max_keyword": 3
                    }
                ]
            
            # 火山方舟OpenAI兼容端点应该是 /chat/completions，不是 /responses
            url = self.base_url
            if '/responses' in url:
                url = url.replace('/responses', '/chat/completions')
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            response.raise_for_status()
            result = response.json()
            
            # 解析回复内容
            if result.get('choices') and len(result['choices']) > 0:
                reply = result['choices'][0]['message']['content'].strip()
            else:
                reply = result.get('content', '').strip()

            # 去掉所有换行和空格，变成连续字符串
            reply = reply.replace('\n', '').replace('\r', '').replace(' ', '').strip()

            # 确保回复长度不超过最大限制
            if len(reply) > self.max_length:
                reply = reply[:self.max_length] + "..."

            if reply:
                print(f"✅ AI成功生成回复: {reply}")
            return reply if reply else self._generate_template_reply(comment_text)
            
        except Exception as e:
            print(f"⚠️  AI生成回复失败，回退到模板回复: {str(e)[:100]}")
            return self._generate_template_reply(comment_text)

# 对外接口
def generate_reply(comment_text: str, video_title: str, config: Dict) -> str:
    generator = ReplyGenerator(config)
    return generator.generate(comment_text, video_title)
