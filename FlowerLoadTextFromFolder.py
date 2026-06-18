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


# 章節標題正規式：行首（允許前置空白與各式括弧）後接「第 + 中文/阿拉伯數字 + 章」
# 例：第一章、第十二章、第123章、【第三章】、（第二章）、「第五章」皆能命中
_CHAPTER_RE = re.compile(
    r'^[\s\[\(【「《（〈\<《【]*第[一二三四五六七八九十百千零〇兩0-9０-９]+章',
    re.MULTILINE,
)
# 從章節匹配字串中再抽出純「第X章」標籤（去除前置空白與括弧）
_CHAPTER_LABEL_RE = re.compile(r'第[一二三四五六七八九十百千零〇兩0-9０-９]+章')


def _chapter_label(match_text):
    """從章節匹配的原始字串中取出純『第X章』標籤；找不到回傳空字串。"""
    m = _CHAPTER_LABEL_RE.search(match_text)
    return m.group(0) if m else ""


# 中文數字表（用於「字數自編章節」模式生成『第X章』標籤）
_CN_DIGITS_TBL = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九']


def _num_to_chinese(n: int) -> str:
    """
    將正整數轉為中文數字字串（支援 1~9999；超過則回退為阿拉伯數字）。
    例：1→"一"、10→"十"、12→"十二"、21→"二十一"、100→"一百"、
        101→"一百零一"、1000→"一千"、2025→"二千零二十五"
    """
    if n <= 0:
        return '零'
    if n < 10:
        return _CN_DIGITS_TBL[n]
    if n == 10:
        return '十'
    if n < 20:
        return '十' + _CN_DIGITS_TBL[n - 10]
    if n < 100:
        t, o = divmod(n, 10)
        return _CN_DIGITS_TBL[t] + '十' + (_CN_DIGITS_TBL[o] if o else '')
    if n < 1000:
        h, r = divmod(n, 100)
        s = _CN_DIGITS_TBL[h] + '百'
        if r == 0:
            return s
        if r < 10:
            return s + '零' + _CN_DIGITS_TBL[r]
        return s + _num_to_chinese(r)
    if n < 10000:
        k, r = divmod(n, 1000)
        s = _CN_DIGITS_TBL[k] + '千'
        if r == 0:
            return s
        if r < 100:
            return s + '零' + _num_to_chinese(r)
        return s + _num_to_chinese(r)
    return str(n)


