import os
import json
import re
import random
from server import PromptServer
from aiohttp import web


def _parse_units(content):
    """
    解析文字內容為獨立單位：
    - 以 { 開頭的行開始一個多行區塊，直到單獨的 }、},（全形半形皆可）結束，整個區塊為一個單位
    - 區塊以外的非空行各自為一個獨立單位
    """
    units = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith('{'):
            # 多行區塊開始，{ 後面同行可能還有內容
            block_parts = []
            after_brace = stripped[1:].strip()
            if after_brace:
                block_parts.append(after_brace)
            i += 1
            # 收集直到結尾符號 }、},（半形/全形皆可）
            while i < len(lines):
                block_stripped = lines[i].strip()
                if re.match(r'^[}｝][,，]?\s*$', block_stripped):
                    i += 1
                    break
                block_parts.append(lines[i])
                i += 1
            # EOF 未遇到結尾符號時，仍將已收集內容作為單位輸出
            unit = '\n'.join(block_parts).strip()
            if unit:
                units.append(unit)
        elif stripped:
            # 非空行，作為獨立單位
            units.append(stripped)
            i += 1
        else:
            i += 1
    return units


def _strip_comments(text):
    """移除 /*...*/ 格式的註解（包括符號本身與其間所有文字，支援跨行）"""
    stripped = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    # 移除連續三行以上的空白行，整理成最多兩行
    stripped = re.sub(r'\n{3,}', '\n\n', stripped)
    return stripped.strip()


def _resolve_base_dir(directory):
    """將輸入的目錄字串解析成完整的絕對路徑"""
    base_dir = directory.strip()
    default_dir = os.path.join(os.path.dirname(__file__), "wildcards")
    if not base_dir:
        return default_dir
    if os.path.isabs(base_dir) or ":" in base_dir:
        return base_dir
    return os.path.abspath(os.path.join(default_dir, base_dir))


def _build_file_list(base_dir, configs):
    """
    依照 configs 中 key 的順序建立有序的檔案清單，
    configs 中未包含的剩餘 .txt 檔以 ASCII 優先排序補在後面
    """
    ordered_files = [
        fn for fn in configs.keys()
        if fn.endswith(".txt") and os.path.exists(os.path.join(base_dir, fn))
    ]

    def ascii_first_key(f):
        is_ascii = ord(f[0]) < 128 if f else True
        parts = re.split(r'(\d+)', f)
        parts = [int(p) if p.isdigit() else p for p in parts]
        return (0 if is_ascii else 1, parts)

    remaining = [f for f in os.listdir(base_dir) if f.endswith(".txt") and f not in configs]
    remaining.sort(key=ascii_first_key)
    return ordered_files + remaining


