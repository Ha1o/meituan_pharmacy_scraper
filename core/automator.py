"""
automator.py - uiautomator2 设备操作封装
提供设备连接、App启动/停止、滑动等基础操作
"""
import time
from typing import Optional, Tuple
import uiautomator2 as u2

from core.logger import DeviceLogger


class DeviceAutomator:
    """
    设备自动化操作器
    封装 uiautomator2 的基础操作
    """
    
    def __init__(self, device_serial: str, logger: DeviceLogger, config: dict):
        """
        初始化设备自动化器
        
        Args:
            device_serial: 设备序列号
            logger: 设备日志器
            config: 配置字典
        """
        self.device_serial = device_serial
        self.logger = logger
        self.config = config
        self.device: Optional[u2.Device] = None
        
        # 加载配置
        self.app_config = config.get("app", {})
        self.scroll_config = config.get("scroll", {})
        self.timeout_config = config.get("timeouts", {})
    
    def connect(self) -> bool:
        """
        连接设备
        
        Returns:
            是否连接成功
        """
        try:
            self.logger.step("连接设备", self.device_serial)
            self.device = u2.connect(self.device_serial)
            
            # 验证连接
            info = self.device.info
            self.logger.info(f"设备已连接: {info.get('productName', 'Unknown')}")
            return True
        except Exception as e:
            self.logger.exception("连接设备", e)
            return False
    
    def disconnect(self):
        """断开设备连接"""
        self.device = None
        self.logger.info("设备已断开连接")
    
    def is_connected(self) -> bool:
        """检查设备是否已连接"""
        if not self.device:
            return False
        try:
            self.device.info
            return True
        except:
            return False
    
    def start_app(self) -> bool:
        """
        启动目标App
        
        Returns:
            是否启动成功
        """
        if not self.device:
            self.logger.error("设备未连接，无法启动App")
            return False
        
        package_name = self.app_config.get("package_name", "com.sankuai.meituan")
        activity = self.app_config.get("main_activity", "")
        wait_seconds = self.app_config.get("start_wait_seconds", 5)
        
        try:
            self.logger.step("启动App", package_name)
            
            if activity:
                self.device.app_start(package_name, activity)
            else:
                self.device.app_start(package_name)
            
            # 等待App启动
            time.sleep(wait_seconds)
            
            # 验证App是否在前台
            current_app = self.device.app_current()
            if current_app.get("package") == package_name:
                self.logger.info(f"App已启动: {package_name}")
                return True
            else:
                self.logger.warning(f"App可能未正确启动，当前前台App: {current_app}")
                return True  # 仍返回True，让后续流程判断
                
        except Exception as e:
            self.logger.exception("启动App", e)
            return False
    
    def stop_app(self) -> bool:
        """
        停止目标App
        
        Returns:
            是否成功
        """
        if not self.device:
            return False
        
        package_name = self.app_config.get("package_name", "com.sankuai.meituan")
        
        try:
            self.logger.step("停止App", package_name)
            self.device.app_stop(package_name)
            return True
        except Exception as e:
            self.logger.exception("停止App", e)
            return False
    
    def get_screen_size(self) -> Tuple[int, int]:
        """
        获取屏幕尺寸
        
        Returns:
            (width, height)
        """
        if not self.device:
            return (1080, 1920)
        
        try:
            info = self.device.info
            return (info['displayWidth'], info['displayHeight'])
        except:
            return (1080, 1920)
    
    def swipe_up(self, duration: float = 0.5):
        """
        向上滑动（用于滚动列表）
        
        Args:
            duration: 滑动持续时间(秒)
        """
        if not self.device:
            return
        
        width, height = self.get_screen_size()
        
        # 从屏幕中下部向上滑动
        start_x = width // 2
        start_y = int(height * 0.75)
        end_y = int(height * 0.35)
        
        try:
            self.device.swipe(start_x, start_y, start_x, end_y, duration=duration)
            self.logger.debug(f"向上滑动: ({start_x}, {start_y}) -> ({start_x}, {end_y})")
        except Exception as e:
            self.logger.warning(f"滑动失败: {e}")
    
    def swipe_down(self, duration: float = 0.5):
        """
        向下滑动
        
        Args:
            duration: 滑动持续时间(秒)
        """
        if not self.device:
            return
        
        width, height = self.get_screen_size()
        
        start_x = width // 2
        start_y = int(height * 0.35)
        end_y = int(height * 0.75)
        
        try:
            self.device.swipe(start_x, start_y, start_x, end_y, duration=duration)
            self.logger.debug(f"向下滑动: ({start_x}, {start_y}) -> ({start_x}, {end_y})")
        except Exception as e:
            self.logger.warning(f"滑动失败: {e}")
    
    def swipe_left_in_region(
        self, 
        x_start_ratio: float = 0.8,
        x_end_ratio: float = 0.2,
        y_ratio: float = 0.5,
        duration: float = 0.3
    ):
        """
        在指定区域向左滑动
        
        Args:
            x_start_ratio: 起始X位置比例
            x_end_ratio: 结束X位置比例
            y_ratio: Y位置比例
            duration: 持续时间
        """
        if not self.device:
            return
        
        width, height = self.get_screen_size()
        
        start_x = int(width * x_start_ratio)
        end_x = int(width * x_end_ratio)
        y = int(height * y_ratio)
        
        try:
            self.device.swipe(start_x, y, end_x, y, duration=duration)
        except Exception as e:
            self.logger.warning(f"左滑失败: {e}")
    
    def tap(self, x: int, y: int):
        """
        点击坐标
        
        Args:
            x: X坐标
            y: Y坐标
        """
        if not self.device:
            return
        
        try:
            self.device.click(x, y)
            self.logger.debug(f"点击坐标: ({x}, {y})")
        except Exception as e:
            self.logger.warning(f"点击失败: {e}")
    
    def press_back(self):
        """按返回键"""
        if not self.device:
            return
        
        try:
            self.device.press("back")
            self.logger.debug("按下返回键")
        except Exception as e:
            self.logger.warning(f"返回键失败: {e}")
    
    def press_home(self):
        """按Home键"""
        if not self.device:
            return
        
        try:
            self.device.press("home")
            self.logger.debug("按下Home键")
        except Exception as e:
            self.logger.warning(f"Home键失败: {e}")
    
    def wait(self, seconds: float):
        """
        等待指定时间
        
        Args:
            seconds: 等待秒数
        """
        time.sleep(seconds)
    
    def screenshot(self, filepath: str) -> bool:
        """
        截图保存
        
        Args:
            filepath: 保存路径
            
        Returns:
            是否成功
        """
        if not self.device:
            return False
        
        try:
            self.device.screenshot(filepath)
            return True
        except Exception as e:
            self.logger.warning(f"截图保存失败: {e}")
            return False
    
    def get_page_source(self) -> str:
        """
        获取当前页面XML源码（用于调试）
        
        Returns:
            页面XML
        """
        if not self.device:
            return ""
        
        try:
            return self.device.dump_hierarchy()
        except Exception as e:
            self.logger.warning(f"获取页面源码失败: {e}")
            return ""
    
    def input_text_via_adb(self, text: str):
        """
        通过ADB命令输入文本（备用方案）
        
        Args:
            text: 要输入的文本
        """
        if not self.device:
            return
        
        try:
            # 使用adb shell input text，需要处理中文
            # uiautomator2的set_text更可靠，这里作为备用
            self.device.shell(f'input text "{text}"')
        except Exception as e:
            self.logger.warning(f"ADB输入失败: {e}")
    
    def clear_app_cache(self) -> bool:
        """
        清除App缓存
        
        Returns:
            是否成功
        """
        if not self.device:
            return False
        
        package_name = self.app_config.get("package_name", "com.sankuai.meituan")
        
        try:
            self.device.app_clear(package_name)
            self.logger.info(f"已清除App缓存: {package_name}")
            return True
        except Exception as e:
            self.logger.warning(f"清除缓存失败: {e}")
            return False
    
    def is_page_loaded(self, min_chinese_chars: int = 10) -> bool:
        """
        检测页面是否正常加载（非白屏）
        通过统计可见文本元素的中文字符数来判断
        
        Args:
            min_chinese_chars: 最少中文字符数，低于此值视为白屏
            
        Returns:
            True表示页面正常加载，False表示白屏或加载不全
        """
        if not self.device:
            return False
        
        try:
            # 获取所有可见的 TextView 元素的文本
            text_views = self.device(className="android.widget.TextView")
            
            chinese_count = 0
            if text_views.exists(timeout=2):
                for i in range(text_views.count):
                    try:
                        text = text_views[i].get_text()
                        if text:
                            # 统计这个元素中的中文字符
                            chinese_count += sum(1 for char in text if '\u4e00' <= char <= '\u9fff')
                    except:
                        continue
            
            if chinese_count < min_chinese_chars:
                self.logger.warning(f"检测到白屏/加载不全: 中文字符数={chinese_count} (阈值={min_chinese_chars})")
                return False
            
            self.logger.debug(f"页面正常加载: 中文字符数={chinese_count}")
            return True
            
        except Exception as e:
            self.logger.warning(f"页面检测失败: {e}")
            return False
    
    def wait_for_page_load(self, max_retries: int = 3, wait_seconds: float = 2, min_chinese_chars: int = 10) -> bool:
        """
        等待页面加载完成，如果检测到白屏则自动返回重试
        
        Args:
            max_retries: 最大重试次数
            wait_seconds: 每次检测间隔
            min_chinese_chars: 最少中文字符数
            
        Returns:
            True表示页面最终加载成功，False表示重试后仍失败
        """
        for attempt in range(max_retries):
            time.sleep(wait_seconds)
            
            # 优先检查是否有错误页面并处理
            if self.handle_error_screens():
                # 如果点击了重新加载，等待更长时间让页面刷新
                time.sleep(3)
                # 重新检查加载状态
                if self.is_page_loaded(min_chinese_chars):
                    return True
                continue
            
            if self.is_page_loaded(min_chinese_chars):
                return True
            
            # 检测到白屏，按返回键重试
            self.logger.warning(f"白屏重试 ({attempt + 1}/{max_retries})")
            self.press_back()
            time.sleep(1)
        
        return False

    def handle_error_screens(self) -> bool:
        """
        检测并处理错误页面（如：重新加载页面）
        
        Returns:
            True表示处理了错误页面，False表示未发现错误页面
        """
        if not self.device:
            return False
            
        try:
            # 检测"重新加载"按钮
            reload_btn = self.device(text="重新加载")
            if reload_btn.exists(timeout=2):
                self.logger.warning("检测到错误页面，点击'重新加载'")
                reload_btn.click()
                time.sleep(3)
                return True
                
            # 检测"网络悄悄跑到外星球去了"
            if self.device(textContains="外星球").exists(timeout=1):
                self.logger.warning("检测到网络错误页面，尝试点击屏幕中心重试")
                width, height = self.get_screen_size()
                self.device.click(width // 2, height // 2)
                time.sleep(3)
                return True
                
            return False
        except Exception as e:
            self.logger.debug(f"检查错误页面失败: {e}")
            return False

