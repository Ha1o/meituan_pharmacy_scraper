"""
paths.py - 路径管理模块
集中管理输出目录结构，确保多设备隔离：output/{serial}/...
"""
import os
import re
from pathlib import Path


def sanitize_filename(name: str) -> str:
    """
    清理文件名中的非法字符
    
    Args:
        name: 原始文件名
        
    Returns:
        清理后的文件名
    """
    # Windows文件名非法字符
    illegal_chars = r'[<>:"/\\|?*]'
    sanitized = re.sub(illegal_chars, '_', name)
    sanitized = sanitized.strip('. ')
    if len(sanitized) > 100:
        sanitized = sanitized[:100]
    return sanitized if sanitized else "unknown"


def ensure_dir(path: str) -> str:
    """
    确保目录存在，不存在则创建
    
    Args:
        path: 目录路径
        
    Returns:
        目录路径
    """
    Path(path).mkdir(parents=True, exist_ok=True)
    return path


def device_root(base_output_dir: str, serial: str) -> str:
    """
    获取设备根目录：output/{serial}
    
    Args:
        base_output_dir: 输出根目录（如 "output"）
        serial: 设备序列号
        
    Returns:
        设备根目录路径
    """
    path = os.path.join(base_output_dir, serial)
    return ensure_dir(path)


def results_dir(base_output_dir: str, serial: str) -> str:
    """
    获取结果目录：output/{serial}/results
    
    Args:
        base_output_dir: 输出根目录
        serial: 设备序列号
        
    Returns:
        结果目录路径
    """
    path = os.path.join(base_output_dir, serial, "results")
    return ensure_dir(path)


def state_dir(base_output_dir: str, serial: str) -> str:
    """
    获取状态目录：output/{serial}/state
    
    Args:
        base_output_dir: 输出根目录
        serial: 设备序列号
        
    Returns:
        状态目录路径
    """
    path = os.path.join(base_output_dir, serial, "state")
    return ensure_dir(path)


def logs_dir(base_output_dir: str, serial: str) -> str:
    """
    获取日志目录：output/{serial}/logs
    
    Args:
        base_output_dir: 输出根目录
        serial: 设备序列号
        
    Returns:
        日志目录路径
    """
    path = os.path.join(base_output_dir, serial, "logs")
    return ensure_dir(path)


def screenshots_dir(base_output_dir: str, serial: str) -> str:
    """
    获取截图目录：output/{serial}/screenshots
    
    Args:
        base_output_dir: 输出根目录
        serial: 设备序列号
        
    Returns:
        截图目录路径
    """
    path = os.path.join(base_output_dir, serial, "screenshots")
    return ensure_dir(path)


def shop_xlsx_path(base_output_dir: str, serial: str, shop_name: str, task_index: int) -> str:
    """
    获取店铺Excel文件路径：output/{serial}/results/{shop_name}_{task_index}.xlsx
    
    Args:
        base_output_dir: 输出根目录
        serial: 设备序列号
        shop_name: 店铺名
        task_index: 任务序号（从1开始）
        
    Returns:
        Excel文件路径
    """
    safe_name = sanitize_filename(shop_name)
    filename = f"{safe_name}_{task_index}.xlsx"
    return os.path.join(results_dir(base_output_dir, serial), filename)


def state_json_path(base_output_dir: str, serial: str) -> str:
    """
    获取状态文件路径：output/{serial}/state/state.json
    
    Args:
        base_output_dir: 输出根目录
        serial: 设备序列号
        
    Returns:
        状态文件路径
    """
    return os.path.join(state_dir(base_output_dir, serial), "state.json")
