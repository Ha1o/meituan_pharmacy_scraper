
import os
import time
import threading
import sys
from core.worker import DeviceWorker
from core.device_manager import DeviceManager
from core.logger import DeviceLogger

def log_callback(log_entry):
    """日志回调：输出到控制台"""
    print(log_entry)

def progress_callback(device_serial, current, total, category, count):
    """进度回调"""
    print(f"[进度] 任务 {current+1}/{total} | 分类: {category} | 已采集: {count}条")

def run_test():
    # 1. 获取设备
    dm = DeviceManager()
    devices = dm.refresh_devices()
    
    if not devices:
        print("未找到连接的设备，请检查USB连接")
        return

    serial = devices[0].serial
    print(f"使用设备: {serial}")

    # 2. 初始化Worker
    worker = DeviceWorker(serial, config_path="config.json")
    
    # 设置回调
    worker.set_log_callback(log_callback)
    worker.set_progress_callback(progress_callback)
    
    # 3. 加载任务
    task_file = "auto_test_tasks.xlsx"
    if not os.path.exists(task_file):
        print(f"任务文件不存在: {task_file}")
        return
        
    print(f"加载任务: {task_file}")
    worker.load_tasks(task_file)
    
    # 4. 启动任务
    print("="*50)
    print("开始运行任务...")
    print("="*50)
    worker.start()
    
    # 5. 监控直到结束
    try:
        while worker._thread and worker._thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n停止任务...")
        worker.stop()
        if worker._thread:
            worker._thread.join()
        
    print("="*50)
    print("任务结束")
    print(f"最终状态: {worker.get_status_text()}")
    print(f"采集数量: {worker.collected_count}")
    print("="*50)

if __name__ == "__main__":
    run_test()
