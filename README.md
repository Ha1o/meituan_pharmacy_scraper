# 美团药房数据采集工具

电脑端 ADB 多线程控制安卓手机，采集美团App"看病买药"页面的药品数据。

## 功能特性

- ✅ 多设备并发：同时控制多台安卓手机，每台独立执行任务
- ✅ 可视化界面：PySide6 桌面端界面，全中文显示
- ✅ 任务导入：每台设备独立导入 xlsx 任务文件
- ✅ 暂停/继续：支持随时暂停，继续后不重复采集
- ✅ 状态持久化：JSON 文件保存采集进度，支持断点续跑
- ✅ 去重机制：基于"分类名+药品名+价格"生成唯一key
- ✅ 失败截图：控件查找失败自动截图，便于调试选择器

## 项目结构

```
meituan_pharmacy_scraper/
├── main.py                    # 程序入口
├── config.json                # 配置文件(选择器/参数)
├── requirements.txt           # Python依赖
├── README.md                  # 本文档
├── ui/
│   ├── __init__.py
│   └── main_window.py         # PySide6 主界面
├── core/
│   ├── __init__.py
│   ├── logger.py              # 日志模块
│   ├── selectors.py           # 控件选择器工具
│   ├── automator.py           # uiautomator2 封装
│   ├── task_loader.py         # xlsx 任务加载
│   ├── state_store.py         # 状态持久化
│   ├── exporter.py            # Excel 导出
│   ├── device_manager.py      # 设备管理
│   └── worker.py              # 任务执行器
├── output/                    # 输出目录(运行时生成)
│   ├── logs/                  # 设备日志
│   ├── screenshots/           # 失败截图
│   └── state/                 # 状态文件
└── examples/
    └── tasks_template.xlsx    # 示例任务文件
```

## 环境要求

- Python 3.11+
- Android 手机（开启 USB 调试）
- ADB 工具（已添加到 PATH）

## 安装步骤

### 1. 安装 Python 依赖

```bash
cd meituan_pharmacy_scraper
pip install -r requirements.txt
```

### 2. 安装 ADB 工具

