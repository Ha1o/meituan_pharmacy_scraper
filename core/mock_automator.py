"""
mock_automator.py - Mock自动化模块
用于并发压测，不连接真实设备，模拟采集流程
"""
import os
import time
import random
from typing import Optional, Dict, List, Any
from pathlib import Path


class MockAutomator:
    """
    Mock自动化器，模拟设备操作
    用于多设备并发压测，无需真实设备
    """
    
    # 模拟分类数据
    MOCK_CATEGORIES = [
        "推荐", "感冒用药", "咳嗽用药", "肠胃用药", "止痛退烧",
        "皮肤用药", "眼科用药", "维生素", "保健品", "医疗器械"
    ]
    
    # 模拟药品数据模板
    MOCK_DRUGS = [
        {"name": "[999]感冒灵颗粒10g*9袋/盒", "price": "19.8", "sales": "156"},
        {"name": "[云南白药]创可贴20片/盒", "price": "12.5", "sales": "89"},
        {"name": "[同仁堂]六味地黄丸200粒/瓶", "price": "35.0", "sales": "67"},
        {"name": "[芬必得]布洛芬缓释胶囊0.3g*20粒", "price": "28.9", "sales": "234"},
        {"name": "[修正]板蓝根颗粒10g*20袋", "price": "15.5", "sales": "178"},
        {"name": "[江中]健胃消食片0.8g*32片", "price": "22.0", "sales": "145"},
        {"name": "[三九]抗病毒口服液10ml*6支", "price": "18.6", "sales": "112"},
        {"name": "[康恩贝]肠炎宁片0.42g*24片", "price": "16.8", "sales": "98"},
        {"name": "[力度伸]维生素C泡腾片1g*10片", "price": "32.5", "sales": "203"},
        {"name": "[葵花]护肝片0.35g*100片", "price": "45.0", "sales": "76"},
    ]
    
    def __init__(
        self, 
        device_serial: str, 
        logger=None, 
        config: dict = None,
        failure_rate: float = 0.0  # 默认不失败，压测时确保稳定
    ):
        """
        初始化Mock自动化器
        
        Args:
            device_serial: 设备序列号（如 MOCK-001）
            logger: 日志器
            config: 配置（忽略）
            failure_rate: 随机失败率（0-1）
        """
        self.device_serial = device_serial
        self.logger = logger
        self.failure_rate = failure_rate
        
        # 模拟状态
        self._connected = False
        self._current_category_index = 0
        self._current_scroll_position = 0
        self._items_per_scroll = 3  # 每次滚动显示的商品数
        
        # 模拟设备信息
        self.device = MockDevice(device_serial)
    
    def connect(self) -> bool:
        """模拟连接设备"""
        time.sleep(random.uniform(0.1, 0.3))
        self._connected = True
        if self.logger:
            self.logger.info(f"[Mock] 设备 {self.device_serial} 已连接")
        return True
    
    def disconnect(self):
        """模拟断开连接"""
        self._connected = False
        if self.logger:
            self.logger.info(f"[Mock] 设备 {self.device_serial} 已断开")
    
    def start_app(self) -> bool:
        """模拟启动App"""
        self._maybe_fail("启动App")
        time.sleep(random.uniform(0.2, 0.5))
        if self.logger:
            self.logger.info("[Mock] 美团App已启动")
        return True
    
    def stop_app(self):
        """模拟停止App"""
        time.sleep(random.uniform(0.05, 0.1))
        if self.logger:
            self.logger.debug("[Mock] 美团App已停止")
    
    def swipe_up(self, duration: float = 0.5):
        """模拟向上滑动"""
        self._maybe_fail("滑动")
        time.sleep(random.uniform(0.1, 0.3))
        self._current_scroll_position += 1
        if self.logger:
            self.logger.debug(f"[Mock] 向上滑动 (位置: {self._current_scroll_position})")
    
    def press_back(self):
        """模拟返回"""
        time.sleep(random.uniform(0.05, 0.1))
    
    def handle_error_screens(self) -> bool:
        """模拟错误页面检测（总是返回False，无错误）"""
        return False
    
    def is_page_loaded(self, min_chinese_chars: int = 10) -> bool:
        """模拟页面加载检测（总是返回True）"""
        return True
    
    def screenshot(self, filepath: str) -> bool:
        """模拟截图，写占位文件"""
        try:
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, 'wb') as f:
                # 写入极简占位PNG（1x1透明像素）
                f.write(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')
            return True
        except:
            return False
    
    def get_visible_products(self, category_name: str) -> List[Dict[str, str]]:
        """
        获取当前可见的商品列表（Mock数据）
        
        Args:
            category_name: 当前分类名
            
        Returns:
            商品列表 [{drug_name, price, sales, category}]
        """
        self._maybe_fail("获取商品")
        
        # 模拟滚动到底部后无更多数据
        max_items = len(self.MOCK_DRUGS)
        start_idx = self._current_scroll_position * self._items_per_scroll
        
        if start_idx >= max_items:
            return []  # 无更多数据
        
        end_idx = min(start_idx + self._items_per_scroll, max_items)
        
        products = []
        for i in range(start_idx, end_idx):
            drug = self.MOCK_DRUGS[i % len(self.MOCK_DRUGS)]
            products.append({
                "drug_name": drug["name"],
                "price": drug["price"],
                "sales": drug["sales"],
                "category": category_name
            })
        
        return products
    
    def get_categories(self) -> List[str]:
        """获取分类列表（Mock数据）"""
        return self.MOCK_CATEGORIES.copy()
    
    def reset_scroll_position(self):
        """重置滚动位置"""
        self._current_scroll_position = 0
    
    def _maybe_fail(self, operation: str):
        """按概率随机抛异常，模拟失败"""
        if random.random() < self.failure_rate:
            raise Exception(f"[Mock] 模拟失败: {operation}")


class MockDevice:
    """模拟 uiautomator2 设备对象"""
    
    def __init__(self, serial: str):
        self.serial = serial
        self._info = {
            "displayWidth": 1080,
            "displayHeight": 2340,
            "productName": f"MockDevice-{serial[-3:]}"
        }
    
    @property
    def info(self) -> dict:
        return self._info
    
    def click(self, x: int, y: int):
        """模拟点击"""
        time.sleep(random.uniform(0.05, 0.15))
    
    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.5):
        """模拟滑动"""
        time.sleep(random.uniform(0.1, 0.2))
    
    def __call__(self, **kwargs):
        """模拟选择器调用，返回 MockSelector"""
        return MockSelector()


class MockSelector:
    """模拟 uiautomator2 选择器"""
    
    def exists(self, timeout: float = 5) -> bool:
        return True
    
    @property
    def count(self) -> int:
        return 0
    
    def click(self, timeout: float = 10) -> bool:
        time.sleep(random.uniform(0.05, 0.1))
        return True
    
    def get_text(self) -> str:
        return ""
    
    def set_text(self, text: str):
        time.sleep(random.uniform(0.05, 0.1))
    
    def __getitem__(self, index: int):
        return self