def _split_by_char_count(text, chars_per_chapter):
    """
    依「接近但不超過」chars_per_chapter 的字數切塊，並要求在換行符號處切斷，
    以避免從句子中間硬砍。每塊賦予『第X章』標籤。

    規則（與 _compute_segments 類似）：
      - 從目前起點向後算 chars_per_chapter 個字元，再回推找最近的換行 '\\n' / '\\r'
        於換行之後切斷；找不到任何換行則強制在上限處截斷（避免無法前進）。
      - 切斷後跳過開頭的空白與換行，避免下一塊以多餘空白起始。
      - 若最後一塊長度 < chars_per_chapter / 2，併入前一塊（避免尾段過短，
        實際長度可達 1.5 × chars_per_chapter 左右）。
      - 章節標籤『第一章、第二章…』在合併完成後依序重新編號。

    回傳 [(start, end, "第X章"), ...]。
    """
    n = len(text)
    if n == 0:
        return [(0, 0, "第一章")]

    N = max(1, chars_per_chapter)

    # 整篇不足上限：單一章
    if n <= N:
        return [(0, n, "第一章")]

    blocks = []  # 暫存 (start, end)
    start = 0
    while start < n:
        # 剩餘字數在上限以內 → 收尾為最後一塊
        if (n - start) <= N:
            blocks.append((start, n))
            break

        search_pos = start + N
        cut = -1

        # 回推尋找最近的換行符號；切點落在換行符號「之後」
        for i in range(search_pos, start, -1):
            if text[i - 1] in '\n\r':
                cut = i
                break

        # 找不到換行 → 強制在上限處截斷，避免無法前進
        if cut == -1:
            cut = search_pos

        blocks.append((start, cut))

        # 跳過下一塊開頭的空白與換行
        while cut < n and text[cut] in ' \n\r\t':
            cut += 1
        start = cut

    # 尾段過短（< N/2）併入前一段，避免最後一章只有少量文字
    if len(blocks) > 1 and (blocks[-1][1] - blocks[-1][0]) < (N // 2):
        blocks[-2] = (blocks[-2][0], blocks[-1][1])
        blocks.pop()

    # 依序貼上『第X章』標籤
    return [
        (s, e, f"第{_num_to_chinese(i + 1)}章")
        for i, (s, e) in enumerate(blocks)
    ]


def _split_by_chapters(text):
    """
    依「第X章」章節標題將全文切成多個區塊。

    規則：
      - 第一個區塊：從檔頭到「第二章」之前（包含前言與第一章內容）
      - 之後每個區塊：從該章標題行首到下一章標題行首之前
      - 最後一個區塊：最末章到檔尾

    若找不到任何章節標題（或僅有一個），回傳 [(0, len(text), "<label>")]。
    回傳 [(start, end, chapter_label), ...] 列表，label 例如 "第一章"。
    無章節時 label 為 ""；單一章節時 label 取唯一匹配的標籤。
    """
    n = len(text)
    matches = list(_CHAPTER_RE.finditer(text))

    # 沒有章節標題：整篇視為單一區塊，無標籤
    if not matches:
        return [(0, n, "")]

    # 只有一個章節標題：整篇單一區塊，標籤取該章
    if len(matches) == 1:
        return [(0, n, _chapter_label(matches[0].group(0)))]

    blocks = []
    # 首區塊：0 → 第二章起點（含前言與第一章），標籤＝第一章
    blocks.append((0, matches[1].start(), _chapter_label(matches[0].group(0))))
    # 中間區塊：第i章起點 → 第(i+1)章起點，標籤＝第i章
    for i in range(1, len(matches) - 1):
        blocks.append((
            matches[i].start(),
            matches[i + 1].start(),
            _chapter_label(matches[i].group(0)),
        ))
    # 末區塊：最末章起點 → 檔尾，標籤＝末章
    blocks.append((matches[-1].start(), n, _chapter_label(matches[-1].group(0))))
    return blocks


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
                # 9. 章節分段模式：
                #    - 無：不分章節，整篇依字數上限分段，檔名不加「第X章」
                #    - 根據章編號分章輸出(第一章)：偵測文中「第X章」標題切塊（預設）
                #    - 根據字數自編章節輸出(4000字合成一章)：依 chars_per_auto_chapter
                #      固定字數切塊，並自動命名為「第一章、第二章...」
                "split_by_chapter": (
                    ["無", "根據章編號分章輸出(第一章)", "根據字數自編章節輸出(4000字合成一章)"],
                    {"default": "根據章編號分章輸出(第一章)"},
                ),
                # 10. 自編章節的限制字數：用於「根據字數自編章節輸出」模式
                "chars_per_auto_chapter": ("INT", {"default": 4000, "min": 1, "max": 0xffffffffffffffff}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("text", "full_path", "file_name", "file_name_no_ext")
    FUNCTION = "load_text_from_folder"
    CATEGORY = "flower-tools"
    OUTPUT_NODE = True

    def load_text_from_folder(self, directory, filter_keyword, seed, continuous_processing,
                               sort_mode, max_chars_per_segment=-1, split_symbols=",.?!;:，。？！；：",
                               negativeKeyword="", split_by_chapter="根據章編號分章輸出(第一章)",
                               chars_per_auto_chapter=4000):
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
                max_chars_per_segment, split_symbols, split_by_chapter,
                chars_per_auto_chapter,
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
                             max_chars, split_symbols,
                             split_by_chapter="根據章編號分章輸出(第一章)",
                             chars_per_auto_chapter=4000):
        """
        分段模式核心邏輯。

        步驟：
          1. 逐一讀取所有符合條件的 .txt 檔案
          2. 對每個檔案用 _compute_segments() 計算分段位置
          3. 將所有分段展平成一個虛擬條目列表
          4. 依 seed / continuous_processing 算出索引，取出對應段落文字

        虛擬條目列表顯示格式（file_list_display）：
          「序號- 檔名-段落序號(04d)-段落結尾字元位置」
          例：0- 美如與倩兒-0001-983
              1- 美如與倩兒-0002-1968

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

            # 先以章節為單位切塊（若關閉則整篇視為單一區塊），
            # 再在每塊內依字數上限細分，確保段落不跨章。
            # 依下拉選單決定章節切塊策略
            if split_by_chapter == "根據章編號分章輸出(第一章)":
                chapter_blocks = _split_by_chapters(text)
            elif split_by_chapter == "根據字數自編章節輸出(4000字合成一章)":
                chapter_blocks = _split_by_char_count(text, chars_per_auto_chapter)
            else:  # "無"：不分章，整篇單一區塊且無章節標籤
                chapter_blocks = [(0, len(text), "")]

            segments = []
            for chap_idx, (blk_start, blk_end, chap_label) in enumerate(chapter_blocks, 1):
                block_text = text[blk_start:blk_end]
                for s, e in _compute_segments(block_text, max_chars, split_symbols):
                    # 將區塊內相對位置還原為整篇絕對位置，並記錄所屬章節標籤
                    segments.append((blk_start + s, blk_start + e, chap_label, chap_idx))

            total_segs = len(segments)

            for seg_idx, (start, end, chap_label, chap_num) in enumerate(segments, 1):
                virtual_entries.append({
                    'filename':     filename,
                    'full_path':    full_path,
                    'name_no_ext':  name_no_ext,
                    'seg_num':      seg_idx,      # 1-based 段落序號
                    'total_segs':   total_segs,
                    'start':        start,
                    'end':          end,
                    'chap_label':   chap_label,   # 章節標籤，如 "第一章"；無則為 ""
                    'chap_num':     chap_num,     # 1-based 章節序號（三位數用）
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

        # 產生虛擬分段清單（格式：序號- 檔名-XXX第X章-段落號-結尾字元位置）
        file_list_lines = []
        for i, e in enumerate(virtual_entries):
            chap_prefix = f"-{e['chap_num']:03d}{e['chap_label']}" if e.get('chap_label') else ""
            seg_label = f"{e['name_no_ext']}{chap_prefix}-{e['seg_num']:04d}-{e['end']}"
            file_list_lines.append(f"{i}- {seg_label}")
        file_list_str = "\n".join(file_list_lines)

        # 分段模式下，三個路徑輸出改用虛擬段落檔名
        # 格式：檔名-XXX第X章-段落號-結尾字元位置（與 file_list_display 顯示一致）
        chap_prefix       = f"-{entry['chap_num']:03d}{entry['chap_label']}" if entry.get('chap_label') else ""
        seg_suffix        = f"{chap_prefix}-{entry['seg_num']:04d}-{entry['end']}"
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


@PromptServer.instance.routes.get("/flower-tools/preview-text-segments")
async def preview_text_segments(request):
    """
    完整模擬節點執行，回傳目前設定下會輸出的 content_preview 與 file_list_display。
    供 JS 的 refresh_btn 即時預覽分段／章節結果使用，不需執行整個工作流程。

    Query params 對應節點所有 INPUT_TYPES 欄位。
    """
    q = request.rel_url.query

    def _qi(name, default):
        try:
            return int(q.get(name, str(default)) or default)
        except (ValueError, TypeError):
            return default

    node = FlowerLoadTextFromFolder()
    try:
        result = node.load_text_from_folder(
            directory             = q.get("directory", ""),
            filter_keyword        = q.get("filter_keyword", ""),
            seed                  = _qi("seed", 0),
            continuous_processing = _qi("continuous_processing", 1),
            sort_mode             = q.get("sort_mode", "字母排序(Alphabetical)"),
            max_chars_per_segment = _qi("max_chars_per_segment", -1),
            split_symbols         = q.get("split_symbols", ",.?!;:，。？！；："),
            negativeKeyword       = q.get("negativeKeyword", ""),
            split_by_chapter      = q.get("split_by_chapter", "根據章編號分章輸出(第一章)"),
            chars_per_auto_chapter = _qi("chars_per_auto_chapter", 4000),
        )
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

    ui = result.get("ui", {}) if isinstance(result, dict) else {}
    text_list = ui.get("text") or [""]
    file_list = ui.get("file_list") or [""]
    return web.json_response({
        "text":      text_list[0] if text_list else "",
        "file_list": file_list[0] if file_list else "",
    })


NODE_CLASS_MAPPINGS = {"FlowerLoadTextFromFolder": FlowerLoadTextFromFolder}
NODE_DISPLAY_NAME_MAPPINGS = {"FlowerLoadTextFromFolder": "🌸Flower Load Text From Folder"}
