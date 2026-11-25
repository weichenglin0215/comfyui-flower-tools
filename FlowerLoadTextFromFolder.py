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


def _seed_to_index(seed, continuous_processing):
    """
    把 seed 換算成清單中的位置（回傳 0 起算的 Python 索引，尚未對清單長度取餘數）。

    seed 從 1 開始，對應節點下方檔案清單的編號（清單也從 1 開始）。
    continuous_processing = k 代表同一個項目連續處理 k 次：
      seed 1 ~ k → 清單第 1 項、seed k+1 ~ 2k → 第 2 項，依此類推。

    小於 1 的 seed（例如舊工作流程存的 0，或由其他節點接線傳入的 0）一律視為 1，
    避免換算成負數索引而跑到清單最後一項。
    """
    return (max(1, seed) - 1) // max(1, continuous_processing)


def _replace_fullwidth_space(text):
    """
    將全形空白（U+3000，中文常見的段首縮排 "　　"）取代為半形空白。
    有些 AI 語音無法正常處理全形空白。一個全形空白換成一個半形空白，字數不變。
    只處理 U+3000，不動換行、定位點或其他空白字元。
    """
    return text.replace("　", " ")


# 行首空白字元：任何「不是換行」的空白（半形空白、全形空白、定位點 Tab、不換行空白等），
# 再加上看不見的 BOM：記事本存出的 .txt 開頭常有 BOM，後面接著段首縮排，
# 不把 BOM 一起刪掉的話，第一行的縮排就刪不掉。
# BOM 用 chr(0xFEFF) 寫，避免原始碼裡出現看不見的字元。
# re.MULTILINE 讓 ^ 對應「每一行」的開頭
_LINE_LEADING_BLANK_RE = re.compile(r"^(?:[^\S\r\n]|" + chr(0xFEFF) + r")+", re.MULTILINE)


def _strip_line_leading_space(text):
    """
    刪除每一行行首的空白字元（全形、半形都刪），避免 AI 語音唸到行首空白時產生顫音。
    只刪行首：行中與行尾的空白都保留，換行也保留（這個函數本身不刪行，只有空白的行會變成空行；
    要不要把空行整行刪掉由 _remove_blank_lines 決定）。
    """
    return _LINE_LEADING_BLANK_RE.sub("", text)


# 空白行：整行沒有任何字，或只由空白字元組成（半形、全形、Tab、不換行空白等）。
# 看不見的 BOM 也算空白，和上面「行首空白字元」的認定一致
_BLANK_LINE_RE = re.compile(r"(?:\s|" + chr(0xFEFF) + r")*")


def _remove_blank_lines(text):
    """
    刪除空白行，避免原文稿多餘的空行干擾 AI 語音。
      - 沒有任何字、或只有空白字元（全形、半形都算）的行，整行刪除。
      - 有字的行原封不動（行首、行中、行尾的空白都不在這裡處理）。
      - 結果的開頭與結尾不會留下空行：最後一行有字的後面不再接換行
        （否則預覽框最後仍會看到一個空行）。
      - 整段文字全是空白時，回傳空字串。
    讀檔時使用 Python 的文字模式，\\r\\n 與 \\r 都已轉成 \\n，所以只需要用 \\n 分行。
    """
    return "\n".join(line for line in text.split("\n") if not _BLANK_LINE_RE.fullmatch(line))


def _append_fullwidth_period(text, split_symbols):
    """
    若輸出文字的「最後一個字」不是分段符號，就在文字末端補上一個全形句點「。」。

    用意：有些 AI 語音在句尾不是標點時，最後一個字的尾音很容易被截斷；
    補上句點後，語音會把最後一個字完整唸完。

    規則：
      - 「最後一個字」指最後一個非空白字元；補上的句點緊接在它後面，
        原本結尾的換行／空白保持在句點之後（例：「你好\\n」→「你好。\\n」，不會變成「你好\\n。」）。
      - 最後一個字已經是 split_symbols 中的任一字元就不補（例：，。？！；：）。
      - 全形句點「。」本身一律視為已有結尾，即使使用者把 split_symbols 清空也不會變成「。。」。
      - 結尾是成對括號的結尾（如「你好。」的 」）時，最後一個字是 」，不是分段符號，所以仍會補上句點。
      - 全空白或空字串不處理。
    """
    body = text.rstrip()
    if not body:
        return text
    last = body[-1]
    if last == "。" or last in split_symbols:
        return text
    # body 之後剩下的就是被 rstrip 去掉的結尾空白，原樣接回去
    return body + "。" + text[len(body):]


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