下载 [Android SDK Platform Tools](https://developer.android.com/studio/releases/platform-tools)，解压后将目录添加到系统 PATH。

验证安装：
```bash
adb version
```

### 3. 连接手机

1. 手机开启"开发者选项"和"USB调试"
2. 用USB线连接电脑
3. 手机上允许USB调试授权

验证连接：
```bash
adb devices
```

应显示设备序列号和 `device` 状态。

### 4. 安装 uiautomator2 ATX Agent

首次使用需要在手机上安装 ATX Agent：

```bash
python -m uiautomator2 init
```

## 使用方法

### 1. 启动程序

```bash
python main.py
```

### 2. 基本操作

1. **刷新设备**：点击"刷新设备"按钮，更新设备列表
2. **选择设备**：点击左侧设备列表选中目标设备
3. **导入任务**：点击"导入xlsx任务"按钮，选择任务文件
4. **开始采集**：点击"开始"按钮
5. **暂停/继续**：随时暂停，点击"继续"从断点恢复
6. **查看日志**：右下角实时显示运行日志

### 3. 任务文件格式

xlsx 文件需包含以下列（第一行为表头）：

| poi | shop_name | note |
|-----|-----------|------|
| 天河区体育西路 | 好药师大药房 | 备注1 |
| 海珠区江南西 | 大参林药房 | 备注2 |

- `poi`: 定位点关键词（用于搜索地点）
- `shop_name`: 店铺名（用于搜索店铺）
- `note`: 备注（可选）

## 配置说明

编辑 `config.json` 可调整：

### 选择器配置 (selectors)

每个步骤对应多个候选选择器，按顺序尝试：

```json
"home_waimai": [
    {"text": "外卖"},
    {"textContains": "外卖"},
    {"description": "外卖"}
]
```

支持的选择器属性：
- `text`: 精确文本匹配
- `textContains`: 包含文本
- `textMatches`: 正则匹配
- `resourceId`: 控件ID
- `className`: 控件类名
- `description`: 无障碍描述

### 参数配置

```json
{
    "timeouts": {
        "default_timeout": 10,  // 默认超时(秒)
        "long_timeout": 20,
        "short_timeout": 5
    },
    "scroll": {
        "max_scroll_times": 30,     // 最大滑动次数
        "scroll_pause": 1.0,        // 滑动间隔(秒)
        "no_new_data_threshold": 2  // 连续无新数据次数
    },
    "retry": {
        "max_retries": 3,    // 最大重试次数
        "retry_delay": 2     // 重试间隔(秒)
    }
}
```

## 输出文件

### 采集结果

每个店铺生成独立的 xlsx 文件：
- 路径: `output/{店铺名}_{时间戳}.xlsx`
- 字段: 分类名、药品名、月销、价格

### 日志文件

每台设备独立日志：
- 路径: `output/logs/{设备序列号}.log`

### 失败截图

控件查找失败时自动截图：
- 路径: `output/screenshots/{设备序列号}/{时间戳}_{步骤}.png`

## 故障排除

### 控件找不到

1. 检查 `output/screenshots/` 目录的截图
2. 使用 `uiautomator2` 查看控件树：
   ```python
   import uiautomator2 as u2
   d = u2.connect("设备序列号")
   print(d.dump_hierarchy())
   ```
3. 根据实际控件属性修改 `config.json` 中的选择器

### 设备连接失败

1. 检查 USB 连接和授权
2. 重启 adb 服务：
   ```bash
   adb kill-server
   adb start-server
   ```
3. 确认 uiautomator2 已初始化：
   ```bash
   python -m uiautomator2 init
   ```

### 暂停后数据重复

程序通过去重 key 保证不重复。如果出现重复：
1. 检查 `output/state/{设备序列号}_state.json` 是否保存正确
2. 删除状态文件重新开始

## 明确不做（MVP范围外）

- ❌ 不做App更新适配（UI变化不保证兼容）
- ❌ 不做多机型适配（只保证指定机型/分辨率）
- ❌ 不做风控自动识别（出现风控需人工处理）
- ❌ 不做OCR识别（优先从控件树获取文本）

## 后续扩展点

### 1. 风控识别

在 `worker.py` 的 `_check_control()` 方法中添加验证码/滑块检测：

```python
def _check_risk_control(self) -> bool:
    # 检测是否出现验证码
    if self.selector.find_one("captcha_dialog", timeout=1):
        self.logger.warning("检测到风控验证，请人工处理")
        self.pause()
        return False
    return True
```

### 2. 精确断点续跑

当前断点精确到分类级别。如需精确到药品位置：

1. 在 `state_store.py` 添加 `last_drug_name` 字段
2. 在 `_collect_products_in_category()` 恢复时滚动到上次位置

### 3. 多机型配置

创建 `configs/` 目录，按机型存放不同配置：

```
configs/
├── default.json
├── huawei_p40.json
└── xiaomi_12.json
```

Worker 初始化时根据设备型号加载对应配置。

### 4. Appium 替代

如需更稳定的跨平台支持，可用 Appium 替代 uiautomator2：

```python
from appium import webdriver

desired_caps = {
    'platformName': 'Android',
    'deviceName': 'device_serial',
    'appPackage': 'com.sankuai.meituan',
    'appActivity': 'MainActivity'
}
driver = webdriver.Remote('http://localhost:4723/wd/hub', desired_caps)
```

## 验收标准

- [x] 连接2台手机，各自导入不同xlsx任务，能并发跑通至少1个店铺
- [x] 完成分类逐个采集并输出xlsx
- [x] 任意时刻点击"暂停"，再点"继续"，最终xlsx中不得出现重复药品记录
- [x] 失败会截图并写出清晰中文日志提示

## License

MIT License
