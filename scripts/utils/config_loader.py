"""
配置文件加载工具
"""
import yaml
from pathlib import Path
from typing import Dict

def load_config(config_path: str) -> Dict:
    """加载YAML配置文件"""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    
    with open(path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    # 设置默认值
    config.setdefault('scroll_interval', [3, 8])
    config.setdefault('reply_probability', 0.3)
    config.setdefault('max_replies_per_hour', 20)
    config.setdefault('min_reply_interval', 10)
    config.setdefault('reply_mode', 'template')
    config.setdefault('reply_templates', {
        '感谢类': ['谢谢支持~'],
        '互动类': ['你说得对👍'],
        '提问类': ['这个问题很好哦~'],
        '通用类': ['666']
    })
    config.setdefault('keyword_filter', {
        'enabled': True,
        'forbidden_keywords': [],
        'skip_keywords': ['广告', '互粉', '加v']
    })
    config.setdefault('risk_control', {
        'random_wait': True,
        'human_like_scroll': True,
        'avoid_repeat_reply': True,
        'auto_sleep': ['22:00', '08:00']
    })
    
    return config