# 成對括號表：開頭括號 → 對應的結尾括號。
# 「開頭括號」到「結尾括號」之間通常是角色說的一句話（或一段完整的註解），
# 分段時必須完整放在同一個段落之中，否則語氣會被切斷而不順。
_BRACKET_PAIRS = {
    '「': '」', '［': '］', '『': '』', '【': '】', '〔': '〕',
    '｛': '｝', '＜': '＞', '〈': '〉', '《': '》', '（': '）',
}
_CLOSE_BRACKETS = frozenset(_BRACKET_PAIRS.values())


def _match_bracket_pairs(text):
    """
    找出文字中所有「成對」的括號，回傳 [(開頭位置 o, 結尾位置 c), ...]
    （依結尾括號出現的順序排列，所以內層括號一定排在外層括號之前）。

    配對方式：用堆疊由左到右掃描，結尾括號與「最近一個同種類的開頭括號」配對，
    夾在中間沒有配到對的開頭括號直接捨棄；沒有開頭可配的結尾括號視為雜訊忽略。
    因此文字中少了結尾括號（找不到 」）時，那個開頭括號不會被保護，也不會影響其他段落。
    巢狀括號（如 「他說：『好。』」）會一併配對成內外兩對。
    """
    pairs = []
    stack = []  # 每項為 (期待的結尾括號, 開頭括號位置)

    for i, ch in enumerate(text):
        closer = _BRACKET_PAIRS.get(ch)
        if closer is not None:
            # 開頭括號：記下它期待哪一種結尾，等後面遇到再配對
            stack.append((closer, i))
        elif ch in _CLOSE_BRACKETS and stack:
            # 結尾括號：由最近的開頭往回找第一個種類相同的配對
            for k in range(len(stack) - 1, -1, -1):
                if stack[k][0] == ch:
                    pairs.append((stack[k][1], i))
                    del stack[k:]  # 該開頭及其後尚未配對的開頭一併出堆疊
                    break

    return pairs


def _analyze_brackets(text, max_chars):
    """
    標記「在哪些位置切斷會把一對括號切成兩半」，整份文字只算一次，之後切段時直接查表。
    對一對括號（開頭位置 o、結尾位置 c，括號長度 = c - o + 1）來說：
      - 切在開頭括號之前（p == o）：整對括號都在下一段，可以切。
      - 切在結尾括號之後（p == c+1）：整對括號都在前一段，可以切。
      - 切在括號內部（o+1 ~ c）：會把括號切開，要看這對括號的長度：
          · 長度 <= max_chars（「短括號」）：一定放得進一段，內部永遠不可切。
          · 長度 >  max_chars（「長括號」）：怎麼放都放不進一段，內部是否可切取決於
            「這一段從哪裡開始」——這一段若從這對括號的開頭（或括號內部）才開始，
            就允許在內部切；若這一段從括號之前就開始，則不可切，
            應該切在括號之前，讓整對括號移到下一段（見 _compute_segments）。
      - 切在「結尾括號之前」（p == c）：不論長短，一般情況都不切，否則 」 會單獨出現在下一段開頭。
        只有在「別無選擇」時（整段都是受保護的位置，例如剛好等於上限的短括號後面緊接外層結尾括號），
        才允許切在這裡：括號內容完整留在前一段，只有結尾括號落到下一段開頭。

    回傳 (inside, before_closer, open_at, long_open)：
      inside       : 長度 len(text)+1 的 bytearray，inside[p] == 1 代表位置 p 在某對「短括號」的內部
                     （o+1 ~ c-1），永遠不可切。
      before_closer: 長度 len(text)+1 的 bytearray，before_closer[p] == 1 代表 text[p] 是某對括號的
                     結尾括號（位置 p 是「結尾括號之前」），一般情況不切。
      open_at      : 長度 len(text)+1 的 bytearray，open_at[p] == 1 代表 text[p] 是成對括號的開頭，
                     位置 p 可作為「括號之前」的切點。
      long_open    : 長括號的內部標記。long_open[p] 是「包住位置 p 的長括號中，開頭位置最大
                     （最內層）的那一個」，沒有則為 -1；沒有任何長括號時整個回傳 None（省記憶體）。
                     在「這一段的有效起點是 anchor」時，若 long_open[p] > anchor，代表位置 p 在
                     一對「anchor 之後才開始」的長括號內部，不可切。
    """
    n = len(text)
    inside = bytearray(n + 1)
    before_closer = bytearray(n + 1)
    open_at = bytearray(n + 1)
    long_open = None

    for o, c in _match_bracket_pairs(text):
        open_at[o] = 1
        before_closer[c] = 1
        if c - o + 1 <= max_chars:
            # 短括號：o+1 ~ c-1 之間的切點都會把這對括號切開
            inside[o + 1:c] = b'\x01' * (c - o - 1)
        else:
            # 長括號：記下內部每個位置屬於哪一個長括號的開頭。
            # 內層括號比外層先處理（先結尾），所以只在尚未被內層標記的位置寫入外層開頭
            if long_open is None:
                long_open = [-1] * (n + 1)
            for p in range(o + 1, c):
                if long_open[p] < o:
                    long_open[p] = o

    return inside, before_closer, open_at, long_open


