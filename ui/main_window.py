"""
main_window.py - PySide6 主界面
多设备控制界面，全中文显示
"""
import os
import sys
import json
from typing import Dict, Optional
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTableWidget, QTableWidgetItem,
    QTextEdit, QFileDialog, QHeaderView, QSplitter,
    QGroupBox, QProgressBar, QMessageBox, QFrame
)
from PySide6.QtCore import Qt, Signal, QObject, Slot, QTimer
from PySide6.QtGui import QColor, QFont

from core.device_manager import DeviceManager, DeviceInfo, DeviceStatus
from core.worker import DeviceWorker, WorkerStatus


class WorkerSignals(QObject):
    """Worker信号类，用于线程安全的UI更新"""
    log_signal = Signal(str, str)  # device_serial, log_message
    progress_signal = Signal(str, int, int, str, int)  # serial, current, total, category, count
    status_signal = Signal(str, object)  # serial, WorkerStatus


class MainWindow(QMainWindow):
    """主窗口"""
    
    def __init__(self):
        super().__init__()
        
        # 初始化管理器
        self.device_manager = DeviceManager()
        self.workers: Dict[str, DeviceWorker] = {}
        self.signals = WorkerSignals()
        
        # 输出目录
        self.output_dir = os.path.abspath("output")
        os.makedirs(self.output_dir, exist_ok=True)
        
        # 当前选中的设备
        self.current_device: Optional[str] = None
        
        # 设置窗口
        self.setWindowTitle("美团药房数据采集工具 - 多设备控制")
        self.setMinimumSize(1200, 700)
        
        # 初始化UI
        self._init_ui()
        
        # 连接信号
        self._connect_signals()
        
        # 刷新设备列表
        self._refresh_devices()
        
        # 定时刷新
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self._auto_refresh)
        self.refresh_timer.start(5000)  # 5秒刷新一次
    
    def _init_ui(self):
        """初始化UI"""
        # 中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 主布局
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        
        # 顶部工具栏
        toolbar = self._create_toolbar()
        main_layout.addWidget(toolbar)
        
        # 分割器：左侧设备列表 + 右侧详情
        splitter = QSplitter(Qt.Horizontal)
        
        # 左侧：设备列表
        left_panel = self._create_device_list_panel()
        splitter.addWidget(left_panel)
        
        # 右侧：设备详情
        right_panel = self._create_detail_panel()
        splitter.addWidget(right_panel)
        
        # 设置分割比例
        splitter.setSizes([500, 700])
        
        main_layout.addWidget(splitter, 1)
        
        # 底部状态栏
        self.statusBar().showMessage("就绪")
    
    def _create_toolbar(self) -> QWidget:
        """创建顶部工具栏"""
        toolbar = QFrame()
        toolbar.setFrameShape(QFrame.StyledPanel)
        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(10, 5, 10, 5)
        
        # 刷新设备按钮
        self.btn_refresh = QPushButton("🔄 刷新设备")
        self.btn_refresh.setMinimumWidth(100)
        self.btn_refresh.clicked.connect(self._refresh_devices)
        layout.addWidget(self.btn_refresh)
        
        # 输出目录选择
        layout.addWidget(QLabel("输出目录:"))
        self.lbl_output_dir = QLabel(self.output_dir)
        self.lbl_output_dir.setStyleSheet("color: #0066cc;")
        layout.addWidget(self.lbl_output_dir)
        
        btn_select_dir = QPushButton("选择...")
        btn_select_dir.clicked.connect(self._select_output_dir)
        layout.addWidget(btn_select_dir)
        
        layout.addStretch()
        
        # 加载配置判断是否显示调试功能
        enable_debug = False
        try:
            if os.path.exists("config.json"):
                with open("config.json", "r", encoding="utf-8") as f:
                    config = json.load(f)
                    enable_debug = config.get("enable_debug_features", False)
        except Exception as e:
            print(f"Error loading config: {e}")
            
        if enable_debug:
            # Mock 并发压测按钮
            self.btn_mock_test = QPushButton("🧪 Mock压测")
            self.btn_mock_test.setStyleSheet("background-color: #6f42c1; color: white; padding: 5px 10px;")
            self.btn_mock_test.clicked.connect(self._start_mock_test)
            layout.addWidget(self.btn_mock_test)
            
            # 随机扰动测试按钮
            self.btn_random_disturb = QPushButton("🎲 随机扰动")
            self.btn_random_disturb.setStyleSheet("background-color: #fd7e14; color: white; padding: 5px 10px;")
            self.btn_random_disturb.clicked.connect(self._random_disturb_test)
            self.btn_random_disturb.setToolTip("随机暂停/恢复一个Mock设备，验证线程独立性")
            layout.addWidget(self.btn_random_disturb)
        
        # 设备统计
        self.lbl_device_count = QLabel("设备: 0台在线")
        layout.addWidget(self.lbl_device_count)
        
        return toolbar
    
    def _create_device_list_panel(self) -> QWidget:
        """创建设备列表面板"""
        panel = QGroupBox("设备列表")
        layout = QVBoxLayout(panel)
        
        # 设备表格
        self.device_table = QTableWidget()
        self.device_table.setColumnCount(6)
        self.device_table.setHorizontalHeaderLabels([
            "设备序列号", "型号", "状态", "任务状态", "进度", "操作"
        ])
        
        # 设置表格属性
        header = self.device_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Fixed)
        header.resizeSection(5, 180)
        
        self.device_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.device_table.setSelectionMode(QTableWidget.SingleSelection)
        self.device_table.itemSelectionChanged.connect(self._on_device_selected)
        
        layout.addWidget(self.device_table)
        
        return panel
    
    def _create_detail_panel(self) -> QWidget:
        """创建设备详情面板"""
        panel = QGroupBox("设备详情")
        layout = QVBoxLayout(panel)
        
        # 当前设备信息
        info_layout = QHBoxLayout()
        self.lbl_current_device = QLabel("请选择一个设备")
        self.lbl_current_device.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        info_layout.addWidget(self.lbl_current_device)
        info_layout.addStretch()
        layout.addLayout(info_layout)
        
        # 任务文件
        task_layout = QHBoxLayout()
        task_layout.addWidget(QLabel("任务文件:"))
        self.lbl_task_file = QLabel("未导入")
        self.lbl_task_file.setStyleSheet("color: #666;")
        task_layout.addWidget(self.lbl_task_file, 1)
        
        self.btn_import_task = QPushButton("📂 导入xlsx任务")
        self.btn_import_task.clicked.connect(self._import_task)
        self.btn_import_task.setEnabled(False)
        task_layout.addWidget(self.btn_import_task)
        layout.addLayout(task_layout)
        
        # 控制按钮
        control_layout = QHBoxLayout()
        
        self.btn_start = QPushButton("▶ 开始")
        self.btn_start.setStyleSheet("background-color: #28a745; color: white; font-weight: bold;")
        self.btn_start.setMinimumHeight(40)
        self.btn_start.clicked.connect(self._start_task)
        self.btn_start.setEnabled(False)
        control_layout.addWidget(self.btn_start)
        
        self.btn_pause = QPushButton("⏸ 暂停")
        self.btn_pause.setStyleSheet("background-color: #ffc107; color: black; font-weight: bold;")
        self.btn_pause.setMinimumHeight(40)
        self.btn_pause.clicked.connect(self._pause_task)
        self.btn_pause.setEnabled(False)
        control_layout.addWidget(self.btn_pause)
        
        self.btn_resume = QPushButton("▶ 继续")
        self.btn_resume.setStyleSheet("background-color: #17a2b8; color: white; font-weight: bold;")
        self.btn_resume.setMinimumHeight(40)
        self.btn_resume.clicked.connect(self._resume_task)
        self.btn_resume.setEnabled(False)
        control_layout.addWidget(self.btn_resume)
        
        self.btn_stop = QPushButton("⏹ 停止")
        self.btn_stop.setStyleSheet("background-color: #dc3545; color: white; font-weight: bold;")
        self.btn_stop.setMinimumHeight(40)
        self.btn_stop.clicked.connect(self._stop_task)
        self.btn_stop.setEnabled(False)
        control_layout.addWidget(self.btn_stop)
        
        layout.addLayout(control_layout)
        
        # 进度信息
        progress_group = QGroupBox("当前进度")
        progress_layout = QVBoxLayout(progress_group)
        
        progress_info = QHBoxLayout()
        self.lbl_task_progress = QLabel("任务: 0/0")
        progress_info.addWidget(self.lbl_task_progress)
        self.lbl_category = QLabel("分类: -")
        progress_info.addWidget(self.lbl_category)
        self.lbl_collected = QLabel("已采集: 0条")
        progress_info.addWidget(self.lbl_collected)
        progress_info.addStretch()
        progress_layout.addLayout(progress_info)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)
        
        layout.addWidget(progress_group)
        
        # 日志区域
        log_group = QGroupBox("运行日志")
        log_layout = QVBoxLayout(log_group)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        self.log_text.setStyleSheet("background-color: #1e1e1e; color: #dcdcdc;")
        log_layout.addWidget(self.log_text)
        
        # 日志操作按钮
        log_btn_layout = QHBoxLayout()
        btn_clear_log = QPushButton("清空日志")
        btn_clear_log.clicked.connect(lambda: self.log_text.clear())
        log_btn_layout.addWidget(btn_clear_log)
        log_btn_layout.addStretch()
        log_layout.addLayout(log_btn_layout)
        
        layout.addWidget(log_group, 1)
        
        return panel
    
    def _connect_signals(self):
        """连接信号"""
        self.signals.log_signal.connect(self._on_log_received)
        self.signals.progress_signal.connect(self._on_progress_received)
        self.signals.status_signal.connect(self._on_status_received)
    
    @Slot(str, str)
    def _on_log_received(self, device_serial: str, message: str):
        """接收日志"""
        if device_serial == self.current_device:
            self.log_text.append(message)
            # 自动滚动到底部
            scrollbar = self.log_text.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
    
    @Slot(str, int, int, str, int)
    def _on_progress_received(self, serial: str, current: int, total: int, category: str, count: int):
        """接收进度更新"""
        if serial == self.current_device:
            self.lbl_task_progress.setText(f"任务: {current + 1}/{total}")
            self.lbl_category.setText(f"分类: {category if category else '-'}")
            self.lbl_collected.setText(f"已采集: {count}条")
            
            if total > 0:
                progress = int((current + 1) / total * 100)
                self.progress_bar.setValue(progress)
        
        # 更新表格
        self._update_device_row(serial)
    
    @Slot(str, object)
    def _on_status_received(self, serial: str, status: WorkerStatus):
        """接收状态更新"""
        # 更新设备管理器
        self.device_manager.update_device_task_status(serial, status.value)
        
        # 更新表格
        self._update_device_row(serial)
        
        # 更新按钮状态
        if serial == self.current_device:
            self._update_control_buttons(status)
            
        # 如果是Mock设备完成，更新状态栏
        if status == WorkerStatus.COMPLETED:
            self.statusBar().showMessage(f"设备 {serial} 任务已完成", 3000)
    
    def _refresh_devices(self):
        """刷新设备列表"""
        devices = self.device_manager.refresh_devices()
        
        # 更新表格
        self.device_table.setRowCount(len(devices))
        
        for row, device in enumerate(devices):
            # 序列号
            self.device_table.setItem(row, 0, QTableWidgetItem(device.serial))
            
            # 型号
            self.device_table.setItem(row, 1, QTableWidgetItem(device.model or "-"))
            
            # 设备状态
            status_item = QTableWidgetItem(device.status.value)
            if device.status == DeviceStatus.ONLINE:
                status_item.setForeground(QColor("#28a745"))
            elif device.status == DeviceStatus.OFFLINE:
                status_item.setForeground(QColor("#dc3545"))
            else:
                status_item.setForeground(QColor("#ffc107"))
            self.device_table.setItem(row, 2, status_item)
            
            # 任务状态
            self.device_table.setItem(row, 3, QTableWidgetItem(device.task_status))
            
            # 进度
            self.device_table.setItem(row, 4, QTableWidgetItem(device.progress))
            
            # 操作按钮
            btn_widget = self._create_row_buttons(device.serial)
            self.device_table.setCellWidget(row, 5, btn_widget)
        
        # 更新统计
        online_count = self.device_manager.get_online_count()
        self.lbl_device_count.setText(f"设备: {online_count}台在线")
        
        self.statusBar().showMessage(f"已刷新设备列表，共{len(devices)}台设备，{online_count}台在线")
    
    def _create_row_buttons(self, serial: str) -> QWidget:
        """创建表格行操作按钮"""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        
        btn_start = QPushButton("开始")
        btn_start.setStyleSheet("background-color: #28a745; color: white; padding: 2px 8px;")
        btn_start.clicked.connect(lambda: self._quick_start(serial))
        layout.addWidget(btn_start)
        
        btn_stop = QPushButton("停止")
        btn_stop.setStyleSheet("background-color: #dc3545; color: white; padding: 2px 8px;")
        btn_stop.clicked.connect(lambda: self._quick_stop(serial))
        layout.addWidget(btn_stop)
        
        return widget
    
    def _update_device_row(self, serial: str):
        """更新单行设备信息"""
        device = self.device_manager.get_device(serial)
        if not device:
            return
        
        for row in range(self.device_table.rowCount()):
            item = self.device_table.item(row, 0)
            if item and item.text() == serial:
                self.device_table.setItem(row, 3, QTableWidgetItem(device.task_status))
                self.device_table.setItem(row, 4, QTableWidgetItem(device.progress))
                break
    
    def _auto_refresh(self):
        """自动刷新设备状态（不刷新整个列表，只更新在线状态）"""
        # 轻量级刷新
        pass
    
    def _on_device_selected(self):
        """设备选中事件"""
        selected = self.device_table.selectedItems()
        if not selected:
            self.current_device = None
            self._update_detail_panel(None)
            return
        
        row = selected[0].row()
        serial_item = self.device_table.item(row, 0)
        if serial_item:
            self.current_device = serial_item.text()
            device = self.device_manager.get_device(self.current_device)
            self._update_detail_panel(device)
    
    def _update_detail_panel(self, device: Optional[DeviceInfo]):
        """更新详情面板"""
        if not device:
            self.lbl_current_device.setText("请选择一个设备")
            self.lbl_task_file.setText("未导入")
            self.btn_import_task.setEnabled(False)
            self.btn_start.setEnabled(False)
            self.btn_pause.setEnabled(False)
            self.btn_resume.setEnabled(False)
            self.btn_stop.setEnabled(False)
            self.log_text.clear()
            return
        
        self.lbl_current_device.setText(f"设备: {device.serial} ({device.model or '未知型号'})")
        
        # 检查worker
        worker = self.workers.get(device.serial)
        if worker:
            self.lbl_task_file.setText(worker.task_loader.file_path or "未导入")
            self._update_control_buttons(worker.status)
            
            # 加载日志
            self.log_text.clear()
            for log in worker.logger.get_logs():
                self.log_text.append(log)
        else:
            self.lbl_task_file.setText("未导入")
            self.btn_import_task.setEnabled(device.status == DeviceStatus.ONLINE)
            self.btn_start.setEnabled(False)
            self.btn_pause.setEnabled(False)
            self.btn_resume.setEnabled(False)
            self.btn_stop.setEnabled(False)
            self.log_text.clear()
    
    def _update_control_buttons(self, status: WorkerStatus):
        """根据状态更新控制按钮"""
        self.btn_import_task.setEnabled(status in [WorkerStatus.IDLE, WorkerStatus.COMPLETED, WorkerStatus.STOPPED, WorkerStatus.ERROR])
        self.btn_start.setEnabled(status in [WorkerStatus.IDLE, WorkerStatus.COMPLETED, WorkerStatus.STOPPED, WorkerStatus.ERROR])
        self.btn_pause.setEnabled(status == WorkerStatus.RUNNING)
        self.btn_resume.setEnabled(status == WorkerStatus.PAUSED)
        self.btn_stop.setEnabled(status in [WorkerStatus.RUNNING, WorkerStatus.PAUSED])
    
    def _select_output_dir(self):
        """选择输出目录"""
        dir_path = QFileDialog.getExistingDirectory(self, "选择输出目录", self.output_dir)
        if dir_path:
            self.output_dir = dir_path
            self.lbl_output_dir.setText(dir_path)
    
    def _import_task(self):
        """导入任务文件"""
        if not self.current_device:
            return
        
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择任务文件", "", "Excel文件 (*.xlsx)"
        )
        
        if not file_path:
            return
        
        # 获取或创建worker
        worker = self._get_or_create_worker(self.current_device)
        
        # 加载任务
        if worker.load_tasks(file_path):
            self.lbl_task_file.setText(file_path)
            self.btn_start.setEnabled(True)
            self.statusBar().showMessage(f"已导入任务文件: {file_path}")
        else:
            QMessageBox.warning(self, "导入失败", "无法加载任务文件，请检查文件格式")
    
    def _get_or_create_worker(self, serial: str) -> DeviceWorker:
        """获取或创建worker"""
        if serial not in self.workers:
            worker = DeviceWorker(serial, self.output_dir)
            
            # 设置回调
            worker.set_log_callback(
                lambda msg: self.signals.log_signal.emit(serial, msg)
            )
            worker.set_progress_callback(
                lambda s, c, t, cat, cnt: self.signals.progress_signal.emit(s, c, t, cat, cnt)
            )
            worker.set_status_change_callback(
                lambda s, status: self.signals.status_signal.emit(s, status)
            )
            
            self.workers[serial] = worker
        
        return self.workers[serial]
    
    def _start_task(self):
        """开始任务"""
        if not self.current_device:
            return
        
        worker = self.workers.get(self.current_device)
        if worker:
            worker.start()
            self._update_control_buttons(WorkerStatus.RUNNING)
    
    def _pause_task(self):
        """暂停任务"""
        if not self.current_device:
            return
        
        worker = self.workers.get(self.current_device)
        if worker:
            worker.pause()
            self._update_control_buttons(WorkerStatus.PAUSED)
    
    def _resume_task(self):
        """继续任务"""
        if not self.current_device:
            return
        
        worker = self.workers.get(self.current_device)
        if worker:
            worker.resume()
            self._update_control_buttons(WorkerStatus.RUNNING)
    
    def _stop_task(self):
        """停止任务"""
        if not self.current_device:
            return
        
        worker = self.workers.get(self.current_device)
        if worker:
            worker.stop()
    
    def _quick_start(self, serial: str):
        """快速开始（从表格行按钮）"""
        worker = self.workers.get(serial)
        if worker and worker.task_loader.count() > 0:
            worker.start()
        else:
            # 选中设备并提示导入任务
            self._select_device_by_serial(serial)
            QMessageBox.information(self, "提示", "请先导入任务文件")
    
    def _quick_stop(self, serial: str):
        """快速停止（从表格行按钮）"""
        worker = self.workers.get(serial)
        if worker:
            worker.stop()
    
    def _select_device_by_serial(self, serial: str):
        """通过序列号选中设备"""
        for row in range(self.device_table.rowCount()):
            item = self.device_table.item(row, 0)
            if item and item.text() == serial:
                self.device_table.selectRow(row)
                break
    
    def _start_mock_test(self):
        """启动Mock并发压测"""
        from PySide6.QtWidgets import QInputDialog
        from core.task_loader import Task
        
        # 输入Mock数量
        mock_count, ok = QInputDialog.getInt(
            self, "Mock并发压测", "Mock设备数量:", 10, 1, 50, 1
        )
        if not ok:
            return
        
        self.statusBar().showMessage(f"正在启动 {mock_count} 个Mock设备并发压测...")
        
        # 为每个Mock设备创建worker并启动
        for i in range(1, mock_count + 1):
            serial = f"MOCK-{i:03d}"
            
            # 创建worker
            worker = self._get_or_create_worker(serial)
            
            # 内存中构造3个店铺任务（不需要xlsx文件）
            mock_tasks = [
                Task(index=0, poi="北京市朝阳区", shop_name=f"Mock药房{serial[-3:]}-A店", note=""),
                Task(index=1, poi="北京市海淀区", shop_name=f"Mock药房{serial[-3:]}-B店", note=""),
                Task(index=2, poi="北京市西城区", shop_name=f"Mock药房{serial[-3:]}-C店", note=""),
            ]
            worker.task_loader.tasks = mock_tasks
            
            # 启动worker
            worker.start()
        
        self.statusBar().showMessage(f"已启动 {mock_count} 个Mock设备", 5000)
        
        QMessageBox.information(
            self, "Mock压测已启动",
            f"已启动 {mock_count} 个Mock设备并发运行。\n"
            f"每个设备将采集3个模拟店铺。\n"
            f"你可以点击 '随机扰动' 按钮测试线程独立性。"
        )
    
    def _random_disturb_test(self):
        """随机扰动测试：随机暂停/恢复一个Mock设备"""
        import random
        mock_workers = [w for s, w in self.workers.items() if s.startswith("MOCK-")]
        if not mock_workers:
            self.statusBar().showMessage("没有正在运行的Mock设备", 3000)
            return
            
        worker = random.choice(mock_workers)
        if worker.status == WorkerStatus.RUNNING:
            worker.pause()
            self.statusBar().showMessage(f"🎲 扰动：已暂停 {worker.device_serial}", 2000)
        elif worker.status == WorkerStatus.PAUSED:
            worker.resume()
            self.statusBar().showMessage(f"🎲 扰动：已恢复 {worker.device_serial}", 2000)
        else:
            self.statusBar().showMessage(f"🎲 扰动：设备 {worker.device_serial} 状态为 {worker.status.value}", 2000)
            
        # 如果当前选中的正是这个设备，更新按钮
        if worker.device_serial == self.current_device:
            self._update_control_buttons(worker.status)
    
    def closeEvent(self, event):
        """关闭事件"""
        # 停止所有worker
        for worker in self.workers.values():
            if worker.status in [WorkerStatus.RUNNING, WorkerStatus.PAUSED]:
                worker.stop()
        
        # 停止定时器
        self.refresh_timer.stop()
        
        event.accept()
