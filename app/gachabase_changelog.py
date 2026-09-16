import json
import re
import requests

def fetch_and_parse_json(url):
    print(f"🌐 データをダウンロード中: {url}")
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        
        response.encoding = 'utf-8'
        content = response.text
    except Exception as e:
        print(f"❌ ダウンロードエラー: {e}")
        return None

    decoder = json.JSONDecoder()
    idx = 0
    objects = []
    while idx < len(content):
        while idx < len(content) and content[idx].isspace():
            idx += 1
        if idx >= len(content):
            break
        try:
            obj, idx = decoder.raw_decode(content, idx)
            objects.append(obj)
        except json.JSONDecodeError as e:
            print(f"JSONデコードエラー: {e}")
            break
    return objects

def resolve_item(data_array, item, visited=None):
    if visited is None:
        visited = set()
    
    if isinstance(item, int):
        if 0 <= item < len(data_array):
            if item in visited:
                return f"__CYCLE_REF_{item}__"
            local_visited = visited.copy()
            local_visited.add(item)
            resolved = data_array[item]
            return resolve_item(data_array, resolved, local_visited)
        return item
    elif isinstance(item, list):
        return [resolve_item(data_array, v, visited) for v in item]
    elif isinstance(item, dict):
        return {k: resolve_item(data_array, v, visited) for k, v in item.items()}
    else:
        return item

def clean_id(raw_id, is_character=False):
    if isinstance(raw_id, dict):
        base_id = raw_id.get("id") or raw_id.get("text")
        suffix = raw_id.get("suffix") or raw_id.get("subId") or raw_id.get("value")
        if base_id and suffix:
            return f"{base_id}-{suffix}"
        return base_id or str(raw_id)
    elif isinstance(raw_id, list):
        return "-".join(str(x) for x in raw_id)
    
    raw_str = str(raw_id) if raw_id is not None else ""
    
    if is_character and len(raw_str) >= 9 and raw_str.isdigit():
        return f"{raw_str[:8]}-{raw_str[8:]}"
        
    return raw_id

def normalize_item(item, is_character=False):
    if not isinstance(item, dict):
        return None
        
    item_id = clean_id(item.get("id"), is_character=is_character)

    name_obj = item.get("name")
    if isinstance(name_obj, dict):
        name = name_obj.get("text") or name_obj.get("name") or name_obj.get("value") or ""
    else:
        name = name_obj if name_obj else ""
        
    return {
        "id": item_id,
        "name": name,
        "status": item.get("status", "unknown"),
        "href": item.get("href", ""),
        "diff": item.get("diff", ""),
    }

def main():
    target_url = "https://gi.gachabase.net/changelog/beta/__data.json?lang=ja"

    objects = fetch_and_parse_json(target_url)
    if not objects:
        print("❌ データの取得に失敗したため処理を中断します。")
        return

    chunks_data = None
    for obj in objects:
        if obj.get("type") == "chunk":
            chunks_data = obj.get("data", [])
            break
        if "chunks" in obj:
            chunks = obj["chunks"]
            if chunks and isinstance(chunks, list) and len(chunks) > 0:
                chunks_data = chunks[0].get("data", [])
                break
        if "data" in obj and isinstance(obj["data"], list):
            chunks_data = obj["data"]
            break

    if not chunks_data:
        print("エラー: チャンクデータが見つかりません。")
        return

    revisions_ref = None
    for item in chunks_data:
        if isinstance(item, list) and len(item) >= 1 and isinstance(item[0], int):
            revisions_ref = item
            break

    if not revisions_ref:
        print("エラー: revisions 配列が見つかりません。")
        return

    latest_rev_index = revisions_ref[-1]
    
    print("データを完全に展開中...")
    full_rev_obj = resolve_item(chunks_data, latest_rev_index)

    if not isinstance(full_rev_obj, dict):
        print("エラー: リビジョンの展開に失敗しました。")
        return

    version = "unknown"
    characters_raw = full_rev_obj.get("characters", [])
    weapons_raw = full_rev_obj.get("weapons", [])
    
    sample_items = (characters_raw[:5] if isinstance(characters_raw, list) else []) + (weapons_raw[:5] if isinstance(weapons_raw, list) else [])
    for item in sample_items:
        resolved_item_tmp = resolve_item(chunks_data, item)
        if isinstance(resolved_item_tmp, dict) and "href" in resolved_item_tmp:
            href_text = resolve_item(chunks_data, resolved_item_tmp["href"])
            if isinstance(href_text, str):
                v_match = re.search(r'/beta/([^/]+)/', href_text)
                if v_match:
                    version = v_match.group(1)
                    break

    def find_version_string(target):
        if isinstance(target, str) and re.match(r'^\d+\.\d+\.\d+$', target):
            return target
        elif isinstance(target, list):
            for v in target:
                res = find_version_string(v)
                if res != "unknown": return res
        elif isinstance(target, dict):
            for v in target.values():
                res = find_version_string(v)
                if res != "unknown": return res
        return "unknown"

    if version == "unknown":
        version = find_version_string(full_rev_obj)

    if version == "unknown":
        for val in chunks_data:
            if isinstance(val, str) and re.match(r'^\d+\.\d+\.\d+$', val):
                version = val
                break

    def normalize_list(items, is_character=False):
        if not isinstance(items, list):
            return []
        result = []
        for item in items:
            norm = normalize_item(item, is_character=is_character)
            if norm:
                result.append(norm)
        return result

    result = {
        "version": version,
        "characters": normalize_list(characters_raw, is_character=True),
        "weapons": normalize_list(weapons_raw, is_character=False),
        "artifacts": normalize_list(full_rev_obj.get("artifacts", []), is_character=False),
    }

    return result

if __name__ == "__main__":
    main()