"""
state_store.py - 状态持久化模块
使用JSON保存采集进度，支持暂停后继续不重复
"""
import os
import json
from typing import Set, Optional, Dict, Any
from datetime import datetime

from core import paths


class StateStore:
    """
    状态存储器
    保存采集进度到JSON文件，支持断点续跑
    """
    
    def __init__(self, device_serial: str, base_output_dir: str = "output"):
        """
        初始化状态存储器
        
        Args:
            device_serial: 设备序列号
            base_output_dir: 输出根目录（如 "output"）
        """
        self.device_serial = device_serial
        self.base_output_dir = base_output_dir
        
        # 使用 paths 模块创建目录: output/{serial}/state
        self.state_dir = paths.state_dir(base_output_dir, device_serial)
        
        # 状态文件路径: output/{serial}/state/state.json（固定文件名，不再用 serial 前缀）
        self.state_file = paths.state_json_path(base_output_dir, device_serial)
        
        # 当前状态
        self.state: Dict[str, Any] = {
            "current_task_index": 0,           # 正在处理第几个任务
            "current_shop_name": "",           # 当前店铺名
            "current_category_index": 0,       # 当前分类序号
            "current_category_name": "",       # 当前分类名
            "scroll_round": 0,                 # 当前分类已滑动次数
            "collected_keys": [],              # 已采集的去重key列表
            "collected_count": 0,              # 已采集条数
            "last_update": "",                 # 最后更新时间
            "status": "idle",                  # 状态: idle/running/paused/completed/error
            "all_categories": [],              # 完整分类列表（恢复时使用）
            "risk_control_hit": False,         # 是否因风控暂停
            "current_poi": "",                 # 当前定位点（恢复时使用）
            "switch_mode": "NORMAL",           # 当前切换模式: NORMAL/BOUNDARY/VERIFYING
            "next_category": "",               # 下一个分类名
            "boundary_divider_y": 0,           # 边界分割线Y坐标
            "boundary_products": [],           # 边界商品key列表
            "verify_screen_count": 0           # 验证模式已滑动屏数
        }
        
        # 去重集合（内存中使用set加速查找）
        self.collected_keys_set: Set[str] = set()
    
    def generate_key(
        self,
        shop_name: str,
        category_name: str,
        drug_name: str,
        price: str
    ) -> str:
        """
        生成去重key（仅使用店铺名+药品名，确保绝对唯一，忽略分类和价格变化）

        Args:
            shop_name: 店铺名（必填）
            category_name: 分类名（不参与去重）
            drug_name: 药品名
            price: 价格（不参与去重）

        Returns:
            去重key字符串: shop_name|drug_name
        """
        # 清理空格和特殊字符，key格式: shop_name|drug_name
        parts = [
            shop_name.strip(),
            drug_name.strip()
        ]
        return "|".join(parts)
    
    def is_collected(self, key: str) -> bool:
        """
        检查key是否已采集
        
        Args:
            key: 去重key
            
        Returns:
            是否已采集
        """
        return key in self.collected_keys_set
    
    def add_collected(self, key: str):
        """
        添加已采集key
        
        Args:
            key: 去重key
        """
        if key not in self.collected_keys_set:
            self.collected_keys_set.add(key)
            self.state["collected_keys"].append(key)
            self.state["collected_count"] = len(self.collected_keys_set)
    
    def save(self):
        """保存状态到文件"""
        self.state["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存状态失败: {e}")
    
    def load(self) -> bool:
        """
        从文件加载状态
        
        Returns:
            是否加载成功
        """
        if not os.path.exists(self.state_file):
            return False
        
        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                loaded_state = json.load(f)
            
            # 合并加载的状态
            self.state.update(loaded_state)
            
            # 重建去重集合
            self.collected_keys_set = set(self.state.get("collected_keys", []))
            
            return True
        except Exception as e:
            print(f"加载状态失败: {e}")
            return False
    
    def reset(self):
        """重置状态"""
        self.state = {
            "current_task_index": 0,
            "current_shop_name": "",
            "current_category_index": 0,
            "current_category_name": "",
            "scroll_round": 0,
            "collected_keys": [],
            "collected_count": 0,
            "last_update": "",
            "status": "idle",
            "all_categories": [],
            "risk_control_hit": False,
            "current_poi": "",
            "switch_mode": "NORMAL",
            "next_category": "",
            "boundary_divider_y": 0,
            "boundary_products": [],
            "verify_screen_count": 0
        }
        self.collected_keys_set.clear()
        
        # 删除状态文件
        if os.path.exists(self.state_file):
            try:
                os.remove(self.state_file)
            except:
                pass
    
    def reset_for_new_shop(self, shop_name: str, poi: str = ""):
        """
        为新店铺重置状态（保留任务进度）
        
        Args:
            shop_name: 新店铺名
            poi: 定位点
        """
        self.state["current_shop_name"] = shop_name
        self.state["current_poi"] = poi
        self.state["current_category_index"] = 0
        self.state["current_category_name"] = ""
        self.state["scroll_round"] = 0
        self.state["collected_keys"] = []
        self.state["collected_count"] = 0
        self.state["all_categories"] = []
        self.state["risk_control_hit"] = False
        self.collected_keys_set.clear()
    
    def mark_risk_control(self, categories: list):
        """
        标记风控触发，保存恢复所需信息
        
        Args:
            categories: 完整分类列表
        """
        self.state["risk_control_hit"] = True
        self.state["all_categories"] = categories
        self.save()
    
    def clear_risk_control(self):
        """清除风控标记（恢复采集时调用）"""
        self.state["risk_control_hit"] = False
        self.save()
    
    # 属性访问器
    @property
    def current_task_index(self) -> int:
        return self.state.get("current_task_index", 0)
    
    @current_task_index.setter
    def current_task_index(self, value: int):
        self.state["current_task_index"] = value
    
    @property
    def current_category_index(self) -> int:
        return self.state.get("current_category_index", 0)
    
    @current_category_index.setter
    def current_category_index(self, value: int):
        self.state["current_category_index"] = value
    
    @property
    def current_category_name(self) -> str:
        return self.state.get("current_category_name", "")
    
    @current_category_name.setter
    def current_category_name(self, value: str):
        self.state["current_category_name"] = value
    
    @property
    def scroll_round(self) -> int:
        return self.state.get("scroll_round", 0)
    
    @scroll_round.setter
    def scroll_round(self, value: int):
        self.state["scroll_round"] = value
    
    @property
    def collected_count(self) -> int:
        return len(self.collected_keys_set)
    
    @property
    def status(self) -> str:
        return self.state.get("status", "idle")
    
    @status.setter
    def status(self, value: str):
        self.state["status"] = value
    
    @property
    def risk_control_hit(self) -> bool:
        return self.state.get("risk_control_hit", False)
    
    @property
    def all_categories(self) -> list:
        return self.state.get("all_categories", [])
    
    @property
    def current_poi(self) -> str:
        return self.state.get("current_poi", "")
    
    @current_poi.setter
    def current_poi(self, value: str):
        self.state["current_poi"] = value
    
    def get_progress_summary(self) -> str:
        """获取进度摘要"""
        return (
            f"任务:{self.current_task_index + 1}, "
            f"分类:{self.current_category_index + 1}, "
            f"采集:{self.collected_count}条"
        )

    def enter_boundary_mode(self, next_category: str, divider_y: int):
        """
        进入边界模式

        Args:
            next_category: 下一个分类名
            divider_y: 边界分割线Y坐标
        """
        self.state["switch_mode"] = "BOUNDARY"
        self.state["next_category"] = next_category
        self.state["boundary_divider_y"] = divider_y
        self.state["boundary_products"] = []
        self.state["verify_screen_count"] = 0

    def enter_verifying_mode(self):
        """进入验证模式"""
        self.state["switch_mode"] = "VERIFYING"
        self.state["verify_screen_count"] = 0

    def exit_boundary_mode(self):
        """退出边界模式，回到正常模式"""
        self.state["switch_mode"] = "NORMAL"
        self.state["next_category"] = ""
        self.state["boundary_divider_y"] = 0
        self.state["boundary_products"] = []
        self.state["verify_screen_count"] = 0
