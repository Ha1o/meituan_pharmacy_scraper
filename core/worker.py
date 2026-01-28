"""
worker.py - 单设备任务执行器
实现完整的美团药房采集业务流程
支持 Start/Pause/Resume/Stop 控制
"""
import threading
import time
import json
from typing import Optional, Callable, List
from enum import Enum

from core.logger import DeviceLogger
from core.automator import DeviceAutomator
from core.selectors import SelectorHelper
from core.task_loader import TaskLoader, Task
from core.state_store import StateStore
from core.exporter import ExcelExporter, create_drug_record, DrugRecord


class WorkerStatus(Enum):
    """Worker状态枚举"""
    IDLE = "空闲"
    RUNNING = "运行中"
    PAUSED = "已暂停"
    STOPPING = "正在停止"
    STOPPED = "已停止"
    COMPLETED = "已完成"
    ERROR = "错误"


class DeviceWorker:
    """
    设备工作器
    每台设备对应一个Worker，独立线程执行任务
    """
    
    def __init__(
        self, 
        device_serial: str, 
        output_dir: str = "output",
        config_path: str = "config.json"
    ):
        self.device_serial = device_serial
        self.output_dir = output_dir
        self.config_path = config_path
        
        # 加载配置
        self.config = self._load_config()
        
        # 初始化组件
        self.logger = DeviceLogger(device_serial, output_dir)
        self.automator = DeviceAutomator(device_serial, self.logger, self.config)
        self.selector: Optional[SelectorHelper] = None
        self.task_loader = TaskLoader(self.logger)
        self.state_store = StateStore(device_serial, output_dir)
        self.exporter = ExcelExporter(output_dir, self.logger)
        
        # 线程控制
        self._thread: Optional[threading.Thread] = None
        self._pause_event = threading.Event()
        self._stop_event = threading.Event()
        self._pause_event.set()
        
        # 状态
        self._status = WorkerStatus.IDLE
        self._error_message = ""
        
        # 进度回调
        self.on_progress_callback: Optional[Callable] = None
        self.on_status_change_callback: Optional[Callable] = None
        
        # 当前进度
        self.current_task_index = 0
        self.total_tasks = 0
        self.current_category = ""
        self.collected_count = 0
    
    def _load_config(self) -> dict:
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"加载配置失败: {e}")
            return {}
    
    @property
    def status(self) -> WorkerStatus:
        return self._status
    
    @status.setter
    def status(self, value: WorkerStatus):
        self._status = value
        if self.on_status_change_callback:
            self.on_status_change_callback(self.device_serial, value)
    
    def set_log_callback(self, callback: Callable):
        self.logger.set_log_callback(callback)
    
    def set_progress_callback(self, callback: Callable):
        self.on_progress_callback = callback
    
    def set_status_change_callback(self, callback: Callable):
        self.on_status_change_callback = callback
    
    def _update_progress(self):
        if self.on_progress_callback:
            self.on_progress_callback(
                self.device_serial,
                self.current_task_index,
                self.total_tasks,
                self.current_category,
                self.collected_count
            )
    
    def load_tasks(self, task_file: str) -> bool:
        if self.task_loader.load(task_file):
            self.total_tasks = self.task_loader.count()
            return True
        return False
    
    def start(self):
        if self._thread and self._thread.is_alive():
            self.logger.warning("任务已在执行中")
            return
        
        self._stop_event.clear()
        self._pause_event.set()
        self.status = WorkerStatus.RUNNING
        
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.logger.info("任务已启动")
    
    def pause(self):
        if self.status == WorkerStatus.RUNNING:
            self._pause_event.clear()
            self.status = WorkerStatus.PAUSED
            self.state_store.save()
            self.logger.info("任务已暂停")
    
    def resume(self):
        if self.status == WorkerStatus.PAUSED:
            self._pause_event.set()
            self.status = WorkerStatus.RUNNING
            self.logger.info("任务已继续")
    
    def stop(self):
        self._stop_event.set()
        self._pause_event.set()
        self.status = WorkerStatus.STOPPING
        self.state_store.save()
        self.logger.info("正在停止任务...")
    
    def _check_control(self) -> bool:
        if self._stop_event.is_set():
            return False
        while not self._pause_event.is_set():
            if self._stop_event.is_set():
                return False
            time.sleep(0.1)
        return True
    
    def _run(self):
        try:
            if not self.automator.connect():
                self.status = WorkerStatus.ERROR
                self._error_message = "设备连接失败"
                return
            
            self.selector = SelectorHelper(
                self.automator.device,
                self.logger,
                self.config_path
            )
            
            # 加载状态
            resume_from_risk_control = False
            if self.state_store.load():
                self.current_task_index = self.state_store.current_task_index
                
                # 检查是否是风控恢复模式
                if self.state_store.risk_control_hit:
                    resume_from_risk_control = True
                    self.logger.info(f"检测到风控恢复模式: 任务{self.current_task_index + 1}, 分类: {self.state_store.current_category_name}")
                else:
                    self.logger.info(f"从上次进度继续: 任务{self.current_task_index + 1}")
            
            tasks = self.task_loader.get_tasks()
            
            # 风控恢复模式：重新进入店铺并继续采集
            if resume_from_risk_control and self.current_task_index < len(tasks):
                task = tasks[self.current_task_index]
                self.logger.step("风控恢复: 重新进入店铺", task.shop_name)
                
                if self._resume_to_shop(task):
                    # 清除风控标记并继续采集
                    self.state_store.clear_risk_control()
                    self._process_shop(task, resume_mode=True)
                else:
                    self.logger.error("恢复进入店铺失败")
                
                # 恢复完成后继续下一个任务
                self.current_task_index += 1
            
            for i in range(self.current_task_index, len(tasks)):
                if not self._check_control():
                    break
                
                task = tasks[i]
                self.current_task_index = i
                self.state_store.current_task_index = i
                self.state_store.save()
                self._update_progress()
                
                self.logger.step(f"开始任务 {i + 1}/{len(tasks)}", str(task))
                
                success = self._process_shop(task)
                
                if not success:
                    self.logger.warning(f"任务 {i + 1} 执行失败，继续下一个")
            
            if not self._stop_event.is_set():
                self.status = WorkerStatus.COMPLETED
                self.logger.info("所有任务执行完成")
            else:
                self.status = WorkerStatus.STOPPED
                self.logger.info("任务已停止")
                
        except Exception as e:
            self.status = WorkerStatus.ERROR
            self._error_message = str(e)
            self.logger.exception("任务执行", e)
        finally:
            self.automator.disconnect()
    
    def _process_shop(self, task: Task, resume_mode: bool = False) -> bool:
        """
        处理单个店铺
        
        Args:
            task: 任务对象
            resume_mode: 是否为恢复模式（跳过导航，直接进入采集）
        """
        try:
            # 恢复模式不重置店铺数据
            if not resume_mode:
                self.state_store.reset_for_new_shop(task.shop_name, task.poi)
                self.exporter.start_shop(task.shop_name, poi=task.poi, task_id=self.current_task_index + 1)
                self.collected_count = 0
            else:
                # 恢复模式：从state_store加载已采集数量
                self.collected_count = self.state_store.collected_count
                self.logger.info(f"恢复模式: 已采集 {self.collected_count} 条，从分类 '{self.state_store.current_category_name}' 继续")
            
            self._update_progress()
            
            # Step 1: 重启App
            self.logger.step("重启美团App")
            self.automator.stop_app()
            time.sleep(1)
            if not self.automator.start_app():
                return False
            
            time.sleep(5)  # 等待首页加载
            
            if not self._check_control():
                return False
            
            # Step 2: 点击外卖（使用固定坐标，左上角第一个图标）
            self.logger.step("进入外卖")
            
            # 获取屏幕尺寸并计算坐标
            screen_info = self.automator.device.info
            screen_width = screen_info.get('displayWidth', 1096)
            screen_height = screen_info.get('displayHeight', 2560)
            
            waimai_x = int(screen_width * 0.05)
            waimai_y = int(screen_height * 0.21)
            
            # 重试机制：检测白屏/错误页面并重试
            for retry in range(3):
                self.logger.info(f"点击外卖坐标: ({waimai_x}, {waimai_y}), 屏幕: {screen_width}x{screen_height}")
                self.automator.device.click(waimai_x, waimai_y)
                time.sleep(3)
                
                # 检测并处理错误页面（重新加载等）
                if self.automator.handle_error_screens():
                    self.logger.info("处理了错误页面，等待恢复...")
                    time.sleep(3)
                
                # 检测页面是否正常加载
                if self.automator.is_page_loaded(min_chinese_chars=15):
                    break
                else:
                    self.logger.warning(f"外卖页面加载异常，返回重试 ({retry + 1}/3)")
                    self.automator.press_back()
                    time.sleep(2)
            
            if not self._check_control():
                return False
            
            # Step 3: 点击看病买药（首页右上角第5个图标）
            self.logger.step("进入看病买药")
            
            pharmacy_x = int(screen_width * 0.9)
            pharmacy_y = int(screen_height * 0.21)
            
            # 重试机制
            for retry in range(3):
                self.logger.info(f"点击看病买药坐标: ({pharmacy_x}, {pharmacy_y})")
                self.automator.device.click(pharmacy_x, pharmacy_y)
                time.sleep(3)
                
                # 检测并处理错误页面
                if self.automator.handle_error_screens():
                    self.logger.info("处理了错误页面，等待恢复...")
                    time.sleep(3)
                
                # 检测页面是否正常加载
                if self.automator.is_page_loaded(min_chinese_chars=15):
                    break
                else:
                    self.logger.warning(f"看病买药页面加载异常，返回重试 ({retry + 1}/3)")
                    self.automator.press_back()
                    time.sleep(2)
            
            if not self._check_control():
                return False
            
            # Step 4: 定位搜索（必须成功）
            self.logger.step("定位搜索", task.poi)
            time.sleep(2)
            if not self._search_location(task.poi):
                self.logger.error("定位搜索失败，终止当前任务")
                return False
            
            if not self._check_control():
                return False
            
            # Step 5: 搜索店铺（必须成功）
            self.logger.step("搜索店铺", task.shop_name)
            time.sleep(2)
            if not self._search_shop(task.shop_name):
                return False
            
            if not self._check_control():
                return False
            
            # Step 6: 点击全部商品
            self.logger.step("点击全部商品")
            time.sleep(2)
            if not self.selector.click_one("all_products_tab", step_name="点击全部商品"):
                self.logger.warning("通过选择器点击全部商品失败，尝试文本模糊匹配")
                if not self.selector.click_by_text_contains("全部", timeout=3):
                    self.logger.error("无法找到'全部商品'标签，请检查页面状态")
                    return False
            
            if not self._check_control():
                return False
            
            # Step 7: 遍历分类采集
            self.logger.step("开始分类采集")
            time.sleep(2)
            if not self._collect_all_categories(resume_mode=resume_mode):
                self.logger.warning("分类采集未完全成功")
            
            # Step 8: 导出结果
            filepath = self.exporter.export()
            if filepath:
                self.logger.info(f"店铺数据已导出: {filepath}")
            
            self.automator.press_back()
            time.sleep(1)
            self.automator.press_back()
            time.sleep(1)
            self.automator.press_back()
            
            return True
            
        except Exception as e:
            self.logger.exception(f"处理店铺[{task.shop_name}]", e)
            return False
    
    def _resume_to_shop(self, task: Task) -> bool:
        """
        恢复模式：重新导航进入店铺页面
        用于风控恢复后，重新进入之前采集的店铺
        
        Args:
            task: 任务对象
            
        Returns:
            是否成功进入店铺页面
        """
        try:
            self.logger.info(f"恢复进入店铺: {task.shop_name}, POI: {task.poi}")
            
            # Step 1: 重启App
            self.logger.step("重启美团App")
            self.automator.stop_app()
            time.sleep(1)
            if not self.automator.start_app():
                return False
            
            time.sleep(5)  # 等待首页加载
            
            # 获取屏幕尺寸
            screen_info = self.automator.device.info
            screen_width = screen_info.get('displayWidth', 1096)
            screen_height = screen_info.get('displayHeight', 2560)
            
            # Step 2: 点击外卖
            self.logger.step("进入外卖")
            waimai_x = int(screen_width * 0.05)
            waimai_y = int(screen_height * 0.21)
            
            for retry in range(3):
                self.automator.device.click(waimai_x, waimai_y)
                time.sleep(3)
                if self.automator.handle_error_screens():
                    time.sleep(3)
                if self.automator.is_page_loaded(min_chinese_chars=15):
                    break
                self.automator.press_back()
                time.sleep(2)
            
            # Step 3: 点击看病买药
            self.logger.step("进入看病买药")
            pharmacy_x = int(screen_width * 0.9)
            pharmacy_y = int(screen_height * 0.21)
            
            for retry in range(3):
                self.automator.device.click(pharmacy_x, pharmacy_y)
                time.sleep(3)
                if self.automator.handle_error_screens():
                    time.sleep(3)
                if self.automator.is_page_loaded(min_chinese_chars=15):
                    break
                self.automator.press_back()
                time.sleep(2)
            
            # Step 4: 定位搜索
            self.logger.step("定位搜索", task.poi)
            time.sleep(2)
            if not self._search_location(task.poi):
                self.logger.error("恢复模式: 定位搜索失败")
                return False
            
            # Step 5: 搜索店铺
            self.logger.step("搜索店铺", task.shop_name)
            time.sleep(2)
            if not self._search_shop(task.shop_name):
                self.logger.error("恢复模式: 店铺搜索失败")
                return False
            
            # Step 6: 点击全部商品
            self.logger.step("点击全部商品")
            time.sleep(2)
            if not self.selector.click_one("all_products_tab", step_name="点击全部商品"):
                if not self.selector.click_by_text_contains("全部", timeout=3):
                    self.logger.error("恢复模式: 无法找到'全部商品'标签")
                    return False
            
            self.logger.info("恢复成功: 已进入店铺页面")
            return True
            
        except Exception as e:
            self.logger.exception("恢复进入店铺", e)
            return False
    
    def _search_location(self, poi: str) -> bool:
        """定位搜索：点击顶部定位入口，输入地址"""
        try:
            screen_info = self.automator.device.info
            screen_width = screen_info.get("displayWidth", 1096)
            screen_height = screen_info.get("displayHeight", 2560)
            
            # Step 0: 错误页面检测与恢复
            # 在开始操作前，检查是否处于错误页面（白屏/重新加载）
            for attempt in range(3):
                # 检测并处理错误页面
                if self.automator.handle_error_screens():
                    self.logger.info("已处理错误页面，等待恢复...")
                    time.sleep(3)
                    continue
                
                # 检测白屏
                if not self.automator.is_page_loaded(min_chinese_chars=10):
                    self.logger.warning(f"检测到白屏/加载异常 ({attempt + 1}/3)")
                    self.automator.press_back()
                    time.sleep(2)
                    continue
                
                break
            
            # Step 4.1: 点击定位入口
            entry_x = int(screen_width * 0.6)
            entry_y = int(screen_height * 0.055)
            
            self.logger.info(f"点击定位入口坐标: ({entry_x}, {entry_y})")
            self.automator.device.click(entry_x, entry_y)
            
            # 等待定位搜索页面加载
            time.sleep(2)
            
            # 再次检测错误页面
            self.automator.handle_error_screens()
            
            # Step 4.2: 输入地址（带重试）
            input_success = False
            for retry in range(3):
                # 检查是否有输入框存在
                if self.selector.set_text("location_search_input", poi, step_name="输入定位关键词"):
                    input_success = True
                    break
                
                # 备用方案1: 通用选择器
                if self.selector.set_text("shop_search_input", poi, step_name="输入定位关键词(兼容)"):
                    input_success = True
                    break
                
                # 备用方案2: 直接查找 EditText
                if self._set_text_fallback(poi):
                    input_success = True
                    break
                
                # 可能在错误页面，尝试恢复
                self.logger.warning(f"输入框查找失败，尝试恢复 ({retry + 1}/3)")
                
                # 检测错误页面
                if self.automator.handle_error_screens():
                    time.sleep(3)
                    # 重新点击定位入口
                    self.automator.device.click(entry_x, entry_y)
                    time.sleep(2)
                    continue
                
                # 可能需要返回重试
                self.automator.press_back()
                time.sleep(1)
                self.automator.device.click(entry_x, entry_y)
                time.sleep(2)
            
            if not input_success:
                self.logger.error("定位输入框查找失败")
                return False
            
            time.sleep(2)
            
            # Step 4.3: 点击搜索结果
            results = self.selector.find_all("location_search_result", timeout=3)
            if results:
                try:
                    bounds = results[0].info.get('bounds')
                    if bounds:
                        center_x = (bounds['left'] + bounds['right']) // 2
                        center_y = (bounds['top'] + bounds['bottom']) // 2
                        
                        if center_y < screen_height * 0.12:
                            self.logger.warning(f"定位结果坐标异常({center_x}, {center_y})，使用坐标兜底")
                        else:
                            self.logger.info(f"点击第一个结果: ({center_x}, {center_y})")
                            self.automator.device.click(center_x, center_y)
                            time.sleep(2)
                            return True
                except Exception as e:
                    self.logger.warning(f"点击定位结果失败: {e}")
            
            # 坐标兜底
            result_x = int(screen_width * 0.5)   
            result_y = int(screen_height * 0.16) 
            
            self.logger.warning(f"使用坐标兜底点击定位结果: ({result_x}, {result_y})")
            self.automator.device.click(result_x, result_y)
            time.sleep(2)
            
            return True
            
        except Exception as e:
            self.logger.exception("定位搜索", e)
            return False
    
    def _set_text_fallback(self, text: str) -> bool:
        try:
            input_elem = self.automator.device(className="android.widget.EditText")
            if input_elem.exists(timeout=3):
                input_elem.set_text(text)
                self.logger.step("输入定位关键词(回退)", f"输入: {text}")
                return True
        except Exception as e:
            self.logger.debug(f"回退输入失败: {e}")
        return False
    
    def _search_shop(self, shop_name: str) -> bool:
        """店铺搜索：输入店名，点击搜索按钮，然后点击第一个搜索结果"""
        try:
            # 获取屏幕尺寸
            screen_info = self.automator.device.info
            w = screen_info.get("displayWidth", 1096)
            h = screen_info.get("displayHeight", 2560)
            
            # 1. 点击搜索框 (选择器 -> 坐标兜底)
            if not self.selector.click_one("shop_search_btn", step_name="点击搜索"):
                self.logger.warning("通过选择器点击搜索失败，尝试坐标兜底")
                
                # 坐标兜底：点击屏幕顶部搜索框区域
                x = int(w * 0.5)
                y = int(h * 0.075)
                
                self.logger.info(f"点击搜索框坐标: ({x}, {y})")
                self.automator.device.click(x, y)
            
            time.sleep(2)
            
            # 2. 输入店铺名
            if not self.selector.wait_exists("shop_search_input", timeout=3):
                self.logger.warning("未检测到搜索输入框，重试点击搜索区域")
                self.automator.device.click(int(w * 0.5), int(h * 0.075))
                time.sleep(2)

            if not self.selector.set_text("shop_search_input", shop_name, step_name="输入店铺名"):
                self.logger.error("无法输入店铺名，可能未进入搜索页")
                return False
            
            time.sleep(1)
            
            # 3. 点击"搜索"按钮 (右上角)
            search_btn_clicked = False
            
            if self.selector.click_by_text("搜索", timeout=2):
                self.logger.step("点击搜索按钮", "文本匹配成功")
                search_btn_clicked = True
            
            if not search_btn_clicked:
                btn_x = int(w * 0.92)
                btn_y = int(h * 0.075)
                self.logger.info(f"点击搜索按钮坐标: ({btn_x}, {btn_y})")
                self.automator.device.click(btn_x, btn_y)
            
            # 等待搜索结果加载
            time.sleep(4)
            
            # 4. 点击搜索结果 (列表第一项)
            # 策略: 店铺名模糊匹配 -> 选择器查找 -> 坐标兜底
            
            # 策略A: 使用店铺名关键词模糊匹配 (最可靠)
            # 提取店铺名第一个有意义的词 (优先中文3-4字)
            import re
            shop_keywords = re.findall(r'[\u4e00-\u9fa5]{2,4}', shop_name)
            
            for keyword in shop_keywords[:2]:  # 尝试前2个关键词
                try:
                    elem = self.automator.device(textContains=keyword)
                    if elem.exists(timeout=2):
                        # 获取匹配元素的坐标，检查是否在搜索结果区域
                        bounds = elem.info.get('bounds')
                        if bounds:
                            center_y = (bounds['top'] + bounds['bottom']) // 2
                            # 搜索结果区域通常在屏幕 20%-80% 的位置
                            if center_y > h * 0.2 and center_y < h * 0.85:
                                self.logger.info(f"通过店铺关键词'{keyword}'找到结果，点击...")
                                elem.click()
                                self.logger.step("选择店铺结果", f"关键词'{keyword}'匹配成功")
                                time.sleep(2)
                                return True
                            else:
                                self.logger.debug(f"关键词'{keyword}'匹配到了顶部/底部元素，跳过")
                except Exception as e:
                    self.logger.debug(f"关键词'{keyword}'匹配失败: {e}")
            
            # 策略B: 使用选择器查找结果项
            results = self.selector.find_all("shop_search_result", timeout=2)
            if results:
                try:
                    min_y = h * 0.2
                    max_y = h * 0.85
                    
                    for elem in results:
                        bounds = elem.info.get('bounds')
                        if bounds:
                            center_y = (bounds['top'] + bounds['bottom']) // 2
                            if min_y < center_y < max_y:
                                center_x = (bounds['left'] + bounds['right']) // 2
                                self.logger.info(f"找到有效店铺结果，中心坐标: ({center_x}, {center_y})")
                                # 使用坐标点击而非直接 click()，更可靠
                                self.automator.device.click(center_x, center_y)
                                self.logger.step("选择店铺结果", "控件坐标点击")
                                time.sleep(2)
                                return True
                            else:
                                self.logger.debug(f"跳过无效位置的结果: Center Y: {center_y}")
                except Exception as e:
                    self.logger.warning(f"选择器查找店铺结果失败: {e}")
            
            # 策略C: 坐标兜底 (点击列表区域第一项)
            # 美团搜索结果页，第一条结果通常在 25%-35% 的高度
            result_x = int(w * 0.5)
            result_y = int(h * 0.28)
            
            self.logger.warning(f"使用坐标兜底点击店铺结果: ({result_x}, {result_y})")
            self.automator.device.click(result_x, result_y)
            time.sleep(2)
            
            return True
            
        except Exception as e:
            self.logger.exception("店铺搜索", e)
            return False
    
    def _collect_all_categories(self, resume_mode: bool = False) -> bool:
        """
        连续滚动采集所有分类
        商品列表是连续的，通过检测分类标题来确定当前分类
        
        Args:
            resume_mode: 是否为恢复模式
        """
        try:
            # 获取分类列表（用于匹配分类标题）
            # 恢复模式优先使用保存的分类列表
            if resume_mode and self.state_store.all_categories:
                categories = self.state_store.all_categories
                self.logger.info(f"恢复模式: 使用保存的 {len(categories)} 个分类")
            else:
                categories = self._get_category_list()
            
            category_set = set(categories) if categories else set()
            
            if not categories:
                self.logger.warning("未获取到分类列表")
                return False
            
            self.logger.info(f"共发现 {len(categories)} 个分类: {categories}")
            
            # 保存分类列表到state_store（用于恢复）
            self.state_store.state["all_categories"] = categories
            
            # 确定起始分类
            start_category = categories[0]
            start_index = 0
            
            # 恢复模式：从上次分类继续
            if resume_mode and self.state_store.current_category_name:
                saved_category = self.state_store.current_category_name
                if saved_category in categories:
                    start_index = categories.index(saved_category)
                    start_category = saved_category
                    self.logger.info(f"恢复模式: 从分类 '{saved_category}' (索引{start_index}) 继续")
                else:
                    self.logger.warning(f"恢复模式: 保存的分类 '{saved_category}' 不在当前列表中，从头开始")
            
            # 点击起始分类
            self.logger.step("点击分类开始采集", start_category)
            
            if not self._click_category(start_category):
                self.logger.warning(f"点击分类失败: {start_category}，尝试继续")
            
            time.sleep(1)
            
            # 初始化当前分类
            current_category = start_category
            current_category_index = start_index
            self.current_category = current_category
            self.state_store.current_category_name = current_category
            self.state_store.current_category_index = current_category_index
            self._update_progress()
            
            # 采集配置
            scroll_config = self.config.get("scroll", {})
            max_scroll = scroll_config.get("max_scroll_times", 100)
            scroll_pause = scroll_config.get("pause_seconds", 1.5)
            no_new_threshold = scroll_config.get("no_new_data_threshold", 3)
            
            no_new_count = 0
            scroll_count = 0
            collected_categories = set()
            collected_categories.add(current_category)
            
            # 标记是否为最后一个分类
            is_last_category = (current_category_index == len(categories) - 1)
            
            self.logger.info(f"开始连续滚动采集，当前分类: {current_category} (最后分类: {is_last_category})")
            
            while scroll_count < max_scroll:
                if not self._check_control():
                    return False
                
                # 检测分类标题（看是否进入了新分类）
                detected_category = self._detect_category_header(category_set)
                if detected_category and detected_category != current_category:
                    self.logger.info(f"检测到分类切换: {current_category} → {detected_category}")
                    current_category = detected_category
                    self.current_category = current_category
                    self.state_store.current_category_name = current_category
                    
                    # 更新分类索引
                    if current_category in categories:
                        current_category_index = categories.index(current_category)
                        self.state_store.current_category_index = current_category_index
                        is_last_category = (current_category_index == len(categories) - 1)
                    
                    collected_categories.add(current_category)
                    self._update_progress()
                    no_new_count = 0  # 重置计数器
                    
                    # 分类切换时保存进度
                    self.state_store.save()
                
                # 采集当前可见的商品
                new_count = self._collect_visible_products(current_category)
                
                if new_count == 0:
                    no_new_count += 1
                    if no_new_count >= no_new_threshold:
                        # 判断是否为风控触发
                        if not is_last_category:
                            # 非最后分类，判定为风控触发
                            self.logger.warning(f"⚠️ 风控触发: 连续{no_new_threshold}次无新数据，当前分类: {current_category} (还有 {len(categories) - current_category_index - 1} 个分类未采集)")
                            self.logger.warning(f"请换号登录后，点击 '继续' 恢复采集")
                            
                            # 标记风控并保存状态
                            self.state_store.mark_risk_control(categories)
                            
                            # 暂停任务等待人工介入
                            self._pause_event.clear()
                            self.status = WorkerStatus.PAUSED
                            
                            # 等待恢复
                            self.logger.info("任务已暂停，等待人工换号后继续...")
                            while not self._pause_event.is_set():
                                if self._stop_event.is_set():
                                    return False
                                time.sleep(0.5)
                            
                            # 用户点击了继续，清除风控标记
                            self.logger.info("收到继续信号，准备恢复采集...")
                            self.state_store.clear_risk_control()
                            
                            # 返回True，让_run方法处理恢复逻辑
                            return True
                        else:
                            # 最后分类，正常结束
                            self.logger.info(f"连续{no_new_threshold}次无新数据，已到达最后分类，采集完成")
                            break
                else:
                    no_new_count = 0
                
                # 向上滚动
                self.automator.swipe_up()
                scroll_count += 1
                
                time.sleep(scroll_pause)
            
            self.logger.info(f"采集完成: 滚动{scroll_count}次, 覆盖{len(collected_categories)}个分类")
            return True
            
        except Exception as e:
            self.logger.exception("分类采集", e)
            return False
    
    def _detect_category_header(self, known_categories: set) -> str:
        """
        检测右侧商品区域出现的分类标题
        分类标题特征：在分割线下方，文本是已知分类名
        
        Args:
            known_categories: 已知的分类名集合
            
        Returns:
            检测到的分类名，未检测到则返回空字符串
        """
        if not known_categories:
            return ""
        
        try:
            # 获取屏幕尺寸
            screen_info = self.automator.device.info
            screen_width = screen_info.get("displayWidth", 1096)
            screen_height = screen_info.get("displayHeight", 2560)
            
            # 分类标题出现的区域：
            # X: 商品区域左侧（20%-50%）
            # Y: 屏幕中上部（15%-70%）
            min_x = screen_width * 0.20
            max_x = screen_width * 0.50
            min_y = screen_height * 0.15
            max_y = screen_height * 0.70
            
            # 获取所有文本元素
            elements = self.automator.device(className="android.widget.TextView")
            
            if not elements.exists(timeout=1):
                return ""
            
            for i in range(elements.count):
                try:
                    elem = elements[i]
                    text = elem.get_text()
                    
                    if not text:
                        continue
                    
                    text = text.strip()
                    
                    # 检查是否是已知分类名
                    if text not in known_categories:
                        continue
                    
                    # 检查坐标是否在标题区域内
                    bounds = elem.info.get('bounds')
                    if not bounds:
                        continue
                    
                    center_x = (bounds['left'] + bounds['right']) // 2
                    center_y = (bounds['top'] + bounds['bottom']) // 2
                    
                    if min_x < center_x < max_x and min_y < center_y < max_y:
                        # 找到了分类标题
                        return text
                        
                except:
                    continue
            
            return ""
            
        except Exception as e:
            return ""
    
    def _click_category(self, category_name: str) -> bool:
        """
        点击分类：先尝试完整文本匹配，失败则尝试部分匹配（解决换行分类问题）
        """
        # 获取屏幕尺寸
        screen_info = self.automator.device.info
        screen_width = screen_info.get("displayWidth", 1096)
        screen_height = screen_info.get("displayHeight", 2560)
        category_center_x = int(screen_width * 0.10)
        max_x = screen_width * 0.20  # 分类区域最大X
        
        # 准备部分匹配的前缀（取前3个字符，解决换行问题如"所搜商品"→"所搜商"+"品"）
        prefix = category_name[:3] if len(category_name) >= 3 else category_name
        
        # 最多尝试3轮滚动查找
        for attempt in range(4):
            # 1. 先尝试完整文本匹配
            if self.selector.click_by_text(category_name, timeout=2):
                return True
            
            # 2. 尝试部分匹配（用前缀）
            try:
                elem = self.automator.device(textContains=prefix)
                if elem.exists(timeout=1):
                    # 找到包含前缀的元素，检查是否在分类区域内
                    for i in range(elem.count):
                        try:
                            bounds = elem[i].info.get('bounds')
                            if bounds:
                                center_x = (bounds['left'] + bounds['right']) // 2
                                if center_x < max_x:
                                    elem[i].click()
                                    self.logger.debug(f"通过前缀'{prefix}'点击分类成功")
                                    return True
                        except:
                            continue
            except:
                pass
            
            if attempt == 0:
                # 第一次失败，可能需要滚回顶部
                self.logger.debug(f"分类'{category_name}'未找到，尝试滚回顶部...")
                for _ in range(3):
                    self.automator.device.swipe(
                        category_center_x, int(screen_height * 0.35),
                        category_center_x, int(screen_height * 0.80),
                        duration=0.3
                    )
                    time.sleep(0.3)
            else:
                # 后续尝试，向下滚动查找
                self.logger.debug(f"分类'{category_name}'未找到，向下滚动查找 ({attempt}/3)...")
                self.automator.device.swipe(
                    category_center_x, int(screen_height * 0.70),
                    category_center_x, int(screen_height * 0.40),
                    duration=0.3
                )
                time.sleep(0.5)
        
        # 最后一次尝试
        return self.selector.click_by_text(category_name, timeout=3)
    
    def _get_category_list(self) -> List[str]:
        """
        获取左侧分类列表
        通过坐标过滤 + 滚动 + 合并换行文本
        """
        all_categories = []
        
        try:
            import re
            
            # 获取屏幕尺寸
            screen_info = self.automator.device.info
            screen_width = screen_info.get("displayWidth", 1096)
            screen_height = screen_info.get("displayHeight", 2560)
            
            # 定义左侧分类区域的边界
            max_x = screen_width * 0.20  # 左侧 20% 区域
            max_y = screen_height * 0.88  # 排除底部 12% 的导航栏
            min_y = screen_height * 0.25  # 排除顶部 25% 的店铺信息栏
            
            # 分类区域的中心X和滑动范围
            category_center_x = int(screen_width * 0.10)
            
            # 黑名单（不包含"推荐"，它是有效分类）
            blacklist = {
                '问商家', '购物车', '免配送费', '起送', '配送费', '首页', 
                '全部商品', '商家', '销量', '价格', '商家会员',
                '入会领5元券', '¥20起送'
            }
            
            # 滚动获取所有分类（最多滚动5次）
            scroll_count = 0
            for scroll_round in range(6):
                # 获取当前可见的分类
                round_categories = self._get_visible_categories(
                    max_x, min_y, max_y, blacklist
                )
                
                # 记录新发现的分类
                new_count = 0
                for cat in round_categories:
                    if cat not in all_categories:
                        all_categories.append(cat)
                        new_count += 1
                
                self.logger.debug(f"分类滚动第{scroll_round + 1}轮: 本轮发现{len(round_categories)}个, 新增{new_count}个")
                
                # 如果没有新分类，尝试再滚动一次确认
                if new_count == 0 and scroll_round > 0:
                    break
                
                # 在分类区域内向上滑动
                if scroll_round < 5:
                    start_y = int(screen_height * 0.80)
                    end_y = int(screen_height * 0.35)
                    self.automator.device.swipe(
                        category_center_x, start_y,
                        category_center_x, end_y,
                        duration=0.3
                    )
                    scroll_count += 1
                    time.sleep(0.8)
            
            # 滚回顶部：反向滑动回去
            if scroll_count > 0:
                self.logger.debug(f"滚回分类列表顶部...")
                for _ in range(scroll_count + 1):
                    start_y = int(screen_height * 0.35)
                    end_y = int(screen_height * 0.80)
                    self.automator.device.swipe(
                        category_center_x, start_y,
                        category_center_x, end_y,
                        duration=0.3
                    )
                    time.sleep(0.5)
            
            self.logger.info(f"共获取到 {len(all_categories)} 个分类")
            return all_categories
            
        except Exception as e:
            self.logger.warning(f"获取分类列表失败: {e}")
            return []
    
    def _get_visible_categories(self, max_x: float, min_y: float, max_y: float, blacklist: set) -> List[str]:
        """获取当前可见的分类列表，合并换行文本"""
        elements = self.automator.device(className="android.widget.TextView")
        
        if not elements.exists(timeout=2):
            return []
        
        # 收集左侧区域的文本及其坐标
        text_items = []
        
        for i in range(elements.count):
            try:
                elem = elements[i]
                text = elem.get_text()
                
                if not text or not text.strip():
                    continue
                
                text = text.strip()
                
                # 跳过黑名单
                if text in blacklist:
                    continue
                
                # 获取坐标
                bounds = elem.info.get('bounds')
                if not bounds:
                    continue
                
                center_x = (bounds['left'] + bounds['right']) // 2
                center_y = (bounds['top'] + bounds['bottom']) // 2
                
                # 只保留左侧分类区域的元素
                if center_x < max_x and min_y < center_y < max_y:
                    text_items.append({
                        'text': text,
                        'x': center_x,
                        'y': center_y,
                        'top': bounds['top'],
                        'bottom': bounds['bottom']
                    })
                    
            except:
                continue
        
        # 按Y坐标排序
        text_items.sort(key=lambda x: x['y'])
        
        # 合并相邻的短文本（处理换行问题）
        # 如果两个文本Y坐标接近（间距 < 50px），且第一个文本很短（< 5字），尝试合并
        categories = []
        i = 0
        while i < len(text_items):
            item = text_items[i]
            merged_text = item['text']
            
            # 检查是否需要与下一个合并
            while i + 1 < len(text_items):
                next_item = text_items[i + 1]
                y_gap = next_item['top'] - item['bottom']
                
                # 如果当前文本很短且与下一个接近，合并
                if len(merged_text) <= 4 and y_gap < 50 and len(next_item['text']) <= 4:
                    merged_text += next_item['text']
                    i += 1
                    item = next_item
                else:
                    break
            
            # 过滤掉太短的（单字）
            if len(merged_text) >= 2:
                categories.append(merged_text)
            
            i += 1
        
        return categories
    
    def _collect_products_in_category(self, category_name: str):
        scroll_config = self.config.get("scroll", {})
        max_scroll = scroll_config.get("max_scroll_times", 30)
        scroll_pause = scroll_config.get("scroll_pause", 1.0)
        no_new_threshold = scroll_config.get("no_new_data_threshold", 2)
        
        no_new_count = 0
        scroll_count = 0
        
        while scroll_count < max_scroll:
            if not self._check_control():
                return
            
            new_count = self._collect_visible_products(category_name)
            
            if new_count == 0:
                no_new_count += 1
                if no_new_count >= no_new_threshold:
                    self.logger.info(f"分类[{category_name}]采集完成，连续{no_new_threshold}次无新数据")
                    break
            else:
                no_new_count = 0
            
            self.automator.swipe_up()
            scroll_count += 1
            self.state_store.scroll_round = scroll_count
            
            time.sleep(scroll_pause)
        
        self.logger.info(f"分类[{category_name}]采集结束: 滑动{scroll_count}次, 本分类采集{self.collected_count}条")
    
    def _collect_visible_products(self, category_name: str) -> int:
        """
        采集当前可见区域的商品
        策略：以价格元素(¥XX.XX)为锚点定位商品卡片，通过坐标关联查找商品名
        """
        new_count = 0
        
        try:
            import re
            
            # 获取屏幕尺寸
            screen_info = self.automator.device.info
            screen_width = screen_info.get("displayWidth", 1096)
            screen_height = screen_info.get("displayHeight", 2560)
            
            # 商品区域边界（排除左侧分类栏 x < 20%）
            product_area_min_x = screen_width * 0.20
            product_area_max_x = screen_width * 0.95
            product_area_min_y = screen_height * 0.15
            product_area_max_y = screen_height * 0.90
            
            # === 第一步：找到所有价格元素作为商品卡片锚点 ===
            # 价格是最可靠的商品标识（红色 ¥XX.XX 格式）
            price_elements = self.automator.device(textMatches=r"^¥?\d+\.?\d*$")
            
            if not price_elements.exists(timeout=2):
                self.logger.debug("未找到价格元素")
                return 0
            
            price_items = []
            for i in range(price_elements.count):
                try:
                    elem = price_elements[i]
                    price_text = elem.get_text()
                    bounds = elem.info.get('bounds')
                    
                    if not price_text or not bounds:
                        continue
                    
                    center_x = (bounds['left'] + bounds['right']) // 2
                    center_y = (bounds['top'] + bounds['bottom']) // 2
                    
                    # 只保留商品区域内的价格
                    if (product_area_min_x < center_x < product_area_max_x and
                        product_area_min_y < center_y < product_area_max_y):
                        price_items.append({
                            'text': price_text.replace('¥', '').replace('￥', ''),
                            'x': center_x,
                            'y': center_y,
                            'top': bounds['top'],
                            'bottom': bounds['bottom'],
                            'left': bounds['left'],
                            'right': bounds['right']
                        })
                except:
                    continue
            
            if not price_items:
                return 0
            
            self.logger.debug(f"找到 {len(price_items)} 个价格元素")
            
            # === 第二步：获取所有文本元素，用于匹配商品名 ===
            all_texts = self.automator.device(className="android.widget.TextView")
            text_items = []
            
            for i in range(all_texts.count):
                try:
                    elem = all_texts[i]
                    text = elem.get_text()
                    bounds = elem.info.get('bounds')
                    
                    if not text or not bounds:
                        continue
                    
                    text = text.strip()
                    if len(text) < 2:  # 只过滤掉单字符（保留"月售2"等）
                        continue
                    
                    center_x = (bounds['left'] + bounds['right']) // 2
                    center_y = (bounds['top'] + bounds['bottom']) // 2
                    
                    # 只保留商品区域内的文本
                    if (product_area_min_x < center_x < product_area_max_x and
                        product_area_min_y < center_y < product_area_max_y):
                        text_items.append({
                            'text': text,
                            'x': center_x,
                            'y': center_y,
                            'top': bounds['top'],
                            'bottom': bounds['bottom']
                        })
                except:
                    continue
            
            # === 第三步：为每个价格找到对应的商品名 ===
            # 规则：商品名应该在价格的上方，且X坐标接近
            for price_item in price_items:
                price_text = price_item['text']
                price_y = price_item['y']
                price_x = price_item['x']
                price_left = price_item['left']
                
                # 查找价格上方的商品名
                # 商品名特征：在价格上方 50-200px，X坐标在同一列
                candidate_names = []
                for text_item in text_items:
                    text = text_item['text']
                    text_y = text_item['y']
                    text_x = text_item['x']
                    
                    # 位置检查：在价格上方
                    y_diff = price_y - text_y
                    if y_diff < 30 or y_diff > 250:  # 上方30-250px范围内
                        continue
                    
                    # X坐标接近（同一列）
                    x_diff = abs(text_x - price_x)
                    if x_diff > 300:  # X偏差不超过300px
                        continue
                    
                    # 排除明显不是商品名的文本
                    if self._is_invalid_product_name(text):
                        continue
                    
                    candidate_names.append({
                        'text': text,
                        'y_diff': y_diff,
                        'x_diff': x_diff
                    })
                
                if not candidate_names:
                    continue
                
                # 选择最佳匹配（优先Y坐标最接近的）
                candidate_names.sort(key=lambda x: x['y_diff'])
                best_name = candidate_names[0]['text']
                
                # === 清理商品名前缀乱码（如 TTTTT[品牌] -> [品牌]）===
                # 这些乱码是图片标签（如"健康年"标签）被识别成的替代字符
                best_name = self._clean_product_name(best_name)
                
                # === 检查是否有月售信息 ===
                # 月售在价格上方，商品名和价格之间（垂直方向）
                monthly_sales = "0"
                for text_item in text_items:
                    text = text_item['text']
                    text_y = text_item['y']
                    text_x = text_item['x']
                    
                    # 月售在价格上方 0-150px 范围内
                    y_diff = price_y - text_y
                    if y_diff < 0 or y_diff > 150:
                        continue
                    
                    # X坐标接近（同一列）
                    x_diff = abs(text_x - price_x)
                    if x_diff > 300:
                        continue
                    
                    # 只查找包含"月售"的文本
                    if '月售' in text:
                        match = re.search(r'月售(\d+)', text)
                        if match:
                            monthly_sales = match.group(1)
                        break
                
                # === 去重检查并保存 ===
                key = self.state_store.generate_key(category_name, best_name, price_text)
                
                if self.state_store.is_collected(key):
                    continue
                
                # 创建记录
                record = create_drug_record(
                    category_name=category_name,
                    drug_name=best_name,
                    monthly_sales=monthly_sales,
                    price=price_text
                )
                
                self.exporter.add_record(record)
                self.state_store.add_collected(key)
                
                self.collected_count += 1
                new_count += 1
                self._update_progress()
                
                self.logger.debug(f"采集: {best_name} | ¥{price_text} | 月销{monthly_sales}")
            
            if new_count > 0:
                self.state_store.save()
            
            return new_count
            
        except Exception as e:
            self.logger.warning(f"采集可见商品失败: {e}")
            return 0
    
    def _is_invalid_product_name(self, text: str) -> bool:
        """检查文本是否是无效的商品名"""
        import re
        
        # 太短
        if len(text) < 5:
            return True
        
        # 中文字符太少
        chinese_chars = re.findall(r'[\u4e00-\u9fa5]', text)
        if len(chinese_chars) < 3:
            return True
        
        # 价格格式
        if re.match(r'^[¥￥\d.]+$', text):
            return True
        
        # 分类名、标签等
        invalid_patterns = [
            r'^推荐$', r'^健康年$', r'^活动$', r'^医保$',
            r'^咳嗽用药$', r'^五官用药$', r'^儿科用药$', r'^常用药品$',
            r'^问.*医生$', r'^已优惠', r'^优惠仅剩', r'^\d+人',
            r'^月售', r'^已售', r'^超\d+人', r'^近期', r'^最近',
            r'^\d+元\*', r'^满\d+减', r'^减\d+元', r'起送',
            r'^搜索', r'^约\d+分钟', r'^刚刚有',
        ]
        
        for pattern in invalid_patterns:
            if re.match(pattern, text):
                return True
        
        return False
    
    def _clean_product_name(self, name: str) -> str:
        """
        清理商品名中的前缀乱码
        例如: TTTTT[力度伸]维生素C... -> [力度伸]维生素C...
        """
        import re
        
        if not name:
            return name
        
        # 查找第一个方括号或中文字符的位置
        # 商品名通常以 [品牌名] 或中文开头
        match = re.search(r'[\[\u4e00-\u9fa5]', name)
        
        if match and match.start() > 0:
            # 如果在开头发现非中文非方括号字符，从第一个有效字符开始截取
            cleaned = name[match.start():]
            return cleaned
        
        return name
    
    def get_status_text(self) -> str:
        return self.status.value
    
    def get_progress_text(self) -> str:
        return f"{self.current_task_index + 1}/{self.total_tasks}"
    
    def get_detail_text(self) -> str:
        if self.current_category:
            return f"分类: {self.current_category} | 已采集: {self.collected_count}条"
        return f"已采集: {self.collected_count}条"
