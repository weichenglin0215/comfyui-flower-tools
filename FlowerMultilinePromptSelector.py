import os
import json
import re
import random
from server import PromptServer
from aiohttp import web


def _parse_units(content):
    """
    解析文字內容為獨立單位：
    - 區塊起始：`{` 必須在「行首」（strip 後以 `{` 開頭）
    - 區塊結束：`}` 必須在「行尾」（strip 後以 `}` 結尾，可附加 `,`/`，`，全形半形皆可），不必在行首
    - 支援巢狀：內層 `{...}` 仍依同規則計算深度，整個巢狀區塊視為單一單位
    - 區塊以外的非空行各自為一個獨立單位
    最外層的 `{`/`}` 標記不會包含在輸出單位中；內層巢狀的括號則原樣保留
    """
    units = []
    lines = content.splitlines()
    end_re = re.compile(r'[}｝][,，]?\s*$')   # 行尾結束符（半形/全形皆可）
    trim_end_re = re.compile(r'[}｝][,，]?\s*$')  # 用於去除最外層收尾 }
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith('{'):
            # 區塊起始
            depth = 1
            block_parts = []
            # 處理起始行 `{` 後面的同行內容
            first_rest = stripped[1:]
            if end_re.search(first_rest):
                # 同一行就出現最外層結尾 }（單行區塊）
                depth -= 1
                first_rest = trim_end_re.sub('', first_rest).rstrip()
            first_rest = first_rest.strip()
            if first_rest:
                block_parts.append(first_rest)
            i += 1
            # 持續收集直到 depth 歸 0
            while depth > 0 and i < len(lines):
                line = lines[i]
                line_stripped = line.strip()
                # 行首 `{` 提升深度（內層巢狀區塊）
                if line_stripped.startswith('{'):
                    depth += 1
                # 行尾 `}` 降低深度
                if end_re.search(line_stripped):
                    depth -= 1
                if depth == 0:
                    # 此行為最外層收尾，需移除尾端 } 並保留前面可能存在的內容
                    trimmed = trim_end_re.sub('', line).rstrip()
                    if trimmed.strip():
                        block_parts.append(trimmed)
                    i += 1
                    break
                block_parts.append(line)
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


# 🌸 支援的文字檔副檔名（.txt 與 .md 皆可讀取）🌸
SUPPORTED_EXTS = (".txt", ".md")


def _build_file_list(base_dir, configs):
    """
    依照 configs 中 key 的順序建立有序的檔案清單，
    configs 中未包含的剩餘文字檔（.txt / .md）以 ASCII 優先排序補在後面
    """
    ordered_files = [
        fn for fn in configs.keys()
        if fn.endswith(SUPPORTED_EXTS) and os.path.exists(os.path.join(base_dir, fn))
    ]

    def ascii_first_key(f):
        is_ascii = ord(f[0]) < 128 if f else True
        parts = re.split(r'(\d+)', f)
        parts = [int(p) if p.isdigit() else p for p in parts]
        return (0 if is_ascii else 1, parts)

    remaining = [f for f in os.listdir(base_dir) if f.endswith(SUPPORTED_EXTS) and f not in configs]
    remaining.sort(key=ascii_first_key)
    return ordered_files + remaining


# 🌸 輸出模式常數 🌸
MODE_COMBINATION = "組合模式 Combination"
MODE_SEQUENTIAL = "循序模式 Sequential"
MODE_WHOLE_FILE = "依序輸出整個文字檔 Whole File"
OUTPUT_MODE_OPTIONS = [MODE_COMBINATION, MODE_SEQUENTIAL, MODE_WHOLE_FILE]


def _normalize_output_mode(value):
    """
    將 output_mode 標準化為三種模式字串。
    向後相容：舊版工作流程可能存為 bool（True=循序、False=組合）。
    """
    if isinstance(value, bool):
        return MODE_SEQUENTIAL if value else MODE_COMBINATION
    if isinstance(value, str) and value in OUTPUT_MODE_OPTIONS:
        return value
    return MODE_COMBINATION


def _compute_selection(base_dir, configs, files, process_idx, output_mode):
    """
    依照目前設定計算選取結果（含原始 /*...*/ 註解，不做刪除）。
    供 select_multiline_prompt 與 preview-selection API 共用，避免邏輯重複。
    """
    mode = _normalize_output_mode(output_mode)

    # 🌸 模式三：依序輸出整個文字檔 🌸
    # 只要狀態不是 disabled 就視為啟用，按 process_idx 依序輪流輸出整個檔案內容
    if mode == MODE_WHOLE_FILE:
        enabled_files = [
            fn for fn in files
            if configs.get(fn, {"status": "disabled"}).get("status", "disabled") != "disabled"
        ]
        if not enabled_files:
            return ""
        target = enabled_files[process_idx % len(enabled_files)]
        try:
            with open(os.path.join(base_dir, target), "r", encoding="utf-8") as f:
                return f.read()
        except:
            return ""

    if mode == MODE_SEQUENTIAL:  # 循序模式：所有檔案的單位合併成全域池後依序取出
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
                # Index 3: output_mode（下拉式選單：組合 / 循序 / 整個文字檔）
                "output_mode": (OUTPUT_MODE_OPTIONS, {"default": MODE_COMBINATION}),
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
        f_list = [f for f in os.listdir(base_dir) if f.endswith(SUPPORTED_EXTS)]
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
    # output_mode 可能是字串（新版下拉選單）或 bool（舊版相容）
    output_mode          = _normalize_output_mode(data.get("output_mode", MODE_COMBINATION))
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
