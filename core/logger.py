"""
logger.py - 设备独立日志模块
每台设备单独日志文件，记录每一步、异常、重试、截图路径
"""
import os
import logging
from datetime import datetime
from typing import Optional

from core import paths


class DeviceLogger:
    """
    设备专属日志记录器
    每台设备创建独立的日志文件和处理器
    """
    
    def __init__(self, device_serial: str, base_output_dir: str = "output"):
        """
        初始化设备日志器
        
        Args:
            device_serial: 设备序列号
            base_output_dir: 输出根目录（如 "output"）
        """
        self.device_serial = device_serial
        self.base_output_dir = base_output_dir
        
        # 使用 paths 模块创建目录: output/{serial}/logs, output/{serial}/screenshots
        self.log_dir = paths.logs_dir(base_output_dir, device_serial)
        self.screenshot_dir = paths.screenshots_dir(base_output_dir, device_serial)
        
        # 配置日志器
        self.logger = logging.getLogger(f"device_{device_serial}")
        self.logger.setLevel(logging.DEBUG)
        self.logger.handlers.clear()  # 清除已有处理器
        
        # 文件处理器: output/{serial}/logs/{serial}.log
        log_file = os.path.join(self.log_dir, f"{device_serial}.log")
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        
        # 日志格式
        formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)
        
        # 内存日志缓存（用于UI显示）
        self.log_buffer: list[str] = []
        self.max_buffer_size = 1000
        
        # 日志回调（用于通知UI更新）
        self.on_log_callback = None
    
    def set_log_callback(self, callback):
        """设置日志回调函数，用于实时更新UI"""
        self.on_log_callback = callback
    
    def _add_to_buffer(self, level: str, message: str):
        """添加日志到缓存"""
        timestamp = datetime.now().strftime('%H:%M:%S')
        log_entry = f"[{timestamp}][{level}] {message}"
        self.log_buffer.append(log_entry)
        
        # 限制缓存大小
        if len(self.log_buffer) > self.max_buffer_size:
            self.log_buffer = self.log_buffer[-self.max_buffer_size:]
        
        # 触发回调
        if self.on_log_callback:
            self.on_log_callback(log_entry)
    
    def info(self, message: str):
        """记录信息日志"""
        self.logger.info(message)
        self._add_to_buffer("信息", message)
    
    def warning(self, message: str):
        """记录警告日志"""
        self.logger.warning(message)
        self._add_to_buffer("警告", message)
    
    def error(self, message: str):
        """记录错误日志"""
        self.logger.error(message)
        self._add_to_buffer("错误", message)
    
    def debug(self, message: str):
        """记录调试日志"""
        self.logger.debug(message)
        self._add_to_buffer("调试", message)
    
    def step(self, step_name: str, detail: str = ""):
        """记录步骤日志"""
        msg = f"【步骤】{step_name}"
        if detail:
            msg += f" - {detail}"
        self.info(msg)
    
    def retry(self, step_name: str, attempt: int, max_attempts: int, reason: str = ""):
        """记录重试日志"""
        msg = f"【重试】{step_name} ({attempt}/{max_attempts})"
        if reason:
            msg += f" - 原因: {reason}"
        self.warning(msg)
    
    def screenshot(self, step_name: str) -> str:
        """
        生成截图路径并记录日志
        
        Args:
            step_name: 步骤名称
            
        Returns:
            截图文件路径
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        # 清理步骤名中的非法字符
        safe_step = "".join(c if c.isalnum() or c in ('_', '-') else '_' for c in step_name)
        filename = f"{timestamp}_{safe_step}.png"
        filepath = os.path.join(self.screenshot_dir, filename)
        
        self.info(f"【截图】保存到: {filepath}")
        return filepath
    
    def exception(self, step_name: str, error: Exception, screenshot_path: Optional[str] = None):
        """记录异常日志"""
        msg = f"【异常】{step_name} - {type(error).__name__}: {str(error)}"
        if screenshot_path:
            msg += f"\n    截图已保存: {screenshot_path}"
        
        # 仅在特定错误（如选择器查找失败）时提示检查配置，避免误导
        if "Selector" in str(type(error).__name__) or "UiObject" in str(type(error).__name__):
            msg += "\n    请检查 config.json 中的选择器配置是否正确"
            
        self.error(msg)
    
    def get_logs(self) -> list[str]:
        """获取日志缓存"""
        return self.log_buffer.copy()
    
    def clear_buffer(self):
        """清空日志缓存"""
        self.log_buffer.clear()