def _compute_segments(text, max_chars, split_symbols):
    """
    將文字依照最大字數與分段符號切成多段。

    演算法：
      從目前段落起點往後算 max_chars 個字元，再回推找最近的分段符號，
      在符號之後（含符號）切斷。若整段都沒有分段符號，強制在 max_chars 處截斷。
      切斷後跳過開頭的空白與換行，再繼續處理下一段。

    成對括號保護（「」『』［］【】〔〕｛｝＜＞〈〉《》（））：
      括號內通常是角色說的一句話，要和結尾括號放在同一個段落，所以：
      - 括號內的 ，。？！ 等分段符號不當作切點，括號只會整對留在前一段或移到下一段。
      - 括號結尾若緊接在分段符號之後（如 。」 ！』），切點算在結尾括號「之後」，
        不會把 」 單獨留到下一段開頭。
      - 字數上限是硬性的：每一段都不超過 max_chars。括號保護絕不能把一段撐過上限
        （語音模型遇到太長的段落，後半段會漏字），所以括號太長時依下列規則處理。

    括號太長（長度 > max_chars，怎麼放都放不進一段）的處理（見 _analyze_brackets）：
      規則 1：這一段不是從這對括號的開頭開始，而上限落在這對括號裡面
              → 不要包含括號內的文字，切在括號「之前」，整對括號移到下一段
              （寧可這一段變短）。括號之前沒有標點時，也允許直接切在開頭括號之前。
      規則 2：這一段從這對括號的開頭開始，括號本身就超過上限
              → 無法再移到下一段，只好把括號內的文字切開。
              切點優先選括號內部的分段符號（內層沒超過上限的括號仍保持完整，
              超過的內層括號再套用同樣規則）；括號內沒有分段符號才在上限處硬切。
              整份文字的括號配對與長短是「一開始就一次算好」的（_analyze_brackets），
              所以切開後下一段雖然開頭沒有 「，也知道自己還在括號裡面，
              不會誤判成一般文字；括號的最後一小段會連同括號後方的文字，
              繼續依一般規則填滿到上限為止。
    一般（短）括號則是：上限落在括號內部時，往前找括號之前的切點；
    前方連一個可切的位置都沒有，就切在最近的「括號之前」，不再往後延伸而超過上限。
    段落開頭若是連續的開頭括號（如 「『…），視為這一段已經身在括號裡面，
    不會切在這些開頭括號之間，避免產生只剩一個 「 的段落。

    回傳 [(start, end), ...] 列表，text[start:end] 即為該段落文字。
    """
    n = len(text)

    # 整份文字不超過上限，直接當作一段
    if n <= max_chars:
        return [(0, n)]

    split_set = set(split_symbols)
    # 預先標記括號資訊（只算一次，後面回推時直接查表），內容見 _analyze_brackets
    inside, before_closer, open_at, long_open = _analyze_brackets(text, max_chars)

    def interior_blocked(p, anchor):
        """
        在位置 p 切斷（text[:p] 為前一段、text[p:] 為下一段）是否會切進某對括號的「內部」？
        anchor 是目前這一段的「有效起點」：長括號只有在「這一段從括號之前就開始」
        （括號開頭位置 > anchor）時，內部才不可切；
        這一段若從長括號的開頭或內部才開始（規則 2），內部就允許切。
        """
        return bool(inside[p]) or (long_open is not None and long_open[p] > anchor)

    def blocked(p, anchor):
        """位置 p 是否不該切：切進括號內部，或切在成對結尾括號之前（會讓 」 單獨落到下一段開頭）。"""
        return interior_blocked(p, anchor) or bool(before_closer[p])

    def is_cut_point(p, anchor):
        """
        位置 p 是否為合法切點：
          1. 不能把括號切開（見 blocked）。
          2. p 之前必須是分段符號；若緊貼著結尾括號，則略過這些結尾括號後再看是不是分段符號
             （例：「你好。」 的切點在 」 之後，而不是 。 與 」 之間）。
        沒有任何括號時，行為與原本「text[p-1] 是分段符號」完全相同。
        """
        if blocked(p, anchor):
            return False
        j = p - 1
        while j >= 0 and text[j] in _CLOSE_BRACKETS:
            j -= 1
        return j >= 0 and text[j] in split_set

    def last_open_cut(search_pos, anchor):
        """
        從 search_pos 往前找最近、而且不會把括號切開的「開頭括號之前」位置；找不到回傳 -1。
        只找 anchor 之後的位置：anchor 之前都是這一段開頭連續的開頭括號，
        切在它們之間會讓這一段只剩幾個 「 而沒有任何內容。
        """
        for i in range(search_pos, anchor, -1):
            if open_at[i] and not blocked(i, anchor):
                return i
        return -1

    def forced_cut(search_pos, anchor):
        """
        上限處（search_pos）不能直接切，而且前面沒有分段符號可切時的最後手段，依序嘗試：
          1. 最近的「開頭括號之前」。
          2. 往前退到最近一個完全不會切開括號的位置。
          3. 別無選擇（整段都是受保護的位置）：允許切在成對結尾括號之前——
             括號內容完整留在這一段，只有結尾括號落到下一段開頭。
          4. 連這個都沒有（理論上不會發生）：在上限處強制截斷。
        一律只往前找（而且只找 anchor 之後），所以這一段絕不會超過 max_chars。
        """
        cut = last_open_cut(search_pos, anchor)
        if cut != -1:
            return cut
        for i in range(search_pos, anchor, -1):
            if not blocked(i, anchor):
                return i
        for i in range(search_pos, anchor, -1):
            if not interior_blocked(i, anchor):
                return i
        return search_pos

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

        # 有效起點 anchor：這一段開頭若緊接著連續的成對開頭括號（如 「『…），
        # 視為這一段「已經身在這些括號裡面」，直接對它們套用規則 2。
        # 否則（外層開頭緊接內層長括號）規則 1 會把切點放在內層括號之前，這一段就只剩一個 「。
        anchor = start
        while anchor < n and open_at[anchor]:
            anchor += 1

        # 第一輪：找「分段符號 + 緊接換行」（真正的段落邊界，如 。\n ！\n ？\n 或 。」\n）
        for i in range(search_pos, start, -1):
            if i < n and text[i] in '\n\r' and is_cut_point(i, anchor):
                cut = i  # 切在符號之後、換行之前；換行由後方跳過迴圈處理
                break

        # 規則 1：上限落在一對「從這一段之後才開始」的長括號裡面
        # → 直接切在該括號之前，讓整對括號移到下一段（下一段就會從括號開頭開始，進入規則 2）。
        # 不再往前找更早的分段符號：那樣只會讓括號前的文字被切成不必要的小段。
        if cut == -1 and long_open is not None and long_open[search_pos] > anchor:
            cut = last_open_cut(search_pos, anchor)

        # 第二輪回退：找任意分段符號（段落邊界不在範圍內時的降級處理）
        if cut == -1:
            for i in range(search_pos, start, -1):
                if is_cut_point(i, anchor):
                    cut = i
                    break

        # 最終回退：整段都沒有分段符號可切，在上限處強制截斷。
        # 上限若剛好落在括號內部（或結尾括號之前），改用 forced_cut 往前找不會切開括號的位置。
        # 一律只往前找，所以這一段絕不會超過 max_chars。
        if cut == -1:
            cut = search_pos
            if blocked(cut, anchor):
                cut = forced_cut(search_pos, anchor)

        segments.append((start, cut))

        # 跳過斷點後的空白與換行，避免下一段開頭有多餘空白
        while cut < n and text[cut] in ' \n\r\t':
            cut += 1
        start = cut

    # 尾段不再合併到前一段（原本「不足 128／64 字就併入前一段」的規則已移除）：
    # 字數上限是硬性的，必須「合併後總長不超過 max_chars」才能合併，但這個條件永遠不會成立——
    # 倒數第二段之所以存在，是因為從它的起點算起剩餘字數仍超過 max_chars，
    # 也就是它加上尾段一定超過 max_chars。所以短尾段會保持獨立一段。
    return segments


