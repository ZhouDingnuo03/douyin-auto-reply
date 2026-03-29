"""
风控控制模块：防止账号被检测
"""
import time
import random
from datetime import datetime
from typing import Dict, List

class RiskController:
    def __init__(self, config: Dict):
        self.config = config
        self.risk_config = config.get('risk_control', {})
        self.filter_config = config.get('keyword_filter', {})
        
        self.reply_history = []  # 回复历史
        self.last_reply_time = 0
        self.hourly_reply_count = 0
        self.hour_start_time = time.time()

    def can_operate(self) -> bool:
        """检查是否可以进行操作"""
        # 检查夜间休眠
        auto_sleep = self.risk_config.get('auto_sleep', ["22:00", "08:00"])
        if len(auto_sleep) == 2:
            try:
                start_hour, start_min = map(int, auto_sleep[0].split(':'))
                end_hour, end_min = map(int, auto_sleep[1].split(':'))
                now = datetime.now()
                current_hour, current_min = now.hour, now.minute
                current_total = current_hour * 60 + current_min
                start_total = start_hour * 60 + start_min
                end_total = end_hour * 60 + end_min
                # 如果休眠区间跨天
                if start_total < end_total:
                    if current_total >= start_total and current_total <= end_total:
                        return False
                else:
                    if current_total >= start_total or current_total <= end_total:
                        return False
            except:
                pass

        # 重置小时计数器如果已经过了一小时
        if time.time() - self.hour_start_time > 3600:
            self.hourly_reply_count = 0
            self.hour_start_time = time.time()

        # 检查每小时最大回复数
        max_replies = self.config.get('max_replies_per_hour', 20)
        if self.hourly_reply_count >= max_replies:
            return False

        return True

    def can_reply(self) -> bool:
        """检查是否可以回复（除了can_operate的检查，再加间隔检查）"""
        if not self.can_operate():
            return False

        # 检查最小间隔
        min_interval = self.config.get('min_reply_interval', 30)
        if self.last_reply_time > 0:
            if time.time() - self.last_reply_time < min_interval:
                return False

        return True

    def should_skip_video(self, video_title: str) -> bool:
        """检查是否需要跳过该视频"""
        if not self.filter_config.get('enabled', True):
            return False
        
        skip_keywords = self.filter_config.get('skip_keywords', [])
        title_lower = video_title.lower()
        return any(keyword.lower() in title_lower for keyword in skip_keywords)

    def should_skip_comment(self, comment_text: str) -> bool:
        """检查是否需要跳过该评论"""
        if not self.filter_config.get('enabled', True):
            return False
        
        forbidden_keywords = self.filter_config.get('forbidden_keywords', [])
        comment_lower = comment_text.lower()
        
        # 敏感词过滤
        if any(keyword.lower() in comment_lower for keyword in forbidden_keywords):
            return True
        
        # 避免重复回复同一用户
        if self.risk_config.get('avoid_repeat_reply', True):
            # 检查最近1小时内是否回复过相同内容
            recent_replies = [r for r in self.reply_history if time.time() - r['time'] < 3600]
            if any(comment_text == r['comment_text'] for r in recent_replies):
                return True
        
        return False

    def record_reply(self, user: str = None, comment_text: str = None, reply_content: str = None):
        """记录回复"""
        self.reply_history.append({
            'time': time.time(),
            'user': user or '',
            'comment_text': comment_text or '',
            'reply_content': reply_content or ''
        })

        self.last_reply_time = time.time()
        self.hourly_reply_count += 1
