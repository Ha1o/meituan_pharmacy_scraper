"""
selectors.py - 控件选择器工具
从 config.json 加载选择器，提供通用查找、点击、输入方法
失败时截图并提示检查配置
"""
import json
import os
import time
from typing import Optional, Any, Union
import uiautomator2 as u2

from core.logger import DeviceLogger


class SelectorHelper:
    """
    控件选择器辅助类
    封装 uiautomator2 的查找和操作方法
    支持多候选选择器和重试机制
    """
    
    def __init__(self, device: u2.Device, logger: DeviceLogger, config_path: str = "config.json"):
        """
        初始化选择器辅助器
        
        Args:
            device: uiautomator2 设备对象
            logger: 设备日志器
            config_path: 配置文件路径
        """
        self.device = device
        self.logger = logger
        self.config = self._load_config(config_path)
        
        # 从配置加载参数
        self.default_timeout = self.config.get("timeouts", {}).get("default_timeout", 10)
        self.max_retries = self.config.get("retry", {}).get("max_retries", 3)
        self.retry_delay = self.config.get("retry", {}).get("retry_delay", 2)
        self.selectors = self.config.get("selectors", {})
    
    def _load_config(self, config_path: str) -> dict:
        """加载配置文件"""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"加载配置文件失败: {e}")
            return {}
    
    def _build_selector(self, selector_def: dict) -> u2.UiObject:
        """
        根据选择器定义构建 uiautomator2 选择器
        
        Args:
            selector_def: 选择器定义字典，如 {"text": "外卖"} 或 {"resourceId": "xxx"}
        """
        return self.device(**selector_def)
    
    def _take_screenshot(self, step_name: str) -> str:
        """截图并返回路径"""
        filepath = self.logger.screenshot(step_name)
        try:
            self.device.screenshot(filepath)
        except Exception as e:
            self.logger.error(f"截图失败: {e}")
        return filepath
    
    def find_one(
        self, 
        selector_key: str, 
        timeout: Optional[float] = None,
        custom_selectors: Optional[list[dict]] = None
    ) -> Optional[u2.UiObject]:
        """
        查找单个控件，尝试多个候选选择器
        
        Args:
            selector_key: 选择器键名(对应config中的key)
            timeout: 超时时间(秒)
            custom_selectors: 自定义选择器列表(覆盖config)
            
        Returns:
            找到的控件对象，未找到返回None
        """
        timeout = timeout or self.default_timeout
        selectors = custom_selectors or self.selectors.get(selector_key, [])
        
        if not selectors:
            self.logger.warning(f"未找到选择器配置: {selector_key}")
            return None
        
        start_time = time.time()
        
        # 在超时时间内循环尝试所有选择器
        while time.time() - start_time < timeout:
            for selector_def in selectors:
                try:
                    element = self._build_selector(selector_def)
                    if element.exists(timeout=0.5):
                        return element
                except Exception as e:
                    self.logger.debug(f"选择器 {selector_def} 查找失败: {e}")
            time.sleep(0.5)
        
        return None
    
    def click_one(
        self, 
        selector_key: str, 
        timeout: Optional[float] = None,
        step_name: Optional[str] = None
    ) -> bool:
        """
        点击控件，带重试和失败截图
        
        Args:
            selector_key: 选择器键名
            timeout: 超时时间
            step_name: 步骤名称(用于日志和截图)
            
        Returns:
            是否成功点击
        """
        step_name = step_name or selector_key
        
        for attempt in range(1, self.max_retries + 1):
            element = self.find_one(selector_key, timeout)
            
            if element:
                try:
                    element.click()
                    self.logger.step(step_name, "点击成功")
                    return True
                except Exception as e:
                    self.logger.warning(f"点击失败: {e}")
            
            if attempt < self.max_retries:
                self.logger.retry(step_name, attempt, self.max_retries, "未找到控件或点击失败")
                time.sleep(self.retry_delay)
        
        # 最终失败，截图并记录
        screenshot_path = self._take_screenshot(step_name)
        self.logger.exception(step_name, Exception("控件查找/点击失败"), screenshot_path)
        return False
    
    def set_text(
        self, 
        selector_key: str, 
        text: str,
        timeout: Optional[float] = None,
        step_name: Optional[str] = None,
        clear_first: bool = True
    ) -> bool:
        """
        设置控件文本
        
        Args:
            selector_key: 选择器键名
            text: 要输入的文本
            timeout: 超时时间
            step_name: 步骤名称
            clear_first: 是否先清空
            
        Returns:
            是否成功
        """
        step_name = step_name or f"{selector_key}_输入"
        
        for attempt in range(1, self.max_retries + 1):
            element = self.find_one(selector_key, timeout)
            
            if element:
                try:
                    if clear_first:
                        element.clear_text()
                    element.set_text(text)
                    self.logger.step(step_name, f"输入: {text}")
                    return True
                except Exception as e:
                    self.logger.warning(f"输入失败: {e}")
            
            if attempt < self.max_retries:
                self.logger.retry(step_name, attempt, self.max_retries, "未找到输入框或输入失败")
                time.sleep(self.retry_delay)
        
        screenshot_path = self._take_screenshot(step_name)
        self.logger.exception(step_name, Exception("文本输入失败"), screenshot_path)
        return False
    
    def get_text(
        self, 
        selector_key: str,
        timeout: Optional[float] = None,
        default: str = ""
    ) -> str:
        """
        获取控件文本
        
        Args:
            selector_key: 选择器键名
            timeout: 超时时间
            default: 默认值(找不到时返回)
            
        Returns:
            控件文本
        """
        element = self.find_one(selector_key, timeout)
        
        if element:
            try:
                text = element.get_text()
                return text if text else default
            except Exception as e:
                self.logger.debug(f"获取文本失败: {e}")
        
        return default
    
    def wait_exists(
        self,
        selector_key: str,
        timeout: Optional[float] = None
    ) -> bool:
        """
        等待控件出现
        
        Args:
            selector_key: 选择器键名
            timeout: 超时时间
            
        Returns:
            是否出现
        """
        element = self.find_one(selector_key, timeout)
        return element is not None
    
    def find_all(
        self,
        selector_key: str,
        timeout: Optional[float] = None
    ) -> list:
        """
        查找所有匹配的控件
        
        Args:
            selector_key: 选择器键名
            timeout: 超时时间
            
        Returns:
            控件列表
        """
        timeout = timeout or self.default_timeout
        selectors = self.selectors.get(selector_key, [])
        
        if not selectors:
            return []
        
        # 使用第一个有效的选择器
        for selector_def in selectors:
            try:
                elements = self._build_selector(selector_def)
                if elements.exists(timeout=1):
                    # 获取所有匹配元素
                    count = elements.count
                    if count > 0:
                        return [elements[i] for i in range(count)]
            except Exception as e:
                self.logger.debug(f"查找多个元素失败: {e}")
        
        return []
    
    def click_by_text(self, text: str, timeout: Optional[float] = None) -> bool:
        """
        通过文本点击控件
        
        Args:
            text: 控件文本
            timeout: 超时时间
            
        Returns:
            是否成功
        """
        timeout = timeout or self.default_timeout
        
        for attempt in range(1, self.max_retries + 1):
            try:
                element = self.device(text=text)
                if element.exists(timeout=timeout):
                    element.click()
                    self.logger.step(f"点击文本[{text}]", "成功")
                    return True
            except Exception as e:
                self.logger.debug(f"点击文本失败: {e}")
            
            if attempt < self.max_retries:
                self.logger.retry(f"点击文本[{text}]", attempt, self.max_retries)
                time.sleep(self.retry_delay)
        
        screenshot_path = self._take_screenshot(f"点击_{text}")
        self.logger.exception(f"点击文本[{text}]", Exception("未找到文本"), screenshot_path)
        return False
    
    def click_by_text_contains(self, text: str, timeout: Optional[float] = None) -> bool:
        """
        通过包含的文本点击控件
        
        Args:
            text: 包含的文本
            timeout: 超时时间
            
        Returns:
            是否成功
        """
        timeout = timeout or self.default_timeout
        
        for attempt in range(1, self.max_retries + 1):
            try:
                element = self.device(textContains=text)
                if element.exists(timeout=timeout):
                    element.click()
                    self.logger.step(f"点击包含[{text}]", "成功")
                    return True
            except Exception as e:
                self.logger.debug(f"点击失败: {e}")
            
            if attempt < self.max_retries:
                self.logger.retry(f"点击包含[{text}]", attempt, self.max_retries)
                time.sleep(self.retry_delay)
        
        screenshot_path = self._take_screenshot(f"点击包含_{text}")
        self.logger.exception(f"点击包含[{text}]", Exception("未找到包含文本的控件"), screenshot_path)
        return False
