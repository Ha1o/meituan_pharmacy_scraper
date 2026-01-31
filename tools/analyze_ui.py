import uiautomator2 as u2
import xml.etree.ElementTree as ET
import re
import sys

def get_bounds(node):
    bounds_str = node.get('bounds', '')
    match = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
    if match:
        left, top, right, bottom = map(int, match.groups())
        return {
            'left': left, 'top': top, 'right': right, 'bottom': bottom,
            'center_y': (top + bottom) // 2,
            'height': bottom - top
        }
    return None

def print_node(node, depth=0, prefix=""):
    indent = "  " * depth
    text = node.get('text', '').strip()
    resource_id = node.get('resource-id', '').split('/')[-1]
    class_name = node.get('class', '').split('.')[-1]
    bounds = node.get('bounds', '')

    info = []
    if text: info.append(f"Text='{text}'")
    if resource_id: info.append(f"ID={resource_id}")
    info.append(f"Class={class_name}")
    info.append(f"Bounds={bounds}")

    print(f"{indent}{prefix}Node: {', '.join(info)}")

def analyze_structure():
    try:
        print("正在连接设备...")
        d = u2.connect()
        print(f"设备已连接: {d.info.get('productName')}")

        print("正在获取页面结构...")
        xml = d.dump_hierarchy()
        root = ET.fromstring(xml)

        # 查找所有价格节点作为锚点
        price_nodes = []

        def find_prices(node, path):
            text = node.get('text', '')
            # 匹配价格 ¥12.34 或 12.34
            if re.match(r'^[¥￥]?\d+(\.\d+)?$', text):
                # 排除左侧导航栏 (假设 X < 200)
                b = get_bounds(node)
                if b and b['left'] > 250:
                    price_nodes.append((node, path))

            new_path = path + [node]
            for child in node:
                find_prices(child, new_path)

        find_prices(root, [])

        print(f"\n找到 {len(price_nodes)} 个商品价格节点，开始分析结构:\n")

        for i, (price_node, ancestors) in enumerate(price_nodes):
            price_text = price_node.get('text')
            print(f"=== 商品 {i+1} (价格: {price_text}) ===")

            # 向上追溯3层父节点
            # 通常结构: ... -> RecyclerView -> ViewGroup(卡片) -> ViewGroup(信息区) -> TextView(价格)
            # 我们打印最后 3 层祖先及其所有子节点，看看谁和价格在一起

            # 倒数第2个是直接父节点，倒数第3个是爷爷节点...
            start_index = max(0, len(ancestors) - 4)
            relevant_ancestors = ancestors[start_index:]

            for depth, ancestor in enumerate(relevant_ancestors):
                b = get_bounds(ancestor)
                print(f"\n[层级 -{len(relevant_ancestors)-depth}] 父容器 (Bounds: {ancestor.get('bounds')})")

                # 打印该父容器下的所有文本子节点
                child_texts = []
                def get_all_texts(elem, d=0):
                    t = elem.get('text', '').strip()
                    if t:
                        b_child = get_bounds(elem)
                        y = b_child['center_y'] if b_child else 0
                        child_texts.append({'text': t, 'y': y, 'depth': d})
                    for child in elem:
                        get_all_texts(child, d+1)

                get_all_texts(ancestor)

                # 按Y坐标排序打印
                child_texts.sort(key=lambda x: x['y'])
                for item in child_texts:
                    prefix = "  >> "
                    if item['text'] == price_text:
                        prefix = "  $$ [价格] "
                    elif item['text'].startswith('[') or item['text'].startswith('【'):
                        prefix = "  ## [商品名?] "
                    elif "月售" in item['text']:
                        prefix = "  ** [销量] "

                    print(f"{prefix}{item['text']} (Y={item['y']})")

            print("\n" + "-"*50 + "\n")

    except Exception as e:
        print(f"发生错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    analyze_structure()