def _compute_selection(base_dir, configs, files, process_idx, output_mode):
    """
    依照目前設定計算選取結果（含原始 /*...*/ 註解，不做刪除）。
    供 select_multiline_prompt 與 preview-selection API 共用，避免邏輯重複。
    """
    if output_mode:  # 循序模式：所有檔案的單位合併成全域池後依序取出
        global_pool = []
        for filename in files:
            file_cfg = configs.get(filename, {"status": "disabled"})
            status = file_cfg.get("status", "disabled")
            if status == "disabled":
                continue
            filepath = os.path.join(base_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    units = _parse_units(f.read())
                if not units:
                    continue
                if status == "selected":
                    unit = file_cfg.get("selected_line", "")
                    if unit:
                        global_pool.append(unit)
                elif status == "ordered":
                    global_pool.extend(units)
                elif status == "random":
                    temp_units = units.copy()
                    random.Random(process_idx).shuffle(temp_units)
                    global_pool.extend(temp_units)
            except:
                continue
        if not global_pool:
            return ""
        return global_pool[process_idx % len(global_pool)]
    else:  # 組合模式：每個檔案各取一個單位，以逗號組合
        combined_parts = []
        for filename in files:
            file_cfg = configs.get(filename, {"status": "disabled"})
            status = file_cfg.get("status", "disabled")
            if status == "disabled":
                continue
            filepath = os.path.join(base_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    units = _parse_units(f.read())
                if not units:
                    continue
                if status == "selected":
                    unit = file_cfg.get("selected_line", "")
                    if unit:
                        combined_parts.append(unit)
                elif status == "ordered":
                    combined_parts.append(units[process_idx % len(units)])
                elif status == "random":
                    temp_units = units.copy()
                    random.Random(process_idx).shuffle(temp_units)
                    combined_parts.append(temp_units[process_idx % len(temp_units)])
            except:
                continue
        return ", ".join(combined_parts)


class FlowerMultilinePromptSelector:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                # Index 0: directory
                "directory": ("STRING", {"default": ""}),
                # Index 1 & 2: seed & continuous_processing
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                "continuous_processing": ("INT", {"default": 1, "min": 1, "max": 9999999}),
                # Index 3: output_mode
                "output_mode": ("BOOLEAN", {"default": False, "label_on": "循序模式(Sequential)", "label_off": "組合模式(Combination)"}),
                # Index 4: remove_comments（預設勾選，輸出時刪除 /**/ 註解）
                "remove_comments": ("BOOLEAN", {"default": True, "label_on": "輸出時刪除/**/註解", "label_off": "輸出保留/**/註解"}),
                # Index 5: file_configs
                "file_configs": ("STRING", {"default": "{}", "multiline": True}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "select_multiline_prompt"
    CATEGORY = "flower-tools"
    OUTPUT_NODE = True

    def select_multiline_prompt(self, directory, seed, continuous_processing, output_mode, remove_comments, file_configs="{}"):
        base_dir = _resolve_base_dir(directory)

        if not os.path.exists(base_dir):
            return {"ui": {"text": ["Error: Dir not found"]}, "result": ("Error",)}

        try:
            configs = json.loads(file_configs)
        except:
            configs = {}

        try:
            files = _build_file_list(base_dir, configs)
        except Exception as e:
            return {"ui": {"text": [str(e)]}, "result": ("Error",)}

        # 🌸 核心運算邏輯: seed 除以 continuous_processing 取整數後作為索引 🌸
        process_idx = seed // max(1, continuous_processing)
        result = _compute_selection(base_dir, configs, files, process_idx, output_mode)

        # 節點內預覽顯示完整原文（含 /* */ 註解符號）
        # 最終輸出依 remove_comments 設定決定是否刪除 /* */ 註解
        output = _strip_comments(result) if remove_comments else result
        return {"ui": {"text": [result]}, "result": (output,)}


# --- API ---
@PromptServer.instance.routes.get("/flower-tools/list-files")
async def list_files(request):
    directory = request.query.get("directory", "").strip()
    base_dir = _resolve_base_dir(directory)

    if not os.path.isdir(base_dir):
        return web.json_response({"error": "Directory not found", "path": base_dir}, status=404)

    def sort_key(f):
        is_ascii = ord(f[0]) < 128 if f else True
        parts = re.split(r'(\d+)', f)
        parts = [int(p) if p.isdigit() else p for p in parts]
        return (0 if is_ascii else 1, parts)

    files = []
    try:
        f_list = [f for f in os.listdir(base_dir) if f.endswith(".txt")]
        f_list.sort(key=sort_key)
        for f in f_list:
            path = os.path.join(base_dir, f)
            try:
                with open(path, "r", encoding="utf-8") as file:
                    # 以解析後的單位數量作為 count，而非行數
                    count = len(_parse_units(file.read()))
                files.append({"name": f, "count": count})
            except:
                pass
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

    return web.json_response({"files": files})


@PromptServer.instance.routes.get("/flower-tools/get-file-content")
async def get_file_content(request):
    directory = request.query.get("directory", "").strip()
    filename = request.query.get("filename", "")
    base_dir = _resolve_base_dir(directory)

    path = os.path.join(base_dir, filename)
    if not os.path.isfile(path):
        return web.json_response({"error": "File not found", "path": path}, status=404)

    try:
        with open(path, "r", encoding="utf-8") as f:
            # 回傳解析後的單位清單（每個元素可能是多行文字）
            lines = _parse_units(f.read())
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

    return web.json_response({"lines": lines})


@PromptServer.instance.routes.post("/flower-tools/preview-selection")
async def preview_selection(request):
    """
    即時預覽目前節點設定下的輸出結果，供 JS refresh 按鈕呼叫。
    回傳含原始 /*...*/ 註解的完整文字（與節點執行後 ui 預覽一致）。
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    directory            = data.get("directory", "")
    seed                 = int(data.get("seed", 0))
    continuous_processing = max(1, int(data.get("continuous_processing", 1)))
    output_mode          = bool(data.get("output_mode", False))
    file_configs_str     = data.get("file_configs", "{}")

    base_dir = _resolve_base_dir(directory)
    if not os.path.exists(base_dir):
        return web.json_response({"error": "Directory not found"}, status=404)

    try:
        configs = json.loads(file_configs_str)
    except:
        configs = {}

    try:
        files = _build_file_list(base_dir, configs)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

    process_idx = seed // max(1, continuous_processing)
    result = _compute_selection(base_dir, configs, files, process_idx, output_mode)
    return web.json_response({"result": result})


NODE_CLASS_MAPPINGS = { "FlowerMultilinePromptSelector": FlowerMultilinePromptSelector }
NODE_DISPLAY_NAME_MAPPINGS = { "FlowerMultilinePromptSelector": "🌸Flower Multiline Prompt Selector" }