class FlowerLoadTextFromFolder:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                # 1. 絕對目錄路徑
                "directory": ("STRING", {"default": ""}),
                # 2. 篩選關鍵字（空白＝全選）
                "filter_keyword": ("STRING", {"default": "", "placeholder": "'|'  OR 分隔  '&' 為 AND 分隔 範例：'A&B|C'"}),
                # 3. 排除關鍵字：從 filter_keyword 篩選結果中進一步排除符合的檔案
                #    支援 '&'（AND）與 '|'（OR）分隔多關鍵字
                "negativeKeyword": ("STRING", {"default": "", "placeholder": "'|'  OR 分隔  '&' 為 AND 分隔 範例：'A&B|C'"}),
                # 4. seed：指定選取第幾個檔案或分段。從 1 開始，對應節點下方檔案清單的編號
                "seed": ("INT", {"default": 1, "min": 1, "max": 0xffffffffffffffff}),
                # 5. 生成後控制，與 seed 綁定
                "continuous_processing": ("INT", {"default": 1, "min": 1, "max": 9999999}),
                # 6. 排序方式
                "sort_mode": (["字母排序(Alphabetical)", "自然排序(Natural)"],),
            },
            "optional": {
                # 7. 分段字數上限（-1 = 不分段；≥100 = 啟用分段）。每一段都不會超過這個字數
                #    （括號保護、尾段合併都不能突破上限，否則 AI 語音後半段會漏字）
                #    optional 確保舊工作流程載入時不會因缺少此欄位而報錯
                "max_chars_per_segment": ("INT", {"default": 100, "min": -1, "max": 0xffffffffffffffff}),
                # 8. 分段符號，回推時以此符號集為合法切斷點
                #    （「」『』［］【】〔〕｛｝＜＞〈〉《》（） 成對括號內部不會被切斷；
                #      但括號本身比字數上限還長時，為了守住上限只好切開，見 _compute_segments）
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
                # 11. 以半形空白取代全形空白（預設勾選）：
                #     輸出文字（text）與節點上的預覽框，都會把全形空白（U+3000）換成半形空白，
                #     因為有些 AI 語音無法正常處理全形空白。只影響輸出的文字內容，
                #     不影響分段位置（取代後字數不變）與檔名類輸出。
                #     放在 optional 的最後面，舊工作流程載入時不會因缺少此欄位而錯位或報錯
                "replace_fullwidth_space": ("BOOLEAN", {"default": True, "label_on": "以半形空白取代全形空白", "label_off": "不取代（保留全形空白）"}),
                # 12. 輸出文字末端增加全形句點（預設勾選）：
                #     若輸出文字（text）與預覽框的最後一個字不是分段符號（split_symbols 的任一字元），
                #     就在末端補上「。」，避免 AI 語音唸句子最後一個字時尾音被截斷。
                #     同樣放在 optional 最後面，舊工作流程載入時不會錯位；
                #     不影響分段位置與檔名類輸出（只改輸出的文字內容）
                "append_fullwidth_period": ("BOOLEAN", {"default": True, "label_on": "輸出文字末端增加全形句點", "label_off": "不增加（維持原文結尾）"}),
                # 13. 刪除行首空白字元（預設勾選）：
                #     輸出文字（text）與預覽框中，每一行行首的空白（全形、半形都算）全部刪除，
                #     避免 AI 語音產生顫音。放在 optional 最後面，舊工作流程載入時不會錯位；
                #     分段仍以原文計算，所以分段位置與檔名類輸出不受影響
                "strip_line_leading_space": ("BOOLEAN", {"default": True, "label_on": "刪除行首空白字元", "label_off": "不刪除（保留行首空白）"}),
                # 14. 刪除空白行（預設勾選）：
                #     輸出文字（text）與預覽框中，沒有任何字或只有空白字元（全形、半形都算）的行整行刪除，
                #     避免原文稿多餘的空行干擾 AI 語音。放在 optional 最後面，舊工作流程載入時不會錯位；
                #     分段仍以原文計算，所以分段位置與檔名類輸出不受影響
                "remove_blank_lines": ("BOOLEAN", {"default": True, "label_on": "刪除空白行", "label_off": "不刪除（保留空白行）"}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("text", "full_path", "file_name", "file_name_no_ext")
    FUNCTION = "load_text_from_folder"
    CATEGORY = "flower-tools"
    OUTPUT_NODE = True

    def load_text_from_folder(self, directory, filter_keyword, seed, continuous_processing,
                               sort_mode, max_chars_per_segment=100, split_symbols=",.?!;:，。？！；：",
                               negativeKeyword="", split_by_chapter="根據章編號分章輸出(第一章)",
                               chars_per_auto_chapter=4000, replace_fullwidth_space=True,
                               append_fullwidth_period=True, strip_line_leading_space=True,
                               remove_blank_lines=True):
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

        # ── 分段模式（max_chars_per_segment ≥ 100）──
        if max_chars_per_segment >= 100:
            return self._load_with_segments(
                base_dir, all_files, seed, continuous_processing,
                max_chars_per_segment, split_symbols, split_by_chapter,
                chars_per_auto_chapter, replace_fullwidth_space,
                append_fullwidth_period, strip_line_leading_space, remove_blank_lines,
            )

        # ── 直接模式（不分段）──
        file_idx = _seed_to_index(seed, continuous_processing) % len(all_files)

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

        # 全形空白 → 半形空白（text 輸出與預覽框共用這份 content，所以兩邊一起生效）
        if replace_fullwidth_space:
            content = _replace_fullwidth_space(content)

        # 刪除每一行行首的空白字元（同樣是 text 輸出與預覽框一起生效）
        if strip_line_leading_space:
            content = _strip_line_leading_space(content)

        # 刪除空白行（要在補句點之前，句點才會接在最後一行有字的後面）
        if remove_blank_lines:
            content = _remove_blank_lines(content)

        # 結尾不是分段符號就補上全形句點（不分段時，整個檔案內容就是「這一段」）
        if append_fullwidth_period:
            content = _append_fullwidth_period(content, split_symbols)

        # 產生編號檔案清單（從 1 開始，編號即對應的 seed）
        file_list_lines = [f"{i}- {fn}" for i, fn in enumerate(all_files, 1)]
        file_list_str = "\n".join(file_list_lines)

        return {
            "ui": {"text": [content], "file_list": [file_list_str]},
            "result": (content, full_path, selected_file, file_name_no_ext),
        }

    def _load_with_segments(self, base_dir, all_files, seed, continuous_processing,
                             max_chars, split_symbols,
                             split_by_chapter="根據章編號分章輸出(第一章)",
                             chars_per_auto_chapter=4000, replace_fullwidth_space=True,
                             append_fullwidth_period=True, strip_line_leading_space=True,
                             remove_blank_lines=True):
        """
        分段模式核心邏輯。

        步驟：
          1. 逐一讀取所有符合條件的 .txt 檔案
          2. 對每個檔案用 _compute_segments() 計算分段位置，內容全是空白行的段落略過
          3. 將所有分段展平成一個虛擬條目列表
          4. 依 seed / continuous_processing 算出索引，取出對應段落文字

        虛擬條目列表顯示格式（file_list_display）：
          「序號- 檔名-段落序號(04d)-段落結尾字元位置」
          例：1- 美如與倩兒-0001-983
              2- 美如與倩兒-0002-1968
        序號從 1 開始，即對應的 seed（continuous_processing 為 1 時兩者相同，見 _seed_to_index）。

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
                    # 內容全是空白行的段落不列入清單：沒有任何文字可唸，
                    # 送給 AI 語音只會得到空字串（常見於檔案結尾的多行全形空白剛好自成一段）。
                    # 空的或全是空白的檔案也一樣不列入。
                    # 不受「刪除空白行」勾選與否影響，切換該選項時清單與 seed 編號才不會跟著變動；
                    # 段落序號是過濾之後才編的，所以仍然連續不跳號
                    if _BLANK_LINE_RE.fullmatch(block_text[s:e]):
                        continue
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
            msg = "無法建立分段列表（所有檔案皆無法讀取，或沒有任何文字內容）"
            return {"ui": {"text": [msg], "file_list": [""]}, "result": (msg, "", "", "")}

        # 依 seed 計算虛擬列表索引
        entry = virtual_entries[_seed_to_index(seed, continuous_processing) % len(virtual_entries)]

        # 讀取對應段落的文字內容
        try:
            with open(entry['full_path'], "r", encoding="utf-8") as f:
                full_text = f.read()
            content = full_text[entry['start']:entry['end']]
            # 全形空白 → 半形空白。在「切出段落之後」才取代：兩者都是 1 個字元，
            # 所以不會改變分段位置，檔名中的「結尾字元位置」也不受影響
            if replace_fullwidth_space:
                content = _replace_fullwidth_space(content)
            # 刪除每一行行首的空白字元。也是在切出段落之後才刪，分段位置不受影響
            if strip_line_leading_space:
                content = _strip_line_leading_space(content)
            # 刪除空白行。同樣只改這一段的輸出內容，分段位置不受影響；要在補句點之前做
            if remove_blank_lines:
                content = _remove_blank_lines(content)
            # 這一段的結尾不是分段符號就補上全形句點。同樣只改輸出內容，
            # 不影響分段位置與檔名中的「結尾字元位置」（file_list 與虛擬檔名都用原文的 end）
            if append_fullwidth_period:
                content = _append_fullwidth_period(content, split_symbols)
        except Exception as e:
            content = str(e)

        # 產生虛擬分段清單（格式：序號- 檔名-XXX第X章-段落號-結尾字元位置）
        file_list_lines = []
        for i, e in enumerate(virtual_entries, 1):   # 序號從 1 開始，即對應的 seed
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

    # 布林參數：前端以字串 "true" / "false" 傳入；沒傳時採用節點預設（勾選）
    def _qb(name, default):
        v = q.get(name)
        if v is None:
            return default
        return str(v).strip().lower() in ("true", "1", "on", "yes")

    node = FlowerLoadTextFromFolder()
    try:
        result = node.load_text_from_folder(
            directory             = q.get("directory", ""),
            filter_keyword        = q.get("filter_keyword", ""),
            seed                  = _qi("seed", 1),
            continuous_processing = _qi("continuous_processing", 1),
            sort_mode             = q.get("sort_mode", "字母排序(Alphabetical)"),
            max_chars_per_segment = _qi("max_chars_per_segment", 100),
            split_symbols         = q.get("split_symbols", ",.?!;:，。？！；："),
            negativeKeyword       = q.get("negativeKeyword", ""),
            split_by_chapter      = q.get("split_by_chapter", "根據章編號分章輸出(第一章)"),
            chars_per_auto_chapter = _qi("chars_per_auto_chapter", 4000),
            replace_fullwidth_space = _qb("replace_fullwidth_space", True),
            append_fullwidth_period = _qb("append_fullwidth_period", True),
            strip_line_leading_space = _qb("strip_line_leading_space", True),
            remove_blank_lines = _qb("remove_blank_lines", True),
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
NODE_DISPLAY_NAME_MAPPINGS = {"FlowerLoadTextFromFolder": "🌸Flower Load Text From Folder(seed 從 1 開始)"}
