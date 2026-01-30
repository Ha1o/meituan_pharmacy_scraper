"""
美团药房数据采集工具
主程序入口

多设备并发控制安卓手机，采集美团App看病买药页面的药品数据
"""
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------
# 关键修复：在导入其他依赖ADB的模块前，先配置环境变量
# ---------------------------------------------------------
def setup_adb_env():
    """配置ADB环境变量"""
    # 确定应用根目录
    if getattr(sys, 'frozen', False):
        # 打包后的 exe 所在目录 (例如: .../meituan_pharmacy_demo/app)
        app_root = os.path.dirname(sys.executable)
    else:
        # 开发环境下的脚本所在目录
        app_root = os.path.dirname(os.path.abspath(__file__))
    
    # 寻找 adb 目录
    # 1. 开发环境: 在当前目录下 (app_root/adb)
    # 2. 打包环境: 在上级目录下 (app_root/../adb)
    adb_candidates = [
        os.path.join(app_root, "adb"),
        os.path.abspath(os.path.join(app_root, "..", "adb"))
    ]
    
    adb_found = False
    for adb_path in adb_candidates:
        if os.path.exists(adb_path) and os.path.isdir(adb_path):
            # 将 adb 目录添加到 PATH 最前面
            os.environ["PATH"] = adb_path + os.pathsep + os.environ["PATH"]
            print(f"[Init] Added bundled ADB to PATH: {adb_path}")
            adb_found = True
            break
            
    if not adb_found:
        print("[Init] Warning: Bundled ADB not found in candidates. Will use system ADB if available.")

# 执行环境配置
setup_adb_env()
# ---------------------------------------------------------

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from ui.main_window import MainWindow


def main():
    """主函数"""
    # 启用高DPI支持
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    
    # 确定应用根目录 (用于传入MainWindow)
    if getattr(sys, 'frozen', False):
        APP_ROOT = os.path.dirname(sys.executable)
    else:
        APP_ROOT = os.path.dirname(os.path.abspath(__file__))
    
    # 创建应用
    app = QApplication(sys.argv)
    app.setApplicationName("美团药房数据采集工具")
    app.setOrganizationName("PharmacyScraper")
    
    # 设置应用样式
    app.setStyle("Fusion")
    
    # 创建主窗口，传入 APP_ROOT
    window = MainWindow(app_root=APP_ROOT)
    window.show()
    
    # 运行事件循环
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
