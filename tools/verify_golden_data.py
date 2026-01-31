import os
import pandas as pd
import glob

# === 金标准数据 (Golden Data) ===
# 来源于用户提供的三张截图
GOLDEN_DATA = [
    # --- 儿童用药 ---
    {"category": "儿童用药", "name_keyword": "仁和", "full_name": "[仁和]小儿七星茶颗粒7g*8袋/盒"},
    {"category": "儿童用药", "name_keyword": "美林", "full_name": "[美林]布洛芬混悬液(100mg:2g)*120ml/瓶/盒"},
    {"category": "儿童用药", "name_keyword": "泰诺林", "full_name": "[泰诺林]对乙酰氨基酚口服混悬液(100ml:3.2g)*120ml/瓶/盒"},

    # --- 肿瘤用药 ---
    {"category": "肿瘤用药", "name_keyword": "中国药材", "full_name": "[中国药材]鳖甲煎丸3g*30袋/盒", "sales": "1"}, # 重点检查销量
    {"category": "肿瘤用药", "name_keyword": "郝其军", "full_name": "[郝其军]复方皂矾丸0.2g*36丸*2板/盒"},
    {"category": "肿瘤用药", "name_keyword": "胡庆余堂", "full_name": "[胡庆余堂]胃复春胶囊0.35g*12粒*3板/盒"},
    {"category": "肿瘤用药", "name_keyword": "护佑", "full_name": "[护佑]枸橼酸他莫昔芬片10mg*60片/瓶/盒"},
    {"category": "肿瘤用药", "name_keyword": "信谊", "full_name": "[信谊]甲氨蝶呤片2.5mg*16片/瓶/盒"},
    {"category": "肿瘤用药", "name_keyword": "卓仑", "full_name": "[卓仑]卡培他滨片0.5g*12片/板/袋/盒"},

    # --- 惊喜回馈 ---
    {"category": "惊喜回馈", "name_keyword": "美莱欣", "full_name": "[美莱欣]透明质酸钠创面护理敷贴(SHRP-07)235*200mm/片/袋"},
    {"category": "惊喜回馈", "name_keyword": "健安适", "full_name": "[健安适]水飞蓟葛根丹参片6.4g(800mg*8片)/盒"},
    {"category": "惊喜回馈", "name_keyword": "汤臣倍健", "full_name": "[汤臣倍健]维生素C片(甜橙味)78g(780mg*100片)/瓶"}, # 重点检查名字是否被干扰
    {"category": "惊喜回馈", "name_keyword": "佳洪", "full_name": "[佳洪]博曲眠褪黑素胶囊4.2g(0.35g*12粒)/盒"},
    {"category": "惊喜回馈", "name_keyword": "B族维生素", "full_name": "[汤臣倍健]汤臣倍健B族维生素片50g(500mg*100片)/瓶", "sales": "8"},
    {"category": "惊喜回馈", "name_keyword": "大神", "full_name": "[大神]口炎清颗粒10g*10袋/包", "sales": "2"},
    {"category": "惊喜回馈", "name_keyword": "达芙文", "full_name": "[达芙文]阿达帕林凝胶0.1%*30g/管/盒", "sales": "3"},
    {"category": "惊喜回馈", "name_keyword": "易坦静", "full_name": "[易坦静]氨溴特罗口服溶液120ml/瓶/盒", "sales": "13"},
]

def find_latest_result_file():
    # 查找 output/QV.../results/ 下最新的 xlsx 文件
    search_path = os.path.join("output", "*", "results", "*.xlsx")
    files = glob.glob(search_path)

    # 过滤掉临时文件 (以 ~$ 开头)
    files = [f for f in files if not os.path.basename(f).startswith("~$")]

    if not files:
        return None
    return max(files, key=os.path.getmtime)

def verify():
    file_path = find_latest_result_file()
    if not file_path:
        print("FAILED: 未找到结果文件")
        return

    print(f"正在验证文件: {file_path}")
    try:
        df = pd.read_excel(file_path)
    except Exception as e:
        print(f"FAILED: 读取Excel失败: {e}")
        return

    print(f"数据总行数: {len(df)}")

    # 检查是否有重复
    if '商品名字' in df.columns:
        duplicates = df[df.duplicated(subset=['商品名字'], keep=False)]
        if not duplicates.empty:
            print(f"FAILED: 发现 {len(duplicates)} 条重复数据 (按商品名):")
            print(duplicates[['商品分类', '商品名字']].head())
        else:
            print("PASS: 重复性检查通过 (无重复商品名)")

    print("\n开始【金标准】逐项核对:")
    print("-" * 60)

    passed_count = 0
    total_checks = len(GOLDEN_DATA)

    for item in GOLDEN_DATA:
        keyword = item['name_keyword']
        expected_cat = item['category']
        expected_sales = item.get('sales')

        matches = df[df['商品名字'].str.contains(keyword, na=False)]

        if matches.empty:
            print(f"FAILED: 丢失: [{expected_cat}] {item['full_name']}")
            continue

        row = matches.iloc[0]
        actual_name = row['商品名字']
        actual_cat = row['商品分类']
        actual_sales = str(row['月销量']).replace('.0', '')

        cat_status = "PASS" if actual_cat == expected_cat else f"FAILED (期望: {expected_cat})"

        name_status = "PASS"
        if "热销榜" in actual_name or "TOP" in actual_name:
            name_status = "FAILED (包含干扰词)"
        elif not actual_name.startswith("[") and not actual_name.startswith("【"):
             name_status = "FAILED (格式错误，非[开头)"

        sales_status = ""
        if expected_sales:
            sales_status = f" | 销量: {actual_sales} " + ("PASS" if actual_sales == expected_sales else f"FAILED (期望: {expected_sales})")

        print(f"检查 '{keyword}':")
        print(f"   分类: {actual_cat} {cat_status}")
        print(f"   名字: {actual_name} {name_status}")
        if sales_status:
            print(f"  {sales_status}")

        if "FAILED" not in cat_status and "FAILED" not in name_status and ("FAILED" not in sales_status):
            passed_count += 1

    print("-" * 60)
    print(f"验证结果: {passed_count}/{total_checks} 通过")

    if passed_count == total_checks:
        print("完美！所有金标准数据均准确无误。")
    else:
        print("存在不一致，请检查上述错误。")

if __name__ == "__main__":
    verify()
