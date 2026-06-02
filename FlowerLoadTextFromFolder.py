# -*- coding: utf-8 -*-
import os
import re

from server import PromptServer
from aiohttp import web


def _match_keyword_pattern(name: str, pattern: str) -> bool:
    """
    關鍵字模式比對，支援多關鍵字運算：
      '|' 為 OR 分隔（先切割，任一群組符合即為 True）
      '&' 為 AND 分隔（群組內所有關鍵字都必須符合）
      範例：'A&B|C' → (A AND B) OR C
      空白模式回傳 True（不篩選）
    """
    pattern = pattern.strip()
    if not pattern:
        return True
    name_lower = name.lower()
    for group in pattern.split('|'):
        group = group.strip()
        if not group:
            continue
        and_keywords = [kw.strip().lower() for kw in group.split('&') if kw.strip()]
        if all(kw in name_lower for kw in and_keywords):
            return True
    return False


def _get_sort_key(sort_mode):
    """根據排序模式回傳對應的排序函數。"""
    if sort_mode == "自然排序(Natural)":
        def natural_key(f):
            parts = re.split(r'(\d+)', f)
            return [int(p) if p.isdigit() else p.lower() for p in parts]
        return natural_key
    else:
        # 預設字母排序，不區分大小寫
        return lambda f: f.lower()


def _compute_segments(text, max_chars, split_symbols):
    """
    將文字依照最大字數與分段符號切成多段。

    演算法：
      從目前段落起點往後算 max_chars 個字元，再回推找最近的分段符號，
      在符號之後（含符號）切斷。若整段都沒有分段符號，強制在 max_chars 處截斷。
      切斷後跳過開頭的空白與換行，再繼續處理下一段。

    回傳 [(start, end), ...] 列表，text[start:end] 即為該段落文字。
    """
    n = len(text)

    # 整份文字不超過上限，直接當作一段
    if n <= max_chars:
        return [(0, n)]

    split_set = set(split_symbols)
    segments = []
    start = 0

    while start < n:
        # 剩餘字數在上限以內，收尾
        if (n - start) <= max_chars:
            segments.append((start, n))
            break

        # 從 start + max_chars 位置回推，找最近的合法切斷點
        search_pos = start + max_chars
        cut = -1

        # 第一輪：找「分段符號 + 緊接換行」（真正的段落邊界，如 。\n ！\n ？\n）
        for i in range(search_pos, start, -1):
            if text[i - 1] in split_set and i < n and text[i] in '\n\r':
                cut = i  # 切在符號之後、換行之前；換行由後方跳過迴圈處理
                break

        # 第二輪回退：找任意分段符號（段落邊界不在範圍內時的降級處理）
        if cut == -1:
            for i in range(search_pos, start, -1):
                if text[i - 1] in split_set:
                    cut = i
                    break

        # 最終回退：整段都沒有分段符號，強制在上限處截斷
        if cut == -1:
            cut = search_pos

        segments.append((start, cut))

        # 跳過斷點後的空白與換行，避免下一段開頭有多餘空白
        while cut < n and text[cut] in ' \n\r\t':
            cut += 1
        start = cut

    # 若最後一段字數不足 128，合併到倒數第二段，避免產生過短的尾段
    if len(segments) > 1 and (segments[-1][1] - segments[-1][0]) < 128:
        segments[-2] = (segments[-2][0], segments[-1][1])
        segments.pop()

    return segments


