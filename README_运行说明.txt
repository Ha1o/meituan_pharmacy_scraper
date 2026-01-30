美团药房数据采集工具 - 运行说明
==========================================

目录结构：
- app/                  : 程序主目录，包含 exe 和配置文件
- adb/                  : Android 调试桥工具 (必须包含 adb.exe)
- run_check.bat         : 启动脚本
- README_运行说明.txt   : 本文件

运行方式：
1. 双击运行 `run_check.bat` 即可启动程序。
2. 程序启动后会自动读取 `app/config.json` 配置文件。
3. 采集结果会保存在 `app/output/` 目录下。

注意事项：
1. 请确保 `adb/` 目录下包含 `adb.exe` 及相关依赖文件。如果缺失，请从 Android SDK Platform-Tools 下载并解压到该目录。
2. 如果程序无法启动，请尝试直接进入 `app/` 目录双击 `meituan_pharmacy_scraper.exe` 查看是否有错误提示。
3. `app/output/` 目录下的文件按设备序列号隔离存储。

常见问题：
- 如果提示找不到 adb，请检查 `adb/` 目录是否完整。
- 如果无法连接手机，请确保手机已开启 USB 调试模式并连接电脑。
