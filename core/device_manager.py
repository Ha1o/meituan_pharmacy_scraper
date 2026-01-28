"""
device_manager.py - 设备管理模块
使用 adbutils 获取设备列表，管理设备状态
"""
from typing import List, Dict, Optional
from dataclasses import dataclass
from enum import Enum

# 使用 adbutils 替代 subprocess 调用 adb（更可靠，不依赖PATH）
try:
    import adbutils
    HAS_ADBUTILS = True
except ImportError:
    HAS_ADBUTILS = False


class DeviceStatus(Enum):
    """设备状态枚举"""
    OFFLINE = "离线"
    ONLINE = "在线"
    UNAUTHORIZED = "未授权"
    UNKNOWN = "未知"


@dataclass
class DeviceInfo:
    """设备信息"""
    serial: str                          # 设备序列号
    status: DeviceStatus = DeviceStatus.UNKNOWN  # 设备状态
    model: str = ""                      # 设备型号
    task_status: str = "空闲"             # 任务状态
    task_file: str = ""                  # 任务文件路径
    progress: str = "0/0"                # 进度
    
    def to_dict(self) -> Dict:
        return {
            "serial": self.serial,
            "status": self.status.value,
            "model": self.model,
            "task_status": self.task_status,
            "task_file": self.task_file,
            "progress": self.progress
        }


class DeviceManager:
    """
    设备管理器
    管理多台Android设备的连接和状态
    """
    
    def __init__(self):
        """初始化设备管理器"""
        self.devices: Dict[str, DeviceInfo] = {}
    
    def refresh_devices(self) -> List[DeviceInfo]:
        """
        刷新设备列表
        使用 adbutils 获取当前连接的设备
        
        Returns:
            设备信息列表
        """
        if not HAS_ADBUTILS:
            print("adbutils 未安装，请运行: pip install adbutils")
            return list(self.devices.values())
        
        try:
            # 获取 adb 客户端
            adb = adbutils.AdbClient(host="127.0.0.1", port=5037)
            device_list = adb.device_list()
            
            # 记录当前在线的设备
            current_serials = set()
            
            for d in device_list:
                serial = d.serial
                current_serials.add(serial)
                
                # 确定设备状态
                try:
                    state = d.get_state()
                    if state == "device":
                        status = DeviceStatus.ONLINE
                    elif state == "offline":
                        status = DeviceStatus.OFFLINE
                    elif state == "unauthorized":
                        status = DeviceStatus.UNAUTHORIZED
                    else:
                        status = DeviceStatus.UNKNOWN
                except:
                    status = DeviceStatus.ONLINE  # 默认在线
                
                # 获取型号
                model = ""
                try:
                    model = d.prop.model or ""
                except:
                    pass
                
                # 更新或添加设备
                if serial in self.devices:
                    self.devices[serial].status = status
                    if model:
                        self.devices[serial].model = model
                else:
                    self.devices[serial] = DeviceInfo(
                        serial=serial,
                        status=status,
                        model=model
                    )
            
            # 标记离线设备
            for serial in list(self.devices.keys()):
                if serial not in current_serials:
                    self.devices[serial].status = DeviceStatus.OFFLINE
            
            return list(self.devices.values())
            
        except Exception as e:
            print(f"刷新设备列表失败: {e}")
            return list(self.devices.values())
    
    def get_device(self, serial: str) -> Optional[DeviceInfo]:
        """
        获取指定设备信息
        
        Args:
            serial: 设备序列号
            
        Returns:
            设备信息，不存在返回None
        """
        return self.devices.get(serial)
    
    def get_online_devices(self) -> List[DeviceInfo]:
        """获取所有在线设备"""
        return [d for d in self.devices.values() if d.status == DeviceStatus.ONLINE]
    
    def update_device_task_status(
        self, 
        serial: str, 
        task_status: str, 
        progress: str = "",
        task_file: str = ""
    ):
        """
        更新设备任务状态
        
        Args:
            serial: 设备序列号
            task_status: 任务状态
            progress: 进度
            task_file: 任务文件
        """
        if serial in self.devices:
            self.devices[serial].task_status = task_status
            if progress:
                self.devices[serial].progress = progress
            if task_file:
                self.devices[serial].task_file = task_file
    
    def get_device_count(self) -> int:
        """获取设备数量"""
        return len(self.devices)
    
    def get_online_count(self) -> int:
        """获取在线设备数量"""
        return len(self.get_online_devices())
    
    def clear(self):
        """清空设备列表"""
        self.devices.clear()