class FlowerLoadTextFromFolder:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                # 1. 絕對目錄路徑
                "directory": ("STRING", {"default": ""}),
                # 2. 篩選關鍵字（空白＝全選）
                "filter_keyword": ("STRING", {"default": ""}),
                # 3. 排除關鍵字：從 filter_keyword 篩選結果中進一步排除符合的檔案
                #    支援 '&'（AND）與 '|'（OR）分隔多關鍵字
                "negativeKeyword": ("STRING", {"default": ""}),
                # 4. seed：指定選取第幾個檔案或分段
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                # 5. 生成後控制，與 seed 綁定
                "continuous_processing": ("INT", {"default": 1, "min": 1, "max": 9999999}),
                # 6. 排序方式
                "sort_mode": (["字母排序(Alphabetical)", "自然排序(Natural)"],),
            },
            "optional": {
                # 7. 分段字數上限（-1 = 不分段；≥256 = 啟用分段）
                #    optional 確保舊工作流程載入時不會因缺少此欄位而報錯
                "max_chars_per_segment": ("INT", {"default": -1, "min": -1, "max": 0xffffffffffffffff}),
                # 8. 分段符號，回推時以此符號集為合法切斷點
                "split_symbols": ("STRING", {"default": ",.?!;:，。？！；："}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("text", "full_path", "file_name", "file_name_no_ext")
    FUNCTION = "load_text_from_folder"
    CATEGORY = "flower-tools"
    OUTPUT_NODE = True

    def load_text_from_folder(self, directory, filter_keyword, seed, continuous_processing,
                               sort_mode, max_chars_per_segment=-1, split_symbols=",.?!;:，。？！；：",
                               negativeKeyword=""):
        base_dir = directory.strip()

        # 目錄驗證
        if not base_dir or not os.path.isdir(base_dir):
            error_msg = f"錯誤：目錄不存在 [{base_dir}]"
            return {"ui": {"text": [error_msg], "file_list": [""]}, "result": (error_msg, "", "", "")}

        # 列出所有 .txt 檔案
        try:
            all_files = [f for f in os.listdir(base_dir) if f.lower().endswith(".txt")]
        except Exception as e:
            return {"ui": {"text": [str(e)], "file_list": [""]}, "result": (str(e), "", "", "")}

        # 套用篩選關鍵字（支援 '&' AND、'|' OR 多關鍵字模式）
        if filter_keyword.strip():
            all_files = [f for f in all_files if _match_keyword_pattern(f, filter_keyword)]

        # 套用排除關鍵字（從篩選結果中進一步排除）
        if negativeKeyword.strip():
            all_files = [f for f in all_files if not _match_keyword_pattern(f, negativeKeyword)]

        # 依指定方式排序
        all_files.sort(key=_get_sort_key(sort_mode))

        if not all_files:
            empty_msg = "沒有符合條件的文字檔案"
            return {"ui": {"text": [empty_msg], "file_list": [""]}, "result": (empty_msg, "", "", "")}

        # ── 分段模式（max_chars_per_segment ≥ 256）──
        if max_chars_per_segment >= 256:
            return self._load_with_segments(
                base_dir, all_files, seed, continuous_processing,
                max_chars_per_segment, split_symbols
            )

        # ── 直接模式（不分段）──
        process_idx = seed // max(1, continuous_processing)
        file_idx = process_idx % len(all_files)

        selected_file = all_files[file_idx]
        full_path = os.path.join(base_dir, selected_file)
        file_name_no_ext = os.path.splitext(selected_file)[0]

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            return {
                "ui": {"text": [str(e)], "file_list": [""]},
                "result": (str(e), full_path, selected_file, file_name_no_ext),
            }

        # 產生編號檔案清單（從 0 開始）
        file_list_lines = [f"{i}- {fn}" for i, fn in enumerate(all_files)]
        file_list_str = "\n".join(file_list_lines)

        return {
            "ui": {"text": [content], "file_list": [file_list_str]},
            "result": (content, full_path, selected_file, file_name_no_ext),
        }

    def _load_with_segments(self, base_dir, all_files, seed, continuous_processing,
                             max_chars, split_symbols):
        """
        分段模式核心邏輯。

        步驟：
          1. 逐一讀取所有符合條件的 .txt 檔案
          2. 對每個檔案用 _compute_segments() 計算分段位置
          3. 將所有分段展平成一個虛擬條目列表
          4. 依 seed / continuous_processing 算出索引，取出對應段落文字

        虛擬條目列表顯示格式（file_list_display）：
          「序號- 檔名-段落序號(02d)-段落結尾字元位置」
          例：0- 美如與倩兒-01-983
              1- 美如與倩兒-02-1968

        這樣做的好處：seed 直接對應虛擬列表，不會因 continuous_processing
        影響而誤跳到不同檔案，解決跨檔案矛盾問題。
        """
        virtual_entries = []

        for filename in all_files:
            full_path = os.path.join(base_dir, filename)
            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    text = f.read()
            except Exception:
                # 讀取失敗的檔案跳過
                continue

            name_no_ext = os.path.splitext(filename)[0]
            segments = _compute_segments(text, max_chars, split_symbols)
            total_segs = len(segments)

            for seg_idx, (start, end) in enumerate(segments, 1):
                virtual_entries.append({
                    'filename':     filename,
                    'full_path':    full_path,
                    'name_no_ext':  name_no_ext,
                    'seg_num':      seg_idx,      # 1-based 段落序號
                    'total_segs':   total_segs,
                    'start':        start,
                    'end':          end,
                })

        if not virtual_entries:
            msg = "無法建立分段列表（所有檔案皆無法讀取）"
            return {"ui": {"text": [msg], "file_list": [""]}, "result": (msg, "", "", "")}

        # 依 seed 計算虛擬列表索引
        process_idx = seed // max(1, continuous_processing)
        entry = virtual_entries[process_idx % len(virtual_entries)]

        # 讀取對應段落的文字內容
        try:
            with open(entry['full_path'], "r", encoding="utf-8") as f:
                full_text = f.read()
            content = full_text[entry['start']:entry['end']]
        except Exception as e:
            content = str(e)

        # 產生虛擬分段清單（格式：序號- 檔名-段落號-結尾字元位置）
        file_list_lines = []
        for i, e in enumerate(virtual_entries):
            seg_label = f"{e['name_no_ext']}-{e['seg_num']:02d}-{e['end']}"
            file_list_lines.append(f"{i}- {seg_label}")
        file_list_str = "\n".join(file_list_lines)

        # 分段模式下，三個路徑輸出改用虛擬段落檔名
        # 格式：檔名-段落號-結尾字元位置（與 file_list_display 顯示一致）
        seg_suffix        = f"-{entry['seg_num']:02d}-{entry['end']}"
        virtual_name_no_ext = entry['name_no_ext'] + seg_suffix
        virtual_filename    = virtual_name_no_ext + '.txt'
        virtual_full_path   = os.path.join(os.path.dirname(entry['full_path']), virtual_filename)

        return {
            "ui": {"text": [content], "file_list": [file_list_str]},
            "result": (content, virtual_full_path, virtual_filename, virtual_name_no_ext),
        }


# ── API 端點 ──────────────────────────────────────────────────────────────────

@PromptServer.instance.routes.get("/flower-tools/list-text-files")
async def list_text_files(request):
    """
    列出指定目錄中符合關鍵字篩選的 .txt 文字檔清單（字母排序）。
    供 JS 的 refresh_btn 在執行節點前即時預覽檔案清單使用。

    Query params：
      directory       - 目錄絕對路徑
      keyword         - 篩選關鍵字（支援 & AND、| OR；空白 = 不篩選）
      negativeKeyword - 排除關鍵字（同上語法；空白 = 不排除）
    """
    directory        = request.rel_url.query.get("directory", "").strip()
    keyword          = request.rel_url.query.get("keyword", "").strip()
    negative_keyword = request.rel_url.query.get("negativeKeyword", "").strip()

    if not directory or not os.path.isdir(directory):
        return web.json_response({"error": "目錄不存在"}, status=404)

    try:
        files = [f for f in os.listdir(directory) if f.lower().endswith(".txt")]
        if keyword:
            files = [f for f in files if _match_keyword_pattern(f, keyword)]
        if negative_keyword:
            files = [f for f in files if not _match_keyword_pattern(f, negative_keyword)]
        files.sort(key=lambda f: f.lower())
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

    return web.json_response({"files": files})


NODE_CLASS_MAPPINGS = {"FlowerLoadTextFromFolder": FlowerLoadTextFromFolder}
NODE_DISPLAY_NAME_MAPPINGS = {"FlowerLoadTextFromFolder": "🌸Flower Load Text From Folder"}
