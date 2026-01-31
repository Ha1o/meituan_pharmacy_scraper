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
from core.mock_automator import MockAutomator
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
        base_output_dir: str = "output",
        config_path: str = "config.json"
    ):
        self.device_serial = device_serial
        self.base_output_dir = base_output_dir
        self.config_path = config_path
        
        # 加载配置
        self.config = self._load_config()
        
        # 初始化组件（传递 base_output_dir，由各模块自行拼接设备隔离路径）
        self.logger = DeviceLogger(device_serial, base_output_dir)
        
        # Mock 模式：serial 以 MOCK- 开头则使用 MockAutomator
        if device_serial.startswith("MOCK-"):
            self.automator = MockAutomator(device_serial, self.logger, self.config)
            self._is_mock = True
        else:
            self.automator = DeviceAutomator(device_serial, self.logger, self.config)
            self._is_mock = False
        self.selector: Optional[SelectorHelper] = None
        self.task_loader = TaskLoader(self.logger)
        self.state_store = StateStore(device_serial, base_output_dir)
        self.exporter = ExcelExporter(device_serial, base_output_dir, self.logger)
        
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
            
            # Mock 模式：使用简化采集流程
            if self._is_mock:
                return self._process_shop_mock(task)
            
            # ---------------------------------------------------------
            # 新增：无感接管采集（指定目录采集）
            # 如果当前已经在【店铺内-全部商品页】，则直接开始采集
            # ---------------------------------------------------------
            if self.is_in_store_all_goods_page():
                self.logger.info("检测到当前已在店铺商品页，进入【指定目录采集】模式")
                # _collect_seamless 内部已处理导出，且手动停止也返回True
                if self._collect_seamless():
                    return True
                else:
                    self.logger.warning("指定目录采集异常，尝试回退到完整流程...")
                    # 只有在非手动停止的异常情况下，才回退到完整流程
            
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
                time.sleep(1)
                
                # 检测并处理错误页面（重新加载等）
                if self.automator.handle_error_screens():
                    self.logger.info("处理了错误页面，等待恢复...")
                    time.sleep(2)
                
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
                time.sleep(1)
                
                # 检测并处理错误页面
                if self.automator.handle_error_screens():
                    self.logger.info("处理了错误页面，等待恢复...")
                    time.sleep(2)
                
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
    
    def _process_shop_mock(self, task: Task) -> bool:
        """
        Mock模式采集流程（简化版，不涉及真实设备操作）
        
        Args:
            task: 任务对象
            
        Returns:
            是否成功
        """
        try:
            self.logger.step("[Mock] 模拟采集店铺", task.shop_name)
            
            # 模拟启动App
            self.automator.start_app()
            time.sleep(0.2)
            
            # 获取模拟分类列表
            categories = self.automator.get_categories()
            self.logger.info(f"[Mock] 获取到 {len(categories)} 个分类")
            
            # 遍历分类采集
            for cat_idx, category in enumerate(categories):
                if not self._check_control():
                    return False
                
                self.current_category = category
                self.state_store.current_category_name = category
                self.state_store.current_category_index = cat_idx
                self._update_progress()
                
                self.logger.info(f"[Mock] 采集分类: {category}")
                
                # 重置滚动位置
                self.automator.reset_scroll_position()
                
                # 模拟滚动采集
                scroll_count = 0
                no_new_count = 0
                max_scroll = 10
                
                while scroll_count < max_scroll:
                    # 获取模拟商品数据
                    products = self.automator.get_visible_products(category)
                    
                    if not products:
                        no_new_count += 1
                        if no_new_count >= 2:
                            break
                    else:
                        no_new_count = 0
                        
                        # 处理商品
                        for prod in products:
                            shop_name = self.state_store.state.get("current_shop_name", "")
                            key = self.state_store.generate_key(
                                shop_name, category, prod["drug_name"], prod["price"]
                            )
                            
                            if self.state_store.is_collected(key):
                                continue
                            
                            # 创建记录
                            record = create_drug_record(
                                category_name=category,
                                drug_name=prod["drug_name"],
                                monthly_sales=prod.get("sales", "0"),
                                price=prod["price"]
                            )
                            
                            self.exporter.add_record(record)
                            self.state_store.add_collected(key)
                            self.collected_count += 1
                            self._update_progress()
                    
                    # 模拟滑动
                    self.automator.swipe_up()
                    scroll_count += 1
                    time.sleep(0.05)
            
            # 导出结果
            filepath = self.exporter.export()
            if filepath:
                self.logger.info(f"[Mock] 店铺数据已导出: {filepath}")
            
            self.state_store.save()
            return True
            
        except Exception as e:
            self.logger.exception(f"[Mock] 处理店铺[{task.shop_name}]", e)
            # 即使异常也尝试导出已采集的数据
            try:
                filepath = self.exporter.export()
                if filepath:
                    self.logger.info(f"[Mock] 异常恢复: 已导出部分数据到 {filepath}")
            except:
                pass
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
                    time.sleep(2)
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
                    time.sleep(2)
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

            # === 边界模式状态机 ===
            # 检查是否启用边界模式
            enable_boundary_mode = self.config.get("features", {}).get("enable_boundary_mode", True)
            verify_threshold = self.config.get("features", {}).get("verify_screen_threshold", 10)

            # 状态变量
            switch_mode = "NORMAL"
            next_category = ""
            divider_y = 0
            verify_screen_count = 0

            while scroll_count < max_scroll:
                if not self._check_control():
                    return False

                # === 优化核心：一次获取，本地解析 ===
                xml_content = self.automator.get_page_source()
                ui_nodes = self.automator.parse_hierarchy(xml_content)

                # === 边界检测（方案1）===
                # 每次滚动后检测是否出现分类边界
                has_boundary, next_category_candidate, boundary_y = self._detect_category_boundary(
                    ui_nodes, current_category, categories
                )

                # 如果检测到边界，触发异步修正逻辑（用户强制要求）
                if has_boundary and next_category_candidate:
                    # 1. 找到分界线之上的最后一个商品（锚点）
                    anchor_product_name = self._find_last_product_above_boundary(ui_nodes, boundary_y)

                    if anchor_product_name:
                        self.logger.info(f"【锚点定位】分类 {current_category} 的最后一个商品是: {anchor_product_name}")

                        # 2. 启动异步线程，5秒后执行修正
                        # 注意：需要传递当前的记录列表快照或索引，但由于是引用传递，直接传 next_category 即可
                        # 修正逻辑：在 records 中找到 anchor_product_name，将其后的所有商品重置为 next_category_candidate

                        def async_correction_task(worker_ref, anchor_name, target_category, log_category_from):
                            try:
                                time.sleep(5)  # 等待5秒
                                worker_ref.logger.info(f"【异步修正启动】开始执行分类修正: {log_category_from} -> {target_category}")

                                # 锁定记录列表（虽然GIL保证了列表操作原子性，但为了逻辑安全）
                                # 在 worker 实例中执行修正
                                count = worker_ref._perform_retroactive_correction(anchor_name, log_category_from, target_category)

                                if count > 0:
                                    worker_ref.logger.info(f"【异步修正完成】已将锚点 '{anchor_name}' 之后的 {count} 个商品归属修正为 {target_category}")
                                else:
                                    worker_ref.logger.info(f"【异步修正跳过】未找到需要修正的商品 (锚点: {anchor_name})")

                            except Exception as e:
                                worker_ref.logger.error(f"【异步修正异常】{e}")

                        # 启动守护线程
                        t = threading.Thread(
                            target=async_correction_task,
                            args=(self, anchor_product_name, next_category_candidate, current_category),
                            daemon=True
                        )
                        t.start()
                    else:
                        self.logger.warning(f"检测到边界但未找到上方锚点商品 (Y={boundary_y})")

                # 如果检测到边界，进入边界模式
                if has_boundary and next_category_candidate:
                    self.logger.info(f"🔄 进入边界模式: {current_category} → {next_category} (分界线Y={boundary_y})")

                    # === 边界模式采集逻辑优化 ===
                    # 无论左侧是否切换，屏幕上此刻都同时存在两个分类的商品（因为检测到了边界）
                    # 必须采集当前屏幕数据，通过 boundary_y 进行区分

                    self.logger.info(f"边界模式采集: {current_category} (上) vs {next_category} (下)")
                    curr_new, next_new = self._collect_visible_products_with_boundary(
                        current_category, ui_nodes, "BOUNDARY", boundary_y, next_category
                    )
                    new_count = curr_new + next_new

                    # 重新获取UI状态，检测左侧是否已切换
                    xml_content_check = self.automator.get_page_source()
                    ui_nodes_check = self.automator.parse_hierarchy(xml_content_check)
                    detected_category = self._detect_selected_category_from_nodes(ui_nodes_check)

                    if detected_category == next_category:
                        # 左侧已切换
                        self.logger.info(f"✅ 左侧已切换完成: {current_category} → {next_category}")
                        current_category = next_category
                        current_category_index += 1
                        self.current_category = current_category
                        self.state_store.current_category_name = current_category
                        self.state_store.current_category_index = current_category_index
                        collected_categories.add(current_category)
                        self._update_progress()
                        self.state_store.save()
                        no_new_count = 0
                        is_last_category = (current_category_index == len(categories) - 1)
                    else:
                        # 左侧还未切换，但我们已经采集了边界数据
                        # 如果下一分类的数据量显著（next_new > 0），我们也可以认为进入了下一分类
                        if next_new > 0:
                             self.logger.info(f"⚠️ 左侧未切换，但已采集到下一分类商品，准备切换: {current_category} → {next_category}")

                        # 再次检测左侧是否已切换 (原有逻辑)
                        detected_category_after = self._detect_selected_category_from_nodes(ui_nodes_check)
                        if detected_category_after == next_category:
                            self.logger.info(f"✅ 左侧分类已切换: {current_category} → {next_category}")
                            current_category = next_category
                            current_category_index += 1
                            self.current_category = current_category
                            self.state_store.current_category_name = current_category
                            self.state_store.current_category_index = current_category_index
                            collected_categories.add(current_category)
                            self._update_progress()
                            self.state_store.save()
                            no_new_count = 0
                            is_last_category = (current_category_index == len(categories) - 1)
                else:
                    # 正常模式：使用当前分类采集
                    new_count = self._collect_visible_products(current_category, ui_nodes)
                
                # 动态阈值：如果是最后一个分类，使用更严格的判定标准（10次无数据）
                # 否则使用配置的阈值（通常较小，用于快速检测风控）
                current_threshold = 10 if is_last_category else no_new_threshold
                
                if new_count == 0:
                    no_new_count += 1
                    if no_new_count >= current_threshold:
                        # 判断是否为风控触发
                        if not is_last_category:
                            # 非最后分类，判定为风控触发
                            self.logger.warning(f"⚠️ 风控触发: 连续{no_new_count}次无新数据，当前分类: {current_category} (还有 {len(categories) - current_category_index - 1} 个分类未采集)")
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
                            self.logger.info(f"连续{no_new_count}次无新数据，已到达最后分类，采集完成")
                            break
                else:
                    no_new_count = 0
                
                # 向上滚动
                self.automator.swipe_up()
                scroll_count += 1
                
                time.sleep(scroll_pause)
            
            if scroll_count >= max_scroll:
                self.logger.warning(f"达到最大滚动次数({max_scroll})停止，可能未采集完所有商品")

            self.logger.info(f"采集完成: 滚动{scroll_count}次, 覆盖{len(collected_categories)}个分类")
            return True
            
        except Exception as e:
            self.logger.exception("分类采集", e)
            return False
    
    def is_in_store_all_goods_page(self) -> bool:
        """
        判断当前是否处于【店铺内-全部商品页】
        
        判定条件（满足任一即可）：
        1. 存在“全部商品”Tab且处于选中态
        2. 存在“搜索店内商品”输入框
        3. 存在商品列表特征（价格符号 ¥ + 加号图标）
        """
        try:
            # 1. 检查“全部商品”Tab (通常会有 selected=True 属性，或者特定的文本颜色/背景)
            # 这里简化判断：页面上有“全部”且有列表特征
            if self.automator.device(textContains="全部").exists(timeout=1):
                # 进一步检查是否有商品列表特征，避免误判
                if self.automator.device(textContains="¥").exists(timeout=1):
                    return True
            
            # 2. 检查“搜索店内商品”输入框
            if self.automator.device(textContains="搜索店内商品").exists(timeout=1):
                return True
                
            # 3. 检查商品列表特征 (价格 + 购买按钮)
            # 这是一个强特征，通常只有在商品列表页才会大量出现
            price_exists = self.automator.device(textContains="¥").exists(timeout=1)
            add_btn_exists = self.automator.device(resourceIdMatches=".*add.*").exists(timeout=0.5) or \
                             self.automator.device(descriptionContains="添加").exists(timeout=0.5)
            
            if price_exists and add_btn_exists:
                return True
                
            return False
        except Exception as e:
            self.logger.debug(f"页面判定失败: {e}")
            return False

    def _collect_seamless(self) -> bool:
        """
        无感接管采集（指定目录采集）
        
        逻辑：
        1. 不导航、不重启、不回顶
        2. 从当前位置开始滚动
        3. 动态识别分类标题更新 current_category
        4. 连续N次无数据或达到最大滚动次数停止
        """
        try:
            self.logger.info(">>> 触发指定目录采集 <<<")
            self.logger.info("保持当前页面状态，直接开始采集...")
            
            # 1. 初始化状态
            # 优先尝试识别左侧选中的分类
            current_category = self._detect_current_selected_category()

            if not current_category:
                # 尝试从屏幕识别当前分类标题
                current_category = self._detect_category_header_seamless()
                
            if not current_category:
                current_category = "未知分类"
                self.logger.info("起始位置未识别到分类，暂定为'未知分类'")
            else:
                self.logger.info(f"起始位置识别到分类: {current_category}")
            
            self.current_category = current_category
            self.state_store.current_category_name = current_category
            self._update_progress()
            
            # 2. 采集配置
            scroll_config = self.config.get("scroll", {})
            max_scroll = scroll_config.get("max_scroll_times", 100)
            scroll_pause = scroll_config.get("pause_seconds", 1.5)
            no_new_threshold = scroll_config.get("no_new_data_threshold", 5)
            
            no_new_count = 0
            scroll_count = 0
            collected_categories = set()
            if current_category != "未知分类":
                collected_categories.add(current_category)
            
            manual_stop = False

            # ========================================
            # 🔍 静态分析模式 - 已禁用
            # ========================================
            STATIC_ANALYSIS_MODE = False

            if STATIC_ANALYSIS_MODE:
                self.logger.info("="*80)
                self.logger.info("🔍 静态分析模式 - 商品归属分析")
                self.logger.info("方案1: 查找分类边界（分割线、分类标题）")
                self.logger.info("方案2: 分析商品卡片控件（查找内部分类信息）")
                self.logger.info("="*80)

                # 获取当前屏幕XML
                xml_content = self.automator.get_page_source()
                ui_nodes = self.automator.parse_hierarchy(xml_content)

                # 获取屏幕尺寸
                screen_info = self.automator.device.info
                w = screen_info.get("displayWidth", 1096)
                h = screen_info.get("displayHeight", 2560)

                # ==========================================
                # 方案1: 查找分类边界元素
                # ==========================================
                self.logger.info("")
                self.logger.info("="*80)
                self.logger.info("【方案1】扫描分类边界元素")
                self.logger.info("="*80)

                # 1.1 查找所有分类标题（右侧商品区域）
                category_titles = []
                for node in ui_nodes:
                    text = node.get('text', '').strip()
                    if not text or len(text) < 2:
                        continue

                    bounds = node.get('bounds')
                    if not bounds:
                        continue

                    cx = bounds['center_x']
                    cy = bounds['center_y']

                    # 只看右侧商品区域（X > 20%）
                    # Y在商品区域（15%-85%）
                    if cx > w * 0.20 and w * 0.15 < cy < w * 0.85:
                        # 检查是否是分类标题
                        # 分类标题特征：2-6个字，不含价格符号等
                        if len(text) <= 6 and '¥' not in text and '月售' not in text:
                            category_titles.append({
                                'text': text,
                                'y': cy,
                                'bounds': bounds,
                                'className': node.get('className', '')
                            })

                self.logger.info(f"\n找到 {len(category_titles)} 个可能的分类标题:")
                for i, title in enumerate(category_titles[:20]):  # 只显示前20个
                    self.logger.info(f"  {i+1}. [{title['text']}] Y={title['y']}, className={title['className']}")

                # 1.2 查找所有分割线
                dividers = []
                for node in ui_nodes:
                    bounds = node.get('bounds')
                    if not bounds:
                        continue

                    # 分割线特征：
                    # 1. 高度很小（<= 5px）
                    # 2. 宽度很大（>= 50%屏宽）
                    # 3. 在商品区域（X > 20%）
                    if (bounds['height'] <= 5 and
                        bounds['width'] >= w * 0.50 and
                        bounds['left'] > w * 0.20):

                        dividers.append({
                            'y': bounds['center_y'],
                            'bounds': bounds,
                            'className': node.get('className', ''),
                            'resourceId': node.get('resourceId', '')
                        })

                self.logger.info(f"\n找到 {len(dividers)} 条可能的分割线:")
                for i, div in enumerate(dividers[:20]):
                    self.logger.info(f"  {i+1}. Y={div['y']}, width={div['bounds']['width']}, height={div['bounds']['height']}, className={div['className']}")

                # ==========================================
                # 方案2: 分析商品卡片控件
                # ==========================================
                self.logger.info("")
                self.logger.info("="*80)
                self.logger.info("【方案2】分析商品卡片控件结构")
                self.logger.info("="*80)

                import re
                import xml.etree.ElementTree as ET

                root = ET.fromstring(xml_content)

                # 2.1 查找所有价格元素（作为商品卡片的锚点）
                def find_all_prices(element, parent_chain=[]):
                    """递归查找所有价格元素及其父链"""
                    prices = []
                    text = element.attrib.get('text', '')

                    # 识别价格
                    if re.match(r"^¥?\d+\.?\d*$", text):
                        price_text = text.replace('¥', '').replace('￥', '')
                        bounds_str = element.attrib.get('bounds', '')

                        if bounds_str:
                            # 解析bounds
                            match = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
                            if match:
                                left, top, right, bottom = map(int, match.groups())
                                center_y = (top + bottom) // 2
                                center_x = (left + right) // 2

                                # 只看商品区域
                                if (center_x > w * 0.20 and center_x < w * 0.95 and
                                    center_y > h * 0.15 and center_y < h * 0.90):

                                    prices.append({
                                        'price': price_text,
                                        'y': center_y,
                                        'x': center_x,
                                        'element': element,
                                        'parent_chain': parent_chain.copy()
                                    })

                    # 递归查找
                    new_chain = parent_chain + [element]
                    for child in element:
                        prices.extend(find_all_prices(child, new_chain))

                    return prices

                all_prices = find_all_prices(root)
                self.logger.info(f"\n找到 {len(all_prices)} 个商品价格（商品卡片）")

                # 2.2 对每个商品卡片，分析其父节点链和所有兄弟节点
                for i, price_info in enumerate(all_prices[:10]):  # 只分析前10个商品
                    self.logger.info("")
                    self.logger.info("="*60)
                    self.logger.info(f"商品 {i+1}: 价格=¥{price_info['price']}, Y={price_info['y']}")
                    self.logger.info("="*60)

                    parent_chain = price_info['parent_chain']

                    # 分析父节点链（只显示前5层）
                    self.logger.info("\n[父节点链] (从近到远):")
                    for depth, parent in enumerate(reversed(parent_chain[:5])):
                        class_name = parent.attrib.get('class', '')
                        resource_id = parent.attrib.get('resource-id', '')
                        bounds = parent.attrib.get('bounds', '')

                        self.logger.info(f"\n  父{depth+1}:")
                        self.logger.info(f"    class = {class_name}")
                        self.logger.info(f"    resource-id = {resource_id}")
                        self.logger.info(f"    bounds = {bounds}")

                        # 查找父节点的所有子节点中是否有分类信息
                        if depth == 0:  # 直接父节点
                            self.logger.info(f"\n  [父1的所有子节点]:")
                            child_count = 0
                            for sibling in parent:
                                sibling_text = sibling.attrib.get('text', '').strip()
                                sibling_class = sibling.attrib.get('class', '')
                                sibling_id = sibling.attrib.get('resource-id', '')

                                if sibling_text or 'category' in sibling_id.lower() or 'tag' in sibling_id.lower():
                                    child_count += 1
                                    self.logger.info(f"    子节点{child_count}:")
                                    self.logger.info(f"      text = '{sibling_text}'")
                                    self.logger.info(f"      class = {sibling_class}")
                                    self.logger.info(f"      resource-id = {sibling_id}")

                                    # 特别标记可能是分类信息的节点
                                    if ('category' in sibling_id.lower() or
                                        'tag' in sibling_id.lower() or
                                        (len(sibling_text) >= 2 and len(sibling_text) <= 6 and '¥' not in sibling_text)):
                                        self.logger.info(f"      ⭐ 可能是分类信息！")

                self.logger.info("")
                self.logger.info("="*80)
                self.logger.info("静态分析完成")
                self.logger.info("请查看日志，对比方案1和方案2的结果")
                self.logger.info("="*80)

                return True

            # 3. 循环采集（正常模式）
                self.logger.info("="*80)
                self.logger.info("🔍 静态分析模式已启用")
                self.logger.info("仅分析当前屏幕，不进行滚动采集")
                self.logger.info("用于对比选中/未选中分类的控件差异")
                self.logger.info("="*80)

                # 只执行一次分析
                xml_content = self.automator.get_page_source()
                ui_nodes = self.automator.parse_hierarchy(xml_content)

                # 检测当前选中的分类
                detected_category = self._detect_selected_category_from_nodes(ui_nodes)

                if detected_category:
                    self.logger.info(f"✅ 检测到选中分类: {detected_category}")
                else:
                    self.logger.warning("⚠️ 未检测到选中分类")

                # 静态分析完成，直接返回
                self.logger.info("")
                self.logger.info("静态分析完成，程序即将退出")
                self.logger.info("请查看日志中的 [同级兄弟节点] 部分，对比差异")
                return True

            # 3. 循环采集（正常模式）
            while scroll_count < max_scroll:
                if not self._check_control():
                    self.logger.info("检测到停止信号，正在保存数据...")
                    manual_stop = True
                    break

                # === 优化核心：一次获取，本地解析 ===
                xml_content = self.automator.get_page_source()
                ui_nodes = self.automator.parse_hierarchy(xml_content)

                # 获取已知分类列表
                categories = list(self.state_store.state.get("categories", []))
                if not categories:
                    categories = self._get_category_list(scroll_rounds=0)
                    if categories:
                        self.state_store.state["categories"] = categories

                # === 严格边界检测 ===
                # 每次滚动后，优先检测是否存在分类边界（分割线/新标题）
                has_boundary, next_cat_candidate, boundary_y = self._detect_category_boundary(
                    ui_nodes, current_category, categories
                )

                if has_boundary:
                    # 即使没有识别出下一分类名，只要有边界线，就尝试从分类列表推断
                    if not next_cat_candidate and current_category in categories:
                        idx = categories.index(current_category)
                        if idx + 1 < len(categories):
                            next_cat_candidate = categories[idx + 1]

                    next_cat_display = next_cat_candidate if next_cat_candidate else "未知分类"
                    self.logger.info(f"🛑 [严格边界控制] 检测到分界线 Y={boundary_y} | 上方: {current_category} | 下方: {next_cat_display}")

                    # === 核心逻辑：回溯修正 (Retroactive Correction) ===
                    # 1. 立即查找分界线上方最后一个商品（锚点）
                    anchor_product = self._find_last_product_above_boundary(ui_nodes, boundary_y)

                    if anchor_product and next_cat_candidate:
                        self.logger.info(f"⚓ 锚点商品(分界线上方): [{anchor_product}]")
                        # 2. 执行回溯修正：在已采集记录中查找该锚点，并将之后的所有商品归类为下一分类
                        self._perform_retroactive_correction(anchor_product, current_category, next_cat_candidate)
                    else:
                        self.logger.debug(f"未能确定锚点或下一分类，跳过回溯修正 (Anchor={anchor_product}, Next={next_cat_candidate})")

                    # 边界模式采集：严格按照 Y 坐标切分
                    curr_new, next_new = self._collect_visible_products_with_boundary(
                        current_category, ui_nodes, "BOUNDARY", boundary_y, next_cat_candidate
                    )
                    new_count = curr_new + next_new

                    # 如果采集到了下方分类的数据，说明已经实质性进入了下一个分类
                    if next_cat_candidate and next_new > 0:
                        self.logger.info(f"✅ [严格边界] 采集到下方新分类数据 ({next_new}条)，执行分类切换")
                        # 立即切换分类
                        current_category = next_cat_candidate
                        self.current_category = current_category
                        self.state_store.current_category_name = current_category
                        collected_categories.add(current_category)
                        self._update_progress()
                        self.state_store.save()
                        no_new_count = 0
                    else:
                        self.logger.info(f"ℹ️ [严格边界] 仅采集到上方分类数据，暂不切换分类")
                        no_new_count = 0

                else:
                    # 无边界模式：常规采集
                    # 仍然检测左侧导航栏，以防万一
                    detected_category = self._detect_selected_category_from_nodes(ui_nodes)

                    # 双重检查：如果左侧没变，尝试从右侧商品区找已知分类标题（作为兜底）
                    if not detected_category:
                        known_categories = set(categories)
                        if known_categories:
                            detected_category = self._detect_category_from_known_list(ui_nodes, known_categories)

                    if detected_category and detected_category != current_category:
                        self.logger.info(f"✅ [常规模式] 检测到分类切换: {current_category} → {detected_category}")
                        current_category = detected_category
                        self.current_category = current_category
                        self.state_store.current_category_name = current_category
                        collected_categories.add(current_category)
                        self._update_progress()
                        no_new_count = 0

                    # 采集
                    new_count = self._collect_visible_products(current_category, ui_nodes)

                if new_count == 0:
                    no_new_count += 1
                    # 动态阈值：如果是最后一个分类，使用更严格的判定标准
                    is_last = (categories and current_category == categories[-1])
                    current_threshold = 10 if is_last else no_new_threshold

                    if no_new_count >= current_threshold:
                        if not is_last:
                            self.logger.warning(f"⚠️ 风控/卡死预警: 连续{no_new_count}次无数据，当前: {current_category}")
                        else:
                            self.logger.info(f"连续 {no_new_count} 次无新数据，已到达最后分类，停止采集")
                            break
                else:
                    no_new_count = 0

                # C. 滚动
                self.automator.swipe_up()
                scroll_count += 1
                time.sleep(scroll_pause)
            
            # 4. 结束处理
            self.logger.info(f"指定目录采集结束: 滚动{scroll_count}次, 涉及分类: {list(collected_categories)}")
            
            # 导出数据 (无论是正常结束还是手动停止，都导出)
            filepath = self.exporter.export()
            if filepath:
                self.logger.info(f"店铺数据已导出: {filepath}")
            
            # 如果是手动停止，返回True以避免触发外层的错误恢复逻辑
            if manual_stop:
                return True
                
            return True
            
        except Exception as e:
            self.logger.exception("指定目录采集", e)
            # 即使发生异常，也尝试导出
            try:
                self.exporter.export()
            except:
                pass
            return False

    def _detect_category_from_known_list(self, ui_nodes: list, known_categories: set) -> str:
        """
        从已知分类列表中匹配商品区的文本
        只匹配真正的分类名（如"儿童用药"、"肿瘤用药"），排除筛选标签（如"肿瘤辅助药"）

        Args:
            ui_nodes: UI节点列表
            known_categories: 已知的分类名称集合

        Returns:
            检测到的分类名，未检测到则返回空字符串
        """
        try:
            # 获取屏幕尺寸
            screen_info = self.automator.device.info
            w = screen_info.get("displayWidth", 1096)
            h = screen_info.get("displayHeight", 2560)

            # 在商品区域查找已知分类名
            # X: 商品区域（排除左侧导航栏）
            min_x = w * 0.20
            max_x = w * 0.80
            # Y: 屏幕中上部
            min_y = h * 0.12
            max_y = h * 0.60

            candidates = []

            for node in ui_nodes:
                text = node.get('text', '').strip()
                if not text:
                    continue

                # 关键：只匹配已知分类名
                if text not in known_categories:
                    continue

                bounds = node.get('bounds')
                if not bounds:
                    continue

                cx = bounds['center_x']
                cy = bounds['center_y']

                # 位置过滤
                if min_x < cx < max_x and min_y < cy < max_y:
                    candidates.append((text, cy))

            if candidates:
                # 按Y坐标排序，取最上面的一个
                candidates.sort(key=lambda x: x[1])
                detected = candidates[0][0]
                self.logger.info(f"从已知分类列表匹配到: {detected} (y={candidates[0][1]})")
                return detected

            return ""
        except Exception as e:
            self.logger.debug(f"已知分类匹配失败: {e}")
            return ""

    def _detect_selected_category_from_nodes(self, ui_nodes: list) -> str:
        """
        从UI节点中检测左侧导航栏当前选中的分类

        策略：仅通过XML层级结构查找橙色竖条indicator
        橙色竖条位置：父3 (FrameLayout) 的子节点
        resourceId: category_item_indicator_left
        """
        try:
            # 使用device.dump_hierarchy()获取完整XML并解析父子关系
            import xml.etree.ElementTree as ET

            xml_content = self.automator.device.dump_hierarchy()
            root = ET.fromstring(xml_content)

            # 查找所有 resourceId=txt_category_name_1 的分类TextView
            category_nodes = []

            def find_category_nodes(element, parent_chain=[]):
                """递归查找所有分类节点并记录父链"""
                resource_id = element.attrib.get('resource-id', '')

                # 找到分类节点
                if 'txt_category_name_1' in resource_id:
                    text = element.attrib.get('text', '').strip()
                    if text and len(text) >= 2:
                        # 排除干扰项
                        if text not in ["推荐", "活动", "品牌", "常用清单", "全部商品", "首页", "商家", "全部", "综合", "销量", "价格"]:
                            category_nodes.append({
                                'text': text,
                                'element': element,
                                'parent_chain': parent_chain.copy()
                            })

                # 递归查找子节点
                new_chain = parent_chain + [element]
                for child in element:
                    find_category_nodes(child, new_chain)

            find_category_nodes(root)

            # 遍历所有分类，检查父3层级是否有橙色竖条
            for cat_info in category_nodes:
                text = cat_info['text']
                parent_chain = cat_info['parent_chain']

                # 获取父3（FrameLayout）
                if len(parent_chain) >= 3:
                    parent3 = parent_chain[-3]  # 倒数第3个是父3

                    # 检查父3的所有子节点，查找橙色竖条
                    for sibling in parent3:
                        sibling_id = sibling.attrib.get('resource-id', '')
                        if 'category_item_indicator' in sibling_id:
                            # 找到橙色竖条，说明这个分类是选中的
                            self.logger.info(f"✅ 检测到选中分类: {text}")
                            return text

            # 兼容性检测：如果没有找到橙色竖条，检查 selected="true" 属性
            # 但仅限左侧分类区域
            screen_info = self.automator.device.info
            w = screen_info.get("displayWidth", 1096)

            for node in ui_nodes:
                if node.get('selected') == 'true':
                    # 检查是否在左侧区域
                    bounds = node.get('bounds')
                    if bounds and bounds['center_x'] < w * 0.25:
                        text = node.get('text', '').strip()
                        if text and len(text) >= 2 and text not in ["推荐", "活动", "品牌"]:
                            self.logger.info(f"✅ 检测到选中分类(selected属性): {text}")
                            return text

            return ""

        except Exception as e:
            self.logger.error(f"分类检测失败: {e}")
            return ""

    def _detect_selected_by_orange_bar(self, ui_nodes: list) -> str:
        """已弃用：不再使用不准确的坐标推断"""
        return ""

    def _detect_current_selected_category(self) -> str:
        """
        检测左侧导航栏当前选中的分类
        仅使用XML结构检测，不再进行位置推断
        """
        return self._detect_selected_category_from_nodes(self.automator.parse_hierarchy(self.automator.device.dump_hierarchy()))

    def _detect_category_header_seamless(self, ui_nodes: list = None) -> str:
        """
        无感模式下的分类标题检测
        只检测右侧内容区域，排除左侧侧边栏
        
        Args:
            ui_nodes: 预解析的UI节点列表
        """
        try:
            # 获取屏幕尺寸
            screen_info = self.automator.device.info
            w = screen_info.get("displayWidth", 1096)
            h = screen_info.get("displayHeight", 2560)
            
            # 区域限制：
            # X: 必须在左侧侧边栏右边 (x > 0.25w)
            min_x = w * 0.25
            
            # Y: 动态计算起始高度
            min_y = h * 0.12  # 默认兜底值
            
            # 尝试从节点中找到"全部"或"全部商品"来调整min_y
            if ui_nodes:
                for node in ui_nodes:
                    text = node.get('text', '')
                    if text in ["全部", "全部商品"]:
                        bounds = node.get('bounds')
                        if bounds and bounds['bottom'] < h * 0.3:
                            min_y = bounds['bottom'] + 10
                            break
            
            max_y = h * 0.6
            
            candidates = []
            
            if ui_nodes is not None:
                # 使用本地节点
                for node in ui_nodes:
                    text = node.get('text', '')
                    if not text: continue
                    text = text.strip()
                    
                    # 过滤规则
                    if len(text) < 2 or len(text) > 8: continue 
                    if "¥" in text or "月售" in text or "折" in text: continue 
                    if text in ["全部", "综合", "销量", "价格"]: continue
                    if text in ["活动", "推荐", "品牌"]: continue
                    
                    bounds = node.get('bounds')
                    if not bounds: continue
                    
                    cx = bounds['center_x']
                    cy = bounds['center_y']
                    
                    if cx > min_x and min_y < cy < max_y:
                        candidates.append((text, cy))
            else:
                # 兼容旧逻辑（虽然应该不会走到这里）
                return ""
            
            if candidates:
                # 按Y坐标排序，取最上面的一个
                candidates.sort(key=lambda x: x[1])
                best_match = candidates[0][0]
                self.logger.info(f"识别到右侧分类标题: {best_match} (y={candidates[0][1]})")
                return best_match
            
            return ""
        except:
            return ""

    def _detect_next_category_from_sidebar(self, ui_nodes: list) -> str:
        """
        [New] 从侧边栏动态检测下一个分类
        策略：
        1. 找到橙色指示条(category_item_indicator)确定当前分类位置
        2. 在侧边栏列表中找到位于当前分类下方的第一个有效分类名
        """
        try:
            screen_info = self.automator.device.info
            w = screen_info.get("displayWidth", 1096)
            sidebar_max_x = w * 0.25

            # 1. 寻找橙色指示条的位置
            indicator_y = -1
            for node in ui_nodes:
                rid = node.get('resourceId', '')
                if 'category_item_indicator' in rid:
                    bounds = node.get('bounds')
                    if bounds:
                        indicator_y = bounds['center_y']
                        break

            # 2. 收集所有侧边栏分类项
            sidebar_items = []
            for node in ui_nodes:
                text = node.get('text', '').strip()
                bounds = node.get('bounds')

                if not text or not bounds:
                    continue

                # 必须在侧边栏区域 (收紧范围至20%，排除右侧筛选栏)
                if bounds['center_x'] > w * 0.20:
                    continue

                # 排除无效文本
                if len(text) < 2 or text in ["推荐", "活动", "品牌", "常用清单", "全部商品", "首页", "商家", "综合", "销量", "价格", "优惠", "筛选", "排序"]:
                    continue

                # 排除价格数字
                if "¥" in text or text.replace('.', '').isdigit():
                    continue

                sidebar_items.append({
                    'text': text,
                    'y': bounds['center_y'],
                    'selected': node.get('selected') == 'true'
                })

            # 按 Y 坐标排序
            sidebar_items.sort(key=lambda x: x['y'])

            # 3. 确定当前分类索引
            current_index = -1

            # 优先使用指示条匹配
            if indicator_y != -1:
                min_dist = 9999
                for i, item in enumerate(sidebar_items):
                    dist = abs(item['y'] - indicator_y)
                    if dist < min_dist:
                        min_dist = dist
                        current_index = i

                # 如果距离太远（超过150px），可能匹配错误
                if min_dist > 150:
                    current_index = -1

            # 降级：使用 selected 属性匹配
            if current_index == -1:
                for i, item in enumerate(sidebar_items):
                    if item['selected']:
                        current_index = i
                        break

            # 4. 返回下一个分类
            if current_index != -1 and current_index + 1 < len(sidebar_items):
                next_item = sidebar_items[current_index + 1]
                self.logger.debug(f"侧边栏动态检测: 当前='{sidebar_items[current_index]['text']}' -> 下一个='{next_item['text']}'")
                return next_item['text']

            return ""

        except Exception as e:
            self.logger.debug(f"侧边栏检测失败: {e}")
            return ""

    def _detect_category_boundary(self, ui_nodes: list, current_category: str, all_categories: list) -> tuple:
        """
        检测分类边界（分割线和下一分类标题）

        修改后逻辑：
        1. 必须存在分割线
        2. 下一分类优先通过侧边栏动态检测 (Strict Single Mode)
        """
        try:
            screen_info = self.automator.device.info
            w = screen_info.get("displayWidth", 1096)
            h = screen_info.get("displayHeight", 2560)

            # 1. 查找分割线（商品区域的横线）
            dividers = []
            for node in ui_nodes:
                bounds = node.get('bounds')
                if not bounds:
                    continue

                # 分割线特征：高度<=5px, 宽度>=50%屏宽, 在商品区域
                if (bounds['height'] <= 5 and
                    bounds['width'] >= w * 0.50 and
                    bounds['left'] > w * 0.20 and
                    w * 0.15 < bounds['center_y'] < h * 0.85):

                    dividers.append({
                        'y': bounds['center_y'],
                        'height': bounds['height'],
                        'width': bounds['width']
                    })

            # 按Y坐标排序，取最上面的分割线
            divider_y = None
            if dividers:
                dividers.sort(key=lambda x: x['y'])
                divider_y = dividers[0]['y']
                self.logger.debug(f"边界检测: 找到 {len(dividers)} 条分割线, 选择 Y={divider_y}")

            if not divider_y:
                return (False, None, None)

            # 2. 动态检测下一分类 (从侧边栏)
            # 这是用户要求的核心逻辑：check orange bar, text below is next category
            next_category = self._detect_next_category_from_sidebar(ui_nodes)

            # 3. 结果判断
            if next_category:
                self.logger.info(f"📍 检测到分类边界: 下一分类标题 '{next_category}' (侧边栏动态识别) 分割线 Y={divider_y}")
                return (True, next_category, divider_y)

            # 如果没检测到下一分类，但有分割线，依然返回 True，但分类名为 None
            # 这样外层逻辑至少知道有边界，可以避免错误归类（虽然无法进行修正）
            self.logger.debug(f"边界检测: 只找到分割线 Y={divider_y}，但未找到下一分类标题")
            return (True, None, divider_y)

        except Exception as e:
            self.logger.warning(f"边界检测失败: {e}")
            return (False, None, None)
        """
        检测右侧商品区域出现的分类标题
        分类标题特征：在分割线下方，文本是已知分类名
        
        Args:
            known_categories: 已知的分类名集合
            ui_nodes: 预解析的UI节点列表（如果提供则直接使用，否则查询设备）
            
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
            
            # 使用预解析的节点或查询设备
            if ui_nodes is not None:
                # 使用本地节点
                for node in ui_nodes:
                    text = node.get('text', '')
                    if not text: continue
                    
                    text = text.strip()
                    if text not in known_categories: continue
                    
                    bounds = node.get('bounds')
                    if not bounds: continue
                    
                    center_x = bounds['center_x']
                    center_y = bounds['center_y']
                    
                    if min_x < center_x < max_x and min_y < center_y < max_y:
                        return text
                return ""
            else:
                # 原有逻辑：查询设备
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

    def _detect_divider_line(self, ui_nodes: list, category_title_y: int) -> int:
        """
        检测分类标题上方的分割线

        Args:
            ui_nodes: UI节点列表
            category_title_y: 分类标题的Y坐标

        Returns:
            分割线Y坐标，未检测到则返回0
        """
        try:
            # 获取屏幕尺寸
            screen_info = self.automator.device.info
            screen_width = screen_info.get("displayWidth", 1096)
            screen_height = screen_info.get("displayHeight", 2560)

            # 分割线特征：
            # 1. className包含"View"
            # 2. 高度 <= 5px
            # 3. 宽度 >= 50%屏宽
            # 4. 在商品区域(X>20%)
            # 5. 在分类标题上方0-200px

            min_x = screen_width * 0.20
            min_width = screen_width * 0.50
            max_height = 5

            # 搜索范围：分类标题上方0-200px
            search_min_y = max(0, category_title_y - 200)
            search_max_y = category_title_y

            candidates = []

            for node in ui_nodes:
                class_name = node.get('className', '')
                if 'View' not in class_name:
                    continue

                bounds = node.get('bounds')
                if not bounds:
                    continue

                # 检查尺寸
                if bounds['height'] > max_height:
                    continue
                if bounds['width'] < min_width:
                    continue

                # 检查位置
                if bounds['left'] < min_x:
                    continue

                center_y = bounds['center_y']
                if not (search_min_y <= center_y <= search_max_y):
                    continue

                # 符合条件的候选分割线
                candidates.append({
                    'y': center_y,
                    'distance': category_title_y - center_y
                })

            if not candidates:
                return 0

            # 返回最接近分类标题的分割线
            candidates.sort(key=lambda x: x['distance'])
            return candidates[0]['y']

        except Exception as e:
            self.logger.debug(f"分割线检测失败: {e}")
            return 0

    def _detect_left_selected_category(self, ui_nodes: list, expected_category: str) -> bool:
        """
        检测左侧选中的分类

        Args:
            ui_nodes: UI节点列表
            expected_category: 期望的分类名

        Returns:
            是否检测到左侧已切换为expected_category
        """
        try:
            # 获取屏幕尺寸
            screen_info = self.automator.device.info
            screen_width = screen_info.get("displayWidth", 1096)
            screen_height = screen_info.get("displayHeight", 2560)

            # 左侧分类区域: X<20%, Y:15%-90%
            max_x = screen_width * 0.20
            min_y = screen_height * 0.15
            max_y = screen_height * 0.90

            # 方法1：从ui_nodes查找 selected='true' 且文本匹配的节点
            for node in ui_nodes:
                selected = node.get('selected', 'false')
                if selected != 'true':
                    continue

                text = node.get('text', '').strip()
                if not text:
                    continue

                # 检查文本是否匹配（完整匹配或部分匹配）
                if text != expected_category and expected_category not in text:
                    continue

                bounds = node.get('bounds')
                if not bounds:
                    continue

                # 检查位置
                center_x = bounds['center_x']
                center_y = bounds['center_y']

                if center_x < max_x and min_y < center_y < max_y:
                    self.logger.debug(f"检测到左侧选中分类: {text}")
                    return True

            # 方法2：兜底方案 - 使用device查询
            try:
                elem = self.automator.device(text=expected_category, selected=True)
                if elem.exists(timeout=1):
                    bounds = elem.info.get('bounds')
                    if bounds:
                        center_x = (bounds['left'] + bounds['right']) // 2
                        center_y = (bounds['top'] + bounds['bottom']) // 2
                        if center_x < max_x and min_y < center_y < max_y:
                            self.logger.debug(f"检测到左侧选中分类(兜底): {expected_category}")
                            return True
            except:
                pass

            return False

        except Exception as e:
            self.logger.debug(f"左侧选中分类检测失败: {e}")
            return False

    def _get_category_title_y(self, category_name: str, ui_nodes: list) -> int:
        """
        获取分类标题的Y坐标

        Args:
            category_name: 分类名
            ui_nodes: UI节点列表

        Returns:
            分类标题Y坐标，未找到则返回0
        """
        try:
            # 获取屏幕尺寸
            screen_info = self.automator.device.info
            screen_width = screen_info.get("displayWidth", 1096)

            # 分类标题区域: X: 20%-50%
            min_x = screen_width * 0.20
            max_x = screen_width * 0.50

            for node in ui_nodes:
                text = node.get('text', '').strip()
                if text != category_name:
                    continue

                bounds = node.get('bounds')
                if not bounds:
                    continue

                center_x = bounds['center_x']
                if min_x < center_x < max_x:
                    return bounds['center_y']

            return 0

        except Exception as e:
            self.logger.debug(f"获取分类标题Y坐标失败: {e}")
            return 0

    def _update_category_index(self, categories: list, category_name: str):
        """
        更新分类索引并保存状态

        Args:
            categories: 分类列表
            category_name: 当前分类名
        """
        try:
            if category_name in categories:
                category_index = categories.index(category_name)
                self.state_store.current_category_index = category_index
                self.state_store.save()
                self.logger.debug(f"分类索引已更新: {category_name} -> {category_index}")
        except Exception as e:
            self.logger.debug(f"更新分类索引失败: {e}")

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
    
    def _get_category_list(self, scroll_rounds: int = 5) -> List[str]:
        """
        获取左侧分类列表
        通过坐标过滤 + 滚动 + 合并换行文本

        Args:
            scroll_rounds: 滚动次数，默认5次。传入0则只获取当前可见分类，不滚动。
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
            max_y = screen_height * 0.90  # 放宽底部限制 (原0.88)
            min_y = screen_height * 0.15  # 放宽顶部限制 (原0.25)，避免漏掉靠上的分类

            # 分类区域的中心X和滑动范围
            category_center_x = int(screen_width * 0.10)

            # 黑名单（不包含"推荐"，它是有效分类）
            blacklist = {
                '问商家', '购物车', '免配送费', '起送', '配送费', '首页',
                '全部商品', '商家', '销量', '价格', '商家会员',
                '入会领5元券', '¥20起送'
            }

            # 滚动获取所有分类
            scroll_count = 0
            # 如果 scroll_rounds 为 0，则 range(1) 只执行一次不滚动
            loop_count = scroll_rounds + 1 if scroll_rounds > 0 else 1

            for scroll_round in range(loop_count):
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

                self.logger.debug(f"分类获取第{scroll_round + 1}轮: 本轮发现{len(round_categories)}个, 新增{new_count}个")

                # 如果不要求滚动，直接跳出
                if scroll_rounds <= 0:
                    break

                # 如果没有新分类，尝试再滚动一次确认
                if new_count == 0 and scroll_round > 0:
                    break

                # 在分类区域内向上滑动
                if scroll_round < scroll_rounds:
                    start_y = int(screen_height * 0.80)
                    end_y = int(screen_height * 0.35)
                    self.automator.device.swipe(
                        category_center_x, start_y,
                        category_center_x, end_y,
                        duration=0.3
                    )
                    scroll_count += 1
                    time.sleep(0.8)

            # 滚回顶部：反向滑动回去 (只有发生了滚动才滚回)
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
    
    def _detect_all_category_titles_on_screen(self, ui_nodes: list, category_set: set) -> list:
        """
        检测屏幕上所有出现的分类标题及其Y坐标

        ⚠️ 重要：只检测右侧商品区域的分类标题，不包括左侧导航栏
        右侧商品区域的分类标题是商品列表的分隔符，用于划分不同分类的商品

        Args:
            ui_nodes: UI节点列表
            category_set: 已知的分类名称集合

        Returns:
            [
                {"name": "儿童用药", "y": 300},
                {"name": "肿瘤用药", "y": 1500},
                ...
            ]
            按Y坐标从小到大排序
        """
        try:
            # 获取屏幕尺寸，用于区分左侧导航栏和右侧商品区域
            screen_info = self.automator.device.info
            screen_width = screen_info.get("displayWidth", 1096)
            screen_height = screen_info.get("displayHeight", 2560)

            # 区域定义：
            # - 左侧导航栏：X坐标 < 20%（这里的分类文本是导航用的，不要）
            # - 右侧商品区域：X坐标 >= 20%（这里的分类文本才是商品列表的分隔符）
            # - 顶部筛选区域：Y坐标 < 12%（原15%，放宽以检测靠上的标题）
            sidebar_max_x = screen_width * 0.20
            top_filter_max_y = screen_height * 0.12

            category_titles = []

            for node in ui_nodes:
                text = node.get('text', '').strip()
                if not text:
                    continue

                # 检查是否为分类标题
                if text not in category_set:
                    continue

                bounds = node.get('bounds')
                if not bounds:
                    continue

                center_x = bounds['center_x']
                center_y = bounds['center_y']

                # ✅ 只保留右侧商品区域的分类标题
                # 排除左侧导航栏（X < 20%）
                if center_x < sidebar_max_x:
                    continue

                # 排除顶部筛选标签区域（Y < 15%）
                if center_y < top_filter_max_y:
                    continue

                # 记录分类标题及其Y坐标
                category_titles.append({
                    "name": text,
                    "y": center_y
                })

            # 按Y坐标排序（从上到下）
            category_titles.sort(key=lambda x: x['y'])

            return category_titles

        except Exception as e:
            self.logger.warning(f"检测分类标题失败: {e}")
            return []

    def _build_category_zones(self, category_titles: list, screen_height: int) -> list:
        """
        根据分类标题构建分类区间表

        Args:
            category_titles: 分类标题列表 [{"name": "儿童用药", "y": 300}, ...]
            screen_height: 屏幕高度

        Returns:
            [
                {"name": "儿童用药", "y_start": 0, "y_end": 1200},
                {"name": "肿瘤用药", "y_start": 1200, "y_end": 2560}
            ]
        """
        if not category_titles:
            return []

        zones = []

        for i, title in enumerate(category_titles):
            y_start = 0 if i == 0 else category_titles[i - 1]['y']
            y_end = category_titles[i + 1]['y'] if i + 1 < len(category_titles) else screen_height

            # 使用当前标题的Y坐标作为起始点（标题下方才是该分类的商品）
            # 区间为：当前标题Y坐标 到 下一个标题Y坐标
            zones.append({
                "name": title['name'],
                "y_start": title['y'],
                "y_end": y_end
            })

        return zones

    def _find_category_by_y(self, y: int, category_zones: list, fallback_category: str) -> str:
        """
        根据Y坐标查找商品所属分类

        Args:
            y: 商品的Y坐标
            category_zones: 分类区间表
            fallback_category: 兜底分类（当没有匹配区间时使用）

        Returns:
            分类名称
        """
        if not category_zones:
            return fallback_category

        # 查找匹配的区间
        for zone in category_zones:
            if zone['y_start'] <= y < zone['y_end']:
                return zone['name']

        # 如果没有匹配，使用最后一个分类（可能是滚动到底部了）
        if y >= category_zones[-1]['y_start']:
            return category_zones[-1]['name']

        # 兜底：使用传入的分类
        return fallback_category

    def _collect_products_by_structure(self, category_name: str, mode: str = "NORMAL", boundary_y: int = 0, next_category: str = "") -> tuple:
        """
        【重构核心】基于XML树形结构的商品采集
        不再依赖坐标推断，而是通过父子节点关系定位商品卡片
        """
        import xml.etree.ElementTree as ET
        import re

        current_new_count = 0
        next_new_count = 0

        try:
            # 1. 获取完整XML树
            xml_content = self.automator.get_page_source()
            if not xml_content:
                return (0, 0)

            # 处理可能的编码问题
            if isinstance(xml_content, bytes):
                xml_content = xml_content.decode('utf-8', errors='ignore')

            # 移除非法字符避免解析错误
            xml_content = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', xml_content)

            root = ET.fromstring(xml_content)

            # === 恢复智能区间检测逻辑 ===
            # 1. 解析扁平化节点用于标题检测
            ui_nodes = self.automator.parse_hierarchy(xml_content)
            category_set = set(self.state_store.state.get("categories", []))

            # 2. 检测屏幕上的分类标题
            category_titles = self._detect_all_category_titles_on_screen(ui_nodes, category_set)

            # 3. 构建Y坐标区间
            screen_height = self.automator.device.info.get("displayHeight", 2560)
            category_zones = self._build_category_zones(category_titles, screen_height)

            if category_zones:
                zones_str = [f"{z['name']}({z['y_start']}-{z['y_end']})" for z in category_zones]
                self.logger.debug(f"智能分区生效: {zones_str}")
            # ============================

            # 2. 找到所有价格节点作为锚点
            price_nodes = []

            # 辅助函数：递归查找价格节点
            def find_price_nodes(element, ancestors=[]):
                text = element.attrib.get('text', '')
                # 匹配价格格式 (¥xx.xx)
                if re.match(r"^¥?\d+\.?\d*$", text):
                    # 记录价格节点及其祖先链
                    price_nodes.append({
                        'element': element,
                        'text': text,
                        'ancestors': ancestors + [element], # 包含自己在内的完整路径
                        'y': self._get_center_y(element)
                    })

                # 递归查找子节点
                current_chain = ancestors + [element]
                for child in element:
                    find_price_nodes(child, current_chain)

            find_price_nodes(root)

            self.logger.debug(f"结构化分析: 找到 {len(price_nodes)} 个价格锚点")

            # 3. 遍历每个价格，向上寻找"商品卡片容器"
            processed_keys = set()

            # 获取屏幕宽高用于过滤
            screen_width = self.automator.device.info.get("displayWidth", 1096)
            min_x = screen_width * 0.20 # 排除左侧分类栏

            for p_node in price_nodes:
                price_text = p_node['text'].replace('¥', '').replace('￥', '')
                price_y = p_node['y']

                # 过滤左侧分类栏误识别的数字
                bounds = self._get_bounds(p_node['element'])
                if bounds and bounds['center_x'] < min_x:
                    continue

                ancestors = p_node['ancestors']
                # 从直接父节点开始向上查找，最多找4层（通常卡片在父2或父3）
                # 倒序遍历祖先: -2是父节点, -3是爷爷...
                card_found = False
                best_name = ""
                monthly_sales = "0"

                # 我们尝试向上找几层，每一层都作为一个潜在的容器
                for i in range(2, min(7, len(ancestors) + 1)):
                    parent = ancestors[-i]

                    # 在这个父容器中查找商品名（以 [ 或 【 开头）

                    # 提取该容器下所有文本节点
                    container_texts = []
                    def extract_texts(elem):
                        t = elem.attrib.get('text', '').strip()
                        if t:
                            # 计算Y坐标
                            cy = self._get_center_y(elem)
                            container_texts.append({'text': t, 'y': cy})
                        for child in elem:
                            extract_texts(child)

                    extract_texts(parent)

                    # === 调试日志：针对特定商品输出容器内容 ===
                    if "77.8" in price_text or "12" in price_text:
                        self.logger.debug(f"🔍 [调试] 价格 {price_text} (层级-{i}) 容器内容:")
                        for debug_item in container_texts:
                            self.logger.debug(f"   -> '{debug_item['text']}' (Y={debug_item['y']})")
                    # ======================================

                    # 寻找商品名和销量
                    candidates = []
                    sales_found = "0"

                    for item in container_texts:
                        t = item['text']
                        # 忽略价格本身
                        if t == p_node['text']:
                            continue

                        # 查找销量 (只采集"月售"，严格排除"已售")
                        if '月售' in t:
                            m = re.search(r'月售\s*(\d+)', t)
                            if m:
                                sales_found = m.group(1)

                        # 查找潜在商品名
                        # 1. 必须在价格上方
                        if item['y'] >= price_y:
                            continue

                        # 2. 查找潜在商品名
                        # 放宽条件：只要包含 [ 或 【 即可，允许前面有标签（如 "健康年 [健安适]..."）
                        # 并且不能是 "优惠仅剩" 等明显非标题的文本
                        if ('[' in t or '【' in t) and len(t) > 5:
                            # 排除特定的营销文案
                            if any(x in t for x in ["优惠仅剩", "已优惠", "券后", "起送", "配送费"]):
                                continue

                            # 如果 [ 不在开头，确保它在前面不远处 (比如前10个字符内)
                            # 避免匹配到 "... [标签] ..." 这种描述性文本
                            idx = t.find('[') if '[' in t else t.find('【')
                            if idx > 10:
                                continue

                            candidates.append(item)

                    if candidates:
                        # 找到了！这个 parent 就是卡片容器
                        # 选最靠上的（通常是主标题）
                        candidates.sort(key=lambda x: x['y'])
                        best_name = candidates[0]['text']
                        monthly_sales = sales_found
                        card_found = True
                        break # 停止向上查找

                if not card_found:
                    self.logger.debug(f"⚠️ 价格 {price_text} (Y={price_y}) 未找到对应的商品名容器，跳过")
                    continue

                # === 找到了一组有效数据 ===
                # 清理商品名
                best_name = self._clean_product_name(best_name)

                # === 确定归属分类 (优先级：智能区间 > 边界模式 > 默认) ===
                target_category = category_name

                if category_zones:
                    # 优先使用智能区间判断
                    target_category = self._find_category_by_y(price_y, category_zones, category_name)
                elif mode == "BOUNDARY" and boundary_y > 0:
                    # 回退到边界模式
                    if price_y < boundary_y:
                        target_category = category_name
                    else:
                        target_category = next_category
                        # 如果是边界模式且位于分界线下方，但不知道下一分类名
                        # 必须跳过，防止归类到当前分类（Category Drift）
                        if not target_category:
                            self.logger.debug(f"⚠️ 价格 {price_text} (Y={price_y}) 位于边界线(Y={boundary_y})下方且无下一分类名，跳过")
                            continue

                # 前排保护逻辑 (Top 35% 且没有被划分为下一页)
                # 如果智能区间已经判定了，就不需要这个保护了，或者作为辅助
                if not category_zones:
                    screen_height = self.automator.device.info.get("displayHeight", 2560)
                    if price_y < screen_height * 0.35 and target_category != category_name:
                         target_category = category_name

                # 生成唯一键去重
                shop_name = self.state_store.state.get("current_shop_name", "")
                key = self.state_store.generate_key(shop_name, target_category, best_name, price_text)

                if key in processed_keys:
                    continue
                processed_keys.add(key)

                if self.state_store.is_collected(key):
                    continue

                # 保存
                record = create_drug_record(
                    category_name=target_category,
                    drug_name=best_name,
                    monthly_sales=monthly_sales,
                    price=price_text
                )

                self.exporter.add_record(record)
                self.state_store.add_collected(key)
                self.collected_count += 1

                if target_category == category_name:
                    current_new_count += 1
                else:
                    next_new_count += 1

                self.logger.info(f"结构化采集[{target_category}]: {best_name} | ¥{price_text} | 月销{monthly_sales}")

        except Exception as e:
            self.logger.error(f"结构化采集出错: {e}")
            import traceback
            self.logger.error(traceback.format_exc())

        if current_new_count + next_new_count > 0:
            self.state_store.save()

        return (current_new_count, next_new_count)

    def _get_bounds(self, element):
        """解析XML元素的bounds属性"""
        import re
        bounds_str = element.attrib.get('bounds', '')
        match = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
        if match:
            left, top, right, bottom = map(int, match.groups())
            return {
                'left': left, 'top': top, 'right': right, 'bottom': bottom,
                'width': right - left, 'height': bottom - top,
                'center_x': (left + right) // 2,
                'center_y': (top + bottom) // 2
            }
        return None

    def _get_center_y(self, element):
        b = self._get_bounds(element)
        return b['center_y'] if b else 0

    def _find_last_product_above_boundary(self, ui_nodes: list, boundary_y: int) -> str:
        """
        找到分界线上方最近的一个商品名（锚点商品）
        """
        try:
            # 复用 _collect_products_by_structure 的部分逻辑
            # 但这里我们只需要找到 Y < boundary_y 且 Y 最大的那个商品

            # 1. 获取所有商品卡片候选
            # 为了效率，直接重新解析或利用现有结构。
            # 由于 _collect_products_by_structure 比较复杂，这里简化逻辑：
            # 查找所有价格元素，向上找商品名，记录 (Y, Name)

            import xml.etree.ElementTree as ET
            import re

            # 这里的 ui_nodes 是扁平化的，结构化查找需要完整树
            # 我们可以直接再次调用 get_page_source 吗？会有性能开销。
            # 但 ui_nodes 已经丢失了树形结构（只保留了部分属性）。
            # 幸运的是，_run 循环里已经获取了 xml_content，但这里拿不到。
            # 我们只能重新获取或传入。
            # 考虑到 _collect_all_categories 里已经有了 ui_nodes (list of dict)，
            # 但 ui_nodes 不包含层级关系。
            # 必须重新获取 XML 进行精准定位（为了准确性，值得牺牲一点性能）

            xml_content = self.automator.get_page_source()
            if not xml_content:
                return ""

            if isinstance(xml_content, bytes):
                xml_content = xml_content.decode('utf-8', errors='ignore')
            xml_content = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', xml_content)
            root = ET.fromstring(xml_content)

            # 查找所有价格
            candidates = []

            def find_candidates(element, ancestors=[]):
                text = element.attrib.get('text', '')
                if re.match(r"^¥?\d+\.?\d*$", text):
                    # 这是一个价格，尝试找对应的商品名
                    price_y = self._get_center_y(element)

                    # 必须在边界上方
                    if price_y >= boundary_y:
                        return

                    # 向上寻找商品名
                    current_chain = ancestors + [element]
                    best_name = ""

                    # 向上找几层
                    for i in range(2, min(6, len(current_chain))):
                        parent = current_chain[-i]

                        # 提取该容器下所有文本
                        container_texts = []
                        def extract(elem):
                            t = elem.attrib.get('text', '').strip()
                            if t:
                                cy = self._get_center_y(elem)
                                container_texts.append({'text': t, 'y': cy})
                            for child in elem:
                                extract(child)
                        extract(parent)

                        # 找名字
                        potential_names = []
                        for item in container_texts:
                            t = item['text']
                            if t == text: continue # 跳过价格本身

                            # 名字特征：含 [ 或 【，且在价格上方
                            if ('[' in t or '【' in t) and len(t) > 5 and item['y'] < price_y:
                                # 排除干扰
                                if any(x in t for x in ["优惠", "月售", "已售", "起送"]):
                                    continue
                                potential_names.append(item)

                        if potential_names:
                            # 取最靠上的
                            potential_names.sort(key=lambda x: x['y'])
                            best_name = potential_names[0]['text']
                            break

                    if best_name:
                        cleaned_name = self._clean_product_name(best_name)
                        candidates.append({'name': cleaned_name, 'y': price_y})

                # 递归
                new_chain = ancestors + [element]
                for child in element:
                    find_candidates(child, new_chain)

            find_candidates(root)

            if not candidates:
                return ""

            # 按 Y 坐标降序排序（最大的 Y 即最接近边界线的）
            candidates.sort(key=lambda x: x['y'], reverse=True)
            return candidates[0]['name']

        except Exception as e:
            self.logger.error(f"查找锚点商品失败: {e}")
            return ""

    def _perform_retroactive_correction(self, anchor_name: str, current_category: str, next_category: str) -> int:
        """
        回溯修正：检查最近采集的记录，如果包含锚点商品，则将锚点之后的所有商品归类到 next_category

        Args:
            anchor_name: 锚点商品名（当前分类的最后一个商品）
            current_category: 当前分类（A）
            next_category: 下一分类（B）

        Returns:
            修正的记录数量
        """
        try:
            records = self.exporter.records
            if not records:
                return 0

            # 往前查8个
            search_limit = 8
            start_idx = max(0, len(records) - search_limit)

            found_idx = -1
            # 倒序查找锚点
            for i in range(len(records) - 1, start_idx - 1, -1):
                if records[i].drug_name == anchor_name:
                    found_idx = i
                    break

            if found_idx != -1:
                self.logger.info(f"🔄 [回溯修正] 在缓存中找到锚点: {anchor_name} (Index={found_idx})")

                fix_count = 0
                # 从 found_idx + 1 开始，尝试重置为 next_category
                # 关键逻辑：一旦遇到不属于 current_category 且也不属于 next_category 的商品（说明是Category C），立即停止

                for j in range(found_idx + 1, len(records)):
                    record = records[j]
                    old_cat = record.category_name

                    # 安全检查：如果该记录的分类已经是"其它分类"（既不是A也不是B），说明已经进入了C，不能覆盖
                    # 注意：如果是"A"或"B"或"未知"，我们都可以修正为B。
                    # 但如果之前已经被修正为C，或者本身采集时就是C，则必须停止。
                    if old_cat != current_category and old_cat != next_category and old_cat != "未知分类":
                        self.logger.info(f"🛑 [回溯修正] 遇到第三方分类 '{old_cat}' (商品: {record.drug_name})，停止后续修正")
                        break

                    # 执行修正
                    if old_cat != next_category:
                        record.category_name = next_category
                        fix_count += 1
                        self.logger.info(f"    -> 修正: {record.drug_name} | {old_cat} => {next_category}")

                if fix_count > 0:
                    self.logger.info(f"✅ 回溯修正完成: 修正了 {fix_count} 条记录")
                    return fix_count
            else:
                self.logger.debug(f"⚠️ [回溯修正] 最近 {search_limit} 条记录中未找到锚点: {anchor_name}")
                return 0

        except Exception as e:
            self.logger.error(f"回溯修正异常: {e}")
            return 0

    def _collect_visible_products_with_boundary(
        self,
        current_category: str,
        ui_nodes: list,
        mode: str = "NORMAL",
        divider_y: int = None,
        next_category: str = None
    ) -> tuple:
        """
        采集当前可见区域的商品（支持边界模式）
        代理方法：直接调用结构化采集
        """
        # 兼容性处理
        dy = divider_y if divider_y is not None else 0
        nc = next_category if next_category is not None else ""
        return self._collect_products_by_structure(current_category, mode, dy, nc)

    def _collect_visible_products(self, category_name: str, ui_nodes: list = None) -> int:
        """
        采集当前可见区域的商品（兼容接口）
        策略：以价格元素(¥XX.XX)为锚点定位商品卡片，通过坐标关联查找商品名

        Args:
            category_name: 当前分类名
            ui_nodes: 预解析的UI节点列表（如果提供则直接使用，否则查询设备）
        """
        # 向后兼容：调用新函数的NORMAL模式
        if ui_nodes is None:
            self.logger.warning("未传入ui_nodes，_collect_visible_products 性能将受限")
            return 0

        new_count, _ = self._collect_visible_products_with_boundary(
            category_name, ui_nodes, "NORMAL"
        )
        return new_count

    def _collect_visible_products_legacy(self, category_name: str, ui_nodes: list = None) -> int:
        """
        采集当前可见区域的商品（原始逻辑，保留用于降级）
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
            
            # === 准备数据源 ===
            price_items = []
            text_items = []
            
            if ui_nodes is not None:
                # 使用本地节点
                for node in ui_nodes:
                    text = node.get('text', '')
                    if not text: continue
                    
                    bounds = node.get('bounds')
                    if not bounds: continue
                    
                    center_x = bounds['center_x']
                    center_y = bounds['center_y']
                    
                    # 区域过滤
                    if not (product_area_min_x < center_x < product_area_max_x and
                            product_area_min_y < center_y < product_area_max_y):
                        continue
                    
                    # 识别价格
                    if re.match(r"^¥?\d+\.?\d*$", text):
                        price_items.append({
                            'text': text.replace('¥', '').replace('￥', ''),
                            'x': center_x,
                            'y': center_y,
                            'top': bounds['top'],
                            'bottom': bounds['bottom'],
                            'left': bounds['left'],
                            'right': bounds['right']
                        })
                    
                    # 收集所有文本（用于匹配商品名）
                    if len(text.strip()) >= 2:
                        text_items.append({
                            'text': text.strip(),
                            'x': center_x,
                            'y': center_y,
                            'top': bounds['top'],
                            'bottom': bounds['bottom']
                        })
            else:
                # 原有逻辑：查询设备（保留作为兼容，虽然本优化方案中不会用到）
                # ... (为了保持代码整洁，这里省略原有逻辑的完整复制，
                # 实际上如果 ui_nodes 为 None，应该走原有逻辑，但为了优化，
                # 我们假设调用方总是会传入 ui_nodes，或者在这里抛出警告)
                self.logger.warning("未传入ui_nodes，_collect_visible_products 性能将受限")
                return 0
            
            if not price_items:
                return 0
            
            self.logger.debug(f"找到 {len(price_items)} 个价格元素")
            
            # === 第三步：全新重构 - 基于结构特征的匹配 ===
            # 策略：商品名([开头) -> 月售(中间) -> 价格(底部)

            # 1. 识别所有可能的商品名（必须以 [ 或 【 开头）
            product_name_candidates = []
            for item in text_items:
                text = item['text']
                if text.startswith('[') or text.startswith('【'):
                    product_name_candidates.append(item)

            # 2. 为每个价格寻找匹配的商品名
            for price_item in price_items:
                price_text = price_item['text']
                price_y = price_item['y']
                price_x = price_item['x']

                # 在价格上方寻找最近的一个合法商品名
                best_name_item = None
                min_y_dist = float('inf')

                for name_item in product_name_candidates:
                    name_y = name_item['y']
                    name_x = name_item['x']

                    # 必须在价格上方
                    if name_y >= price_y:
                        continue

                    # 水平偏差不能太大 (同列)
                    if abs(name_x - price_x) > 300:
                        continue

                    # 计算垂直距离
                    dist = price_y - name_y

                    # 距离限制 (放宽到 600px，确保能跨过营销标签)
                    if dist > 600:
                        continue

                    # 找离价格最近的那个 [商品名] (通常只有一个，如果有多个，最近的应该是所属关系)
                    # 修正：通常商品名在卡片顶部，价格在底部。中间可能有其他[标签]。
                    # 但根据用户反馈，"药品名是[开头的...跟左侧图片顶部平齐"。
                    # 我们寻找价格上方最近的那个“合法头部”。
                    if dist < min_y_dist:
                        min_y_dist = dist
                        best_name_item = name_item

                if not best_name_item:
                    continue

                best_name = best_name_item['text']
                name_y = best_name_item['y']

                # === 查找月售信息 (在商品名和价格之间的区域) ===
                monthly_sales = "0"
                for text_item in text_items:
                    text = text_item['text']
                    tx = text_item['x']
                    ty = text_item['y']

                    # 必须在商品名和价格之间
                    if not (name_y < ty < price_y):
                        continue

                    # 水平位置限制
                    if abs(tx - price_x) > 350:
                        continue

                    # 匹配月售/已售
                    if '月售' in text or '已售' in text:
                        match = re.search(r'(?:月售|已售)\s*(\d+)', text)
                        if match:
                            monthly_sales = match.group(1)
                            break # 找到即止

                # 清理商品名
                best_name = self._clean_product_name(best_name)

                # === 根据模式确定商品归属分类 ===
                
                # === 去重检查并保存 ===
                # generate_key 必须包含 shop_name（从 state_store 获取）
                shop_name = self.state_store.state.get("current_shop_name", "")
                key = self.state_store.generate_key(shop_name, category_name, best_name, price_text)
                
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
        清理商品名中的前缀乱码和营销标签
        例如:
        - TTTTT[力度伸]维生素C... -> [力度伸]维生素C...
        - 健康年 [健安适]... -> [健安适]...
        """
        import re

        if not name:
            return name

        # 移除常见的干扰前缀 (根据用户反馈添加 "健康年")
        prefixes_to_remove = ["健康年"]
        for prefix in prefixes_to_remove:
            if prefix in name:
                name = name.replace(prefix, "").strip()

        # 查找第一个方括号或中文字符的位置
        # 商品名通常以 [品牌名] 或中文开头
        match = re.search(r'[\[\u4e00-\u9fa5]', name)

        if match:
            # 如果找到了 [ 或 【，直接从这里开始截取
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
