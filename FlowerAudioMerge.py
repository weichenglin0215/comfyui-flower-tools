# -*- coding: utf-8 -*-
"""
FlowerAudioMerge - 合併多個音訊檔案為單一輸出
支援 WAV、MP3、FLAC 輸入，統一重新取樣至 48000 Hz 後串接，
輸出 ComfyUI 標準 AUDIO 格式（waveform tensor + sample_rate）。

可選功能「消除長靜音」（remove_long_silence，預設開啟）：
載入每個音檔後、串接之前，先刪除音檔內持續過久的靜音
（通常是 AI 產生語音時遺留的錯誤），演算法與 FlowerCutSilence 節點相同。
"""

import os
import re
import json
import sys
import subprocess
import importlib
import importlib.util

from server import PromptServer
from aiohttp import web

# 支援的音檔副檔名對照表（用於目錄篩選）
_AUDIO_FORMATS = {
    "ALL":  [".wav", ".mp3", ".flac"],
    "WAV":  [".wav"],
    "MP3":  [".mp3"],
    "FLAC": [".flac"],
}

# torch、numpy 是 ComfyUI 必備依賴，一定存在
import numpy as np
import torch
import torch.nn.functional as F

# torchaudio 在大多數有音訊功能的 ComfyUI 環境中已安裝
# 若未安裝，節點會提示使用者透過 Install 按鈕安裝
try:
    import torchaudio
    _HAS_TORCHAUDIO = True
except ImportError:
    _HAS_TORCHAUDIO = False


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


def _empty_audio():
    """產生空白 ComfyUI AUDIO 物件，用於錯誤情況下的安全回傳。"""
    return {"waveform": torch.zeros(1, 2, 1), "sample_rate": 48000}


def _f32_pcm(wav):
    """將整數 PCM tensor 轉為 float32（-1 ~ 1）；本來就是浮點數則只統一為 float32。"""
    if wav.dtype.is_floating_point:
        return wav.float()
    if wav.dtype == torch.int16:
        return wav.float() / (2 ** 15)
    if wav.dtype == torch.int32:
        return wav.float() / (2 ** 31)
    raise ValueError(f"不支援的音訊資料型別：{wav.dtype}")


def _load_audio_file(path):
    """
    載入音檔，回傳 (waveform [聲道數, 取樣點數] float32, sample_rate)。

    依序嘗試三種讀取方式，前一種失敗才換下一種：
      1. torchaudio.load：原有做法。但新版 torchaudio（>= 2.9）的 load 必須另外安裝
         torchcodec，沒裝時所有檔案都會載入失敗，因此必須有備援。
      2. soundfile：WAV / FLAC（新版 libsndfile 也支援 MP3）。
      3. PyAV（av）：內建 FFmpeg 函式庫，WAV / MP3 / FLAC 都能讀，不需要另外安裝 ffmpeg。
         ComfyUI 核心的 LoadAudio 節點也是用 av 讀音檔，所以一定存在。
    三種都失敗時，丟出的例外訊息會包含各自的失敗原因，方便判斷問題。
    """
    errors = []

    # 1. torchaudio
    if _HAS_TORCHAUDIO:
        try:
            return torchaudio.load(path)
        except Exception as e:
            errors.append(f"torchaudio：{e}")

    # 2. soundfile
    try:
        import soundfile as sf
        data, sr = sf.read(path, dtype="float32", always_2d=True)  # shape = [取樣點, 聲道]
        return torch.from_numpy(data.T.copy()), sr                  # 轉成 [聲道, 取樣點]
    except Exception as e:
        errors.append(f"soundfile：{e}")

    # 3. PyAV
    try:
        import av
        with av.open(path) as container:
            if not container.streams.audio:
                raise ValueError("檔案中找不到音訊串流")
            stream = container.streams.audio[0]
            sr = stream.codec_context.sample_rate
            # 不同版本的 PyAV 取得聲道數的屬性不同，依序嘗試
            n_channels = getattr(getattr(stream.codec_context, "layout", None), "nb_channels", None) \
                or getattr(stream, "channels", None) or 1
            frames = []
            for frame in container.decode(stream):
                buf = torch.from_numpy(frame.to_ndarray())
                # packed（交錯）格式會是 [1, 點數×聲道]，需轉成 [聲道, 點數]；planar 格式已是 [聲道, 點數]
                if buf.shape[0] != n_channels:
                    buf = buf.reshape(-1, n_channels).t()
                frames.append(buf)
            if not frames:
                raise ValueError("沒有解出任何音訊資料")
            return _f32_pcm(torch.cat(frames, dim=1)), sr
    except Exception as e:
        errors.append(f"av：{e}")

    raise RuntimeError("；".join(errors))


# 偵測靜音用的幀長度（秒）：10 毫秒，兼顧解析度與速度
_FRAME_SEC = 0.01


def _cut_silence_single(waveform, sample_rate, threshold_db, min_silence_sec, keep_sec):
    """
    對單一音訊（shape = [聲道數, 取樣點數]）刪除過長的靜音。

    注意：這是 FlowerCutSilence.py 中同名函式的複本（專案規範：每個節點檔案各自獨立），
    修改演算法時兩邊必須同步。

    做法：
      1. 以 10 毫秒為一幀，取每幀所有聲道的「峰值」振幅，低於 threshold_db（dBFS）視為靜音幀。
      2. 連續靜音長度 >= min_silence_sec 的區段才會處理。
      3. 保留規則（目的：不切到有效內容的漸強／漸弱）：
           前後都有內容 → 保留靜音「前面」與「後面」各 keep_sec
           只有前面有內容（靜音一路到檔尾）→ 只保留靜音「前面」keep_sec
           只有後面有內容（靜音從檔首開始）→ 只保留靜音「後面」keep_sec

    回傳 (裁切後 waveform, 被裁切的靜音區段數)。
    """
    n = waveform.shape[-1]
    if n == 0:
        return waveform, 0

    # ── 1. 計算每幀峰值（所有聲道取最大絕對值）──────────────────────────────
    # 逐聲道比較以避免一次產生整份 abs 複本，長音檔較省記憶體
    amp = waveform[0].abs()
    for c in range(1, waveform.shape[0]):
        amp = torch.maximum(amp, waveform[c].abs())

    frame = max(1, int(round(sample_rate * _FRAME_SEC)))
    # max_pool1d 搭配 ceil_mode：最後一個不足一幀的尾巴也會被計入
    frame_peak = F.max_pool1d(amp.view(1, 1, -1), kernel_size=frame, stride=frame, ceil_mode=True)
    frame_peak = frame_peak.view(-1).cpu().numpy()

    # ── 2. 找出靜音幀，並整理成連續的靜音區段 [start, end)（單位：幀）─────────
    threshold_amp = 10.0 ** (threshold_db / 20.0)  # dBFS 轉線性振幅
    silent = (frame_peak < threshold_amp).astype(np.int8)
    edges = np.diff(np.concatenate(([0], silent, [0])))
    run_starts = np.flatnonzero(edges == 1)
    run_ends = np.flatnonzero(edges == -1)

    min_samples = int(round(min_silence_sec * sample_rate))
    keep_samples = int(round(keep_sec * sample_rate))

    # ── 3. 依條件決定要刪除的取樣點範圍 ─────────────────────────────────────
    keep_mask = torch.ones(n, dtype=torch.bool)
    cut_count = 0
    for fs, fe in zip(run_starts, run_ends):
        start = int(fs) * frame
        end = min(int(fe) * frame, n)
        if end - start < min_samples:
            continue  # 靜音不夠久，不處理

        left_keep = 0 if start == 0 else keep_samples      # 前面沒有內容就不必保留前面
        right_keep = 0 if end >= n else keep_samples       # 後面沒有內容就不必保留後面

        cut_start = start + left_keep
        cut_end = end - right_keep
        if cut_end > cut_start:
            keep_mask[cut_start:cut_end] = False
            cut_count += 1

    # 整段音訊都是靜音時，全部刪除會產生長度 0 的音訊而導致下游節點出錯，改為原樣輸出
    if not keep_mask.any():
        print("[FlowerAudioMerge] 警告：整段音訊皆低於臨界值，為避免輸出空音訊，保持原樣")
        return waveform, 0

    if cut_count == 0:
        return waveform, 0
    return waveform[:, keep_mask], cut_count


# 章節標籤正規式：從音檔檔名中抓取「第X章」標籤
_CHAPTER_LABEL_RE = re.compile(r'第[一二三四五六七八九十百千零〇兩0-9０-９]+章')

# 中文數字列表（用於章節標籤排序比對）
_CN_DIGITS = "一二三四五六七八九十百千零〇兩"

_CN_DIGIT_MAP = {'零': 0, '〇': 0, '一': 1, '二': 2, '兩': 2, '三': 3, '四': 4,
                  '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
_CN_UNIT_MAP = {'十': 10, '百': 100, '千': 1000}


def _cn_to_int(s: str):
    """將中文數字字串（如「七」「十二」「一百零三」）轉為 int；解析失敗回傳 None。"""
    if not s:
        return None
    if all(c in _CN_DIGIT_MAP for c in s):
        # 純個位數字串（如「一二三」不常見，通常單字），逐字取值；單字直接回傳
        if len(s) == 1:
            return _CN_DIGIT_MAP[s]
    total = 0
    section = 0
    num = 0
    has_unit = False
    for c in s:
        if c in _CN_DIGIT_MAP:
            num = _CN_DIGIT_MAP[c]
        elif c in _CN_UNIT_MAP:
            unit = _CN_UNIT_MAP[c]
            section += (num if num else 1) * unit
            num = 0
            has_unit = True
        else:
            return None
    total = section + num
    if not has_unit and total == 0 and s not in ('零', '〇'):
        return None
    return total


def _extract_chapter_label(filename: str) -> str:
    """從音檔檔名中提取章節標籤（如「第一章」）；找不到則回傳空字串。"""
    m = _CHAPTER_LABEL_RE.search(filename)
    return m.group(0) if m else ""


def _chapter_sort_key(label: str) -> tuple:
    """
    將「第X章」標籤轉換為可排序的 key。
    規則：阿拉伯數字直接轉 int；中文數字以字元在字串中的位置排序。
    空字串排在最後。
    """
    if not label:
        return (999999, label)
    # 提取 X 部分
    inner = label[1:-1]  # 去掉「第」和「章」
    # 若純阿拉伯數字
    digits_only = re.sub(r'[０-９]', lambda m: str(ord(m.group(0)) - ord('０')), inner)
    digits_only = re.sub(r'[0-9]+', lambda m: m.group(0), digits_only)
    if re.fullmatch(r'\d+', digits_only):
        return (int(digits_only), label)
    # 中文數字：以字元序列的字典序排序（簡易處理）
    return (0, label)


class FlowerAudioMerge:
    """
    ComfyUI 節點：從指定目錄中讀取音訊檔案，依使用者勾選清單串接為單一輸出。

    工作流程：
      1. 輸入目錄路徑、篩選關鍵字、輸入格式
      2. 點擊 refresh_btn 重新整理音檔清單（透過 JS 呼叫 API）
      3. 在清單中勾選要合併的音檔（無強制模式）或使用 seed 控制批次（強制模式）
      4. 執行節點，輸出合併後的 AUDIO tensor

    強制自動輸出模式（autoOutputMode）：
      - 無強制自動輸出：使用 fileConfigs 中的勾選狀態（原有功能）
      - 強制自動依章節分段輸出：seed 決定輸出哪個章節的所有音檔
      - 強制自動依檔案數量分段輸出：seed × autoSegmentCount 決定輸出哪批音檔
    """

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                # 1. 輸入目錄絕對路徑
                "directory": ("STRING", {"default": ""}),
                # 2. 篩選關鍵字：支援 '&'（AND）與 '|'（OR）分隔多關鍵字
                #    範例：'人物&台灣' 表示檔名需同時含有兩者；'台灣|日本' 表示含其中之一即可
                "filterKeyword": ("STRING", {"default": ""}),
                # 3. 排除關鍵字：從 filterKeyword 篩選結果中進一步排除符合的檔案
                #    同樣支援 '&'（AND）與 '|'（OR）分隔
                "negativeKeyword": ("STRING", {"default": ""}),
                # 4. 輸入音檔格式篩選
                "inputFormatSelector": (["ALL", "WAV", "MP3", "FLAC"],),
                # 5. 輸出檔名附加字串：附加在第一個勾選音檔的檔名（不含副檔名）之後
                "appendOutputName": ("STRING", {"default": "_Merge"}),
                # 6. 音檔勾選狀態（JSON 格式，由 JS 前端自動讀寫，用於工作流程儲存與還原）
                # 輸出格式與品質設定由下游 Save Audio 節點負責，本節點輸出格式無關的 float32 tensor
                "fileConfigs": ("STRING", {"multiline": True, "default": "{}"}),
                # 7. 強制自動輸出模式
                #    - 無強制自動輸出：使用 fileConfigs 中的勾選狀態（原有功能）
                #    - 強制自動依章節分段輸出：seed 決定輸出哪個章節的所有音檔
                #    - 強制自動依檔案數量分段輸出：seed × autoSegmentCount 決定輸出哪批音檔
                "autoOutputMode": (["無強制自動輸出", "強制自動依章節分段輸出", "強制自動依檔案數量分段輸出"],),
                # 8. seed：控制強制輸出時的批次索引
                #    ComfyUI 框架會自動在 seed 下方加入「生成後控制」（fixed/increment/decrement/randomize）
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                # 9. 自動分段的檔案數量：「強制自動依檔案數量分段輸出」模式下每批的檔案數
                "autoSegmentCount": ("INT", {"default": 4, "min": 1, "max": 9999}),
            },
            # 以下四個欄位放在 optional：前端會排在所有 required 欄位之後，
            # 舊工作流程（沒有這四個值）載入時不會因欄位位置位移而錯亂，並自動套用預設值
            "optional": {
                # 10. 消除長靜音：載入每個音檔後、串接之前，先刪除音檔內持續過久的靜音
                #     （通常是 AI 產生語音時遺留的錯誤）。預設勾選。
                "remove_long_silence": ("BOOLEAN", {
                    "default": True, "label_on": "消除長靜音", "label_off": "不消除",
                    "tooltip": "勾選時，每個音檔載入後會先刪除過長的靜音，之後才進行合併。",
                }),
                # 11~13. 以下三個參數與 🌸Flower Cut Silence 節點完全相同
                "threshold_db": ("FLOAT", {
                    "default": -20.0, "min": -100.0, "max": 0.0, "step": 0.5,
                    "tooltip": "臨界值(dB)：音量低於此值視為靜音，0 dB 為滿刻度。",
                }),
                "min_silence_sec": ("FLOAT", {
                    "default": 2.0, "min": 0.1, "max": 600.0, "step": 0.1,
                    "tooltip": "持續時間(秒)：靜音持續超過此長度才會被刪除。",
                }),
                "keep_sec": ("FLOAT", {
                    "default": 0.3, "min": 0.0, "max": 60.0, "step": 0.05,
                    "tooltip": "截斷前後的保留秒數：前後都有內容時，被刪除靜音的前面與後面各保留這麼多秒；"
                               "只有一側有內容時，只保留靠近內容那一側。",
                }),
            },
        }

    RETURN_TYPES = ("AUDIO", "INT", "FLOAT", "STRING")
    RETURN_NAMES = ("audio", "count", "length", "fileName")
    FUNCTION = "merge_audio"
    CATEGORY = "flower-tools"
    OUTPUT_NODE = True

    def merge_audio(self, directory, filterKeyword, negativeKeyword, inputFormatSelector,
                    appendOutputName, fileConfigs, autoOutputMode, seed, autoSegmentCount,
                    remove_long_silence=True, threshold_db=-20.0, min_silence_sec=2.0, keep_sec=0.3):
        """
        主執行方法：載入已勾選（或依 seed 自動選取）的音檔，統一重新取樣後串接，
        回傳 ComfyUI AUDIO 格式。

        autoOutputMode 三種模式：
          無強制自動輸出      - 使用 fileConfigs 的勾選狀態（原有邏輯）
          強制自動依章節分段  - 以 seed 決定章節索引，輸出該章節所有音檔
          強制自動依數量分段  - 以 seed × autoSegmentCount 決定檔案範圍

        輸出為格式無關的 float32 tensor，下游 Save Audio 節點負責格式轉換：
          - 取樣率：48000 Hz
          - 聲道數：依來源自動決定。全部音檔都是單聲道 → 單聲道（1 ch）；
                    只要有任何一個雙聲道 → 雙聲道（2 ch），單聲道檔案複製為雙聲道；
                    超過兩聲道的檔案（如 5.1）只取前兩聲道
          - 精度：float32

        回傳：
          audio    - ComfyUI AUDIO {waveform: Tensor(1, 1 或 2, N), sample_rate: 48000}
          count    - 實際成功載入並合併的音檔數量
          length   - 合併後總時長（秒，小數點以下兩位）
          fileName - 第一個被選取音檔的檔名（不含路徑與副檔名）+ appendOutputName
        """
        # 檢查 torchaudio 是否可用
        if not _HAS_TORCHAUDIO:
            err = "錯誤：torchaudio 未安裝，請在節點上點擊「安裝 torchaudio」按鈕。"
            return {"ui": {"text": [err]}, "result": (_empty_audio(), 0, 0.0, "")}

        # 驗證目錄存在
        base_dir = directory.strip()
        if not base_dir or not os.path.isdir(base_dir):
            err = f"錯誤：目錄不存在 [{base_dir}]"
            return {"ui": {"text": [err]}, "result": (_empty_audio(), 0, 0.0, "")}

        # 消除長靜音參數：未勾選時為 None，_do_merge 載入每個音檔時據此決定是否先裁切
        silence_opts = (threshold_db, min_silence_sec, keep_sec) if remove_long_silence else None

        # ── 強制自動輸出模式 ──────────────────────────────────────────────────────
        if autoOutputMode != "無強制自動輸出":
            return self._merge_auto(
                base_dir, filterKeyword, negativeKeyword, inputFormatSelector,
                appendOutputName, autoOutputMode, seed, autoSegmentCount, silence_opts
            )

        # ── 原有邏輯（無強制自動輸出）─────────────────────────────────────────────
        # 解析 fileConfigs JSON，取得已勾選的檔案清單
        try:
            configs = json.loads(fileConfigs) if fileConfigs.strip() else {}
        except json.JSONDecodeError:
            configs = {}

        # 固定字母排序篩選已啟用的檔案，確保串接順序穩定不受 JSON 鍵序影響
        enabled_files = sorted(
            [f for f, cfg in configs.items() if cfg.get("enabled", False)],
            key=lambda f: f.lower()
        )

        # 執行時同步套用 negativeKeyword：確保即使使用者未重新點擊 Refresh，
        # negativeKeyword 的排除邏輯仍然生效
        if negativeKeyword.strip():
            enabled_files = [f for f in enabled_files
                             if not _match_keyword_pattern(f, negativeKeyword)]

        if not enabled_files:
            msg = "尚未勾選任何音檔。請先點擊 Refresh 載入清單，再勾選要合併的音檔。"
            return {"ui": {"text": [msg]}, "result": (_empty_audio(), 0, 0.0, "")}

        return self._do_merge(base_dir, enabled_files, appendOutputName, silence_opts)

    def _get_filtered_files(self, base_dir, filterKeyword, negativeKeyword, inputFormatSelector):
        """
        取得目錄中符合格式與關鍵字篩選的音檔清單（字母排序）。
        用於強制自動輸出模式。
        """
        extensions = _AUDIO_FORMATS.get(inputFormatSelector, _AUDIO_FORMATS["ALL"])
        try:
            files = [f for f in os.listdir(base_dir)
                     if os.path.splitext(f)[1].lower() in extensions]
        except Exception as e:
            return []

        if filterKeyword.strip():
            files = [f for f in files if _match_keyword_pattern(f, filterKeyword)]
        if negativeKeyword.strip():
            files = [f for f in files if not _match_keyword_pattern(f, negativeKeyword)]
        files.sort(key=lambda f: f.lower())
        return files

    def _merge_auto(self, base_dir, filterKeyword, negativeKeyword, inputFormatSelector,
                    appendOutputName, autoOutputMode, seed, autoSegmentCount, silence_opts=None):
        """強制自動輸出模式的核心邏輯。seed 直接作為批次索引。"""
        all_files = self._get_filtered_files(base_dir, filterKeyword, negativeKeyword, inputFormatSelector)

        if not all_files:
            msg = "目錄中沒有符合條件的音檔。請確認目錄路徑與篩選設定。"
            return {"ui": {"text": [msg]}, "result": (_empty_audio(), 0, 0.0, "")}

        process_idx = seed  # seed 直接對應批次索引（生成後控制由 ComfyUI 框架處理）

        if autoOutputMode == "強制自動依章節分段輸出":
            enabled_files, chapter_label = self._select_by_chapter(all_files, process_idx)
        else:  # 強制自動依檔案數量分段輸出
            enabled_files = self._select_by_count(all_files, process_idx, autoSegmentCount)
            chapter_label = ""

        if not enabled_files:
            msg = f"強制自動輸出：目前 seed={seed} 對應批次無檔案（共 {len(all_files)} 個音檔）。"
            return {"ui": {"text": [msg]}, "result": (_empty_audio(), 0, 0.0, "")}

        # 章節模式下，附加章節標籤於 appendOutputName 之前
        effective_append = (f"-{chapter_label}" if chapter_label else "") + appendOutputName
        return self._do_merge(base_dir, enabled_files, effective_append, silence_opts)

    def _select_by_chapter(self, all_files, process_idx):
        """
        依章節分組，以 process_idx 選取章節。
        回傳 (selected_files, chapter_label)。
        無法找到任何章節標籤時，將全部檔案視為單一章節。
        """
        # 分組：chapter_label → [filename, ...]（依原始字母排序）
        groups = {}  # OrderedDict 效果：先出現的 key 在前
        order = []   # 維持章節首次出現的順序

        for f in all_files:
            label = _extract_chapter_label(f)
            if label not in groups:
                groups[label] = []
                order.append(label)
            groups[label].append(f)

        # 若只有空字串 key（完全沒有章節標籤），將全部視為單一組
        if order == [""]:
            print(f"[FlowerAudioMerge] ⚠ 檔名中找不到章節標籤，全部視為單一章節輸出")
            return all_files, ""

        # 依章節標籤排序：先嘗試從標籤抽出數字做數值排序
        def _label_sort_key(label):
            if not label:
                return (999999, label)
            inner = label[1:-1]  # 去掉「第」和「章」
            # 全形數字轉半形
            inner_hf = ''.join(
                chr(ord(c) - ord('０') + ord('0')) if '０' <= c <= '９' else c
                for c in inner
            )
            if re.fullmatch(r'\d+', inner_hf):
                return (int(inner_hf), label)
            # 中文數字：轉換為實際數值排序
            cn_val = _cn_to_int(inner_hf)
            if cn_val is not None:
                return (cn_val, label)
            # 無法解析時退回字串排序，並排在最後避免影響已知章節順序
            return (999998, label)

        sorted_labels = sorted([l for l in order if l], key=_label_sort_key)
        # 空標籤（無章節）放最後
        if "" in order:
            sorted_labels.append("")

        chapter_idx = process_idx % len(sorted_labels)
        chosen_label = sorted_labels[chapter_idx]
        selected_files = groups[chosen_label]

        print(f"[FlowerAudioMerge] 強制依章節：seed→process_idx={process_idx}, "
              f"章節={chosen_label}（共 {len(sorted_labels)} 章），"
              f"選取 {len(selected_files)} 個檔案")
        return selected_files, chosen_label

    def _select_by_count(self, all_files, process_idx, autoSegmentCount):
        """
        依固定數量分批，以 process_idx 選取批次。
        seed=0 → 第1批（索引0~N-1），seed=1 → 第2批，依此類推。
        超出範圍時取最後一批（不循環，避免重複輸出）。
        """
        n = len(all_files)
        total_batches = max(1, (n + autoSegmentCount - 1) // autoSegmentCount)
        batch_idx = process_idx % total_batches  # 以 modulo 循環
        start = batch_idx * autoSegmentCount
        end = min(start + autoSegmentCount, n)
        selected = all_files[start:end]

        print(f"[FlowerAudioMerge] 強制依數量：seed→process_idx={process_idx}, "
              f"批次={batch_idx+1}/{total_batches}，"
              f"選取索引 {start}~{end-1}，共 {len(selected)} 個檔案")
        return selected

    def _do_merge(self, base_dir, enabled_files, appendOutputName, silence_opts=None):
        """
        載入指定音檔清單，統一重新取樣後串接，回傳 ComfyUI AUDIO 格式。

        silence_opts 為 (threshold_db, min_silence_sec, keep_sec) 或 None：
        不是 None 時，每個音檔載入並統一格式後，會先刪除長靜音，之後才進行串接。
        """
        TARGET_SR = 48000  # 統一取樣率：48000 Hz
        segments = []
        load_errors = []   # 記錄載入失敗檔案的原因，全部失敗時顯示在節點的預覽欄

        for filename in enabled_files:
            full_path = os.path.join(base_dir, filename)
            if not os.path.isfile(full_path):
                print(f"[FlowerAudioMerge] ⚠ 檔案不存在，跳過：{filename}")
                continue
            try:
                # 載入音訊：torchaudio 失敗（例如新版需要 torchcodec 卻沒安裝）時，
                # 自動改用 soundfile 或 PyAV 讀取，見 _load_audio_file
                waveform, sr = _load_audio_file(full_path)

                # ── 振幅安全防護 ──────────────────────────────────────────────────
                # torchaudio 載入不同格式的音訊時，正規化行為可能不同：
                # - 16/24-bit PCM WAV：自動正規化至 [-1, 1] ✓
                # - 32-bit float WAV：原始值直讀，若某些 TTS 工具以 int 範圍值儲存
                #   （如 ±32768）就會導致下游節點爆音
                # 解法：峰值超過 1.0 時強制正規化，保持各檔案相對振幅比例
                peak = waveform.abs().max().item()
                if peak > 1.0:
                    waveform = waveform / peak
                    print(f"[FlowerAudioMerge]   ⚠ 振幅已正規化（原峰值 {peak:.3f} → 1.0）")

                # 重新取樣至目標取樣率 48000 Hz
                if sr != TARGET_SR:
                    waveform = torchaudio.functional.resample(waveform, sr, TARGET_SR)

                # 多聲道（如 5.1）→ 只取前兩聲道；單聲道此時先維持原樣，
                # 等全部音檔載入後再依整批來源決定輸出為單聲道或雙聲道（見下方串接前的處理）。
                # 這裡不能先複製成雙聲道：全部都是單聲道時要保持單聲道輸出。
                if waveform.shape[0] > 2:
                    waveform = waveform[:2, :]

                # ── 消除長靜音：在串接之前，先對這個音檔單獨處理 ──────────────────
                # 長靜音通常是 AI 產生語音時遺留的錯誤；必須在串接前處理，
                # 才不會把相鄰音檔的首尾靜音誤判為同一段而影響保留規則
                if silence_opts is not None:
                    before_sec = waveform.shape[1] / TARGET_SR
                    waveform, cut_count = _cut_silence_single(waveform, TARGET_SR, *silence_opts)
                    if cut_count:
                        after_sec = waveform.shape[1] / TARGET_SR
                        print(f"[FlowerAudioMerge]   已刪除 {cut_count} 段長靜音"
                              f"（{before_sec:.2f}s → {after_sec:.2f}s）")

                segments.append(waveform)
                dur = waveform.shape[1] / TARGET_SR
                print(f"[FlowerAudioMerge] ✓ 已載入：{filename}（{dur:.2f}s）")
            except Exception as e:
                print(f"[FlowerAudioMerge] ✗ 載入失敗 {filename}：{e}")
                load_errors.append(f"{filename}：{e}")

        if not segments:
            if load_errors:
                # 直接顯示第一個檔案的真正失敗原因，避免誤導使用者往錯誤方向排查
                err = ("錯誤：所有選取的音檔均無法載入。\n"
                       f"第一個失敗原因 → {load_errors[0]}\n"
                       "（各檔案的詳細原因請查看 ComfyUI 後台輸出視窗）")
            else:
                err = "錯誤：所選音檔都不存在。請先點擊 Refresh 重新整理清單。"
            return {"ui": {"text": [err]}, "result": (_empty_audio(), 0, 0.0, "")}

        # ── 依來源音檔自動決定輸出聲道數 ────────────────────────────────────────
        # 規則：所有音檔都是單聲道 → 輸出單聲道（1 ch）；只要有任何一個是雙聲道 → 輸出雙聲道（2 ch），
        # 此時單聲道的音檔複製為雙聲道（左右相同），避免串接時聲道數不一致而失敗，也不會遺失雙聲道的左右差異。
        # 靜音刪除是看「各聲道的峰值」，單聲道與複製後的雙聲道結果相同，所以先刪靜音再統一聲道不影響結果。
        out_channels = max(seg.shape[0] for seg in segments)
        segments = [seg.repeat(out_channels, 1) if seg.shape[0] != out_channels else seg
                    for seg in segments]

        # 沿時間軸（dim=1）串接所有音訊片段
        merged = torch.cat(segments, dim=1)

        # 計算統計資訊
        count = len(segments)
        length_sec = round(merged.shape[1] / TARGET_SR, 2)
        print(f"[FlowerAudioMerge] 輸出聲道數：{out_channels}（{'單聲道' if out_channels == 1 else '雙聲道'}）")

        # 組成 ComfyUI AUDIO 格式：shape = (batch=1, channels=1 或 2, samples=N)
        audio_out = {
            "waveform": merged.unsqueeze(0),
            "sample_rate": TARGET_SR,
        }

        # fileName：第一個被選取音檔的檔名（去除副檔名）+ appendOutputName
        first_name_no_ext = os.path.splitext(enabled_files[0])[0]
        file_name_out = first_name_no_ext + appendOutputName

        # 顯示已成功合併的音檔清單（每行一個檔名）
        display_text = "\n".join(enabled_files[:count])

        print(f"[FlowerAudioMerge] 合併完成：{count} 個音檔，總時長 {length_sec}s，fileName={file_name_out}")

        return {
            "ui": {"text": [display_text]},
            "result": (audio_out, count, length_sec, file_name_out),
        }


# ── API 端點 ──────────────────────────────────────────────────────────────────

@PromptServer.instance.routes.get("/flower-tools/list-audio-files")
async def list_audio_files(request):
    """
    列出指定目錄中符合格式與關鍵字篩選的音檔清單（固定字母排序）。

    Query params：
      directory       - 目錄絕對路徑
      format          - 格式篩選（ALL / WAV / MP3 / FLAC），預設 ALL
      keyword         - 篩選關鍵字（支援 & AND、| OR；空白 = 不篩選）
      negativeKeyword - 排除關鍵字（同上語法；空白 = 不排除）
    """
    directory        = request.rel_url.query.get("directory", "").strip()
    fmt_filter       = request.rel_url.query.get("format", "ALL")
    keyword          = request.rel_url.query.get("keyword", "").strip()
    negative_keyword = request.rel_url.query.get("negativeKeyword", "").strip()

    if not directory or not os.path.isdir(directory):
        return web.json_response({"error": "目錄不存在"}, status=404)

    extensions = _AUDIO_FORMATS.get(fmt_filter, _AUDIO_FORMATS["ALL"])

    try:
        files = [f for f in os.listdir(directory)
                 if os.path.splitext(f)[1].lower() in extensions]
        # 套用篩選關鍵字
        if keyword:
            files = [f for f in files if _match_keyword_pattern(f, keyword)]
        # 套用排除關鍵字
        if negative_keyword:
            files = [f for f in files if not _match_keyword_pattern(f, negative_keyword)]
        # 固定字母排序
        files.sort(key=lambda f: f.lower())
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

    return web.json_response({"files": files})


@PromptServer.instance.routes.get("/flower-tools/check-torchaudio")
async def check_torchaudio(request):
    """檢查 torchaudio 是否已安裝並回傳版本資訊。"""
    try:
        importlib.invalidate_caches()
        spec = importlib.util.find_spec("torchaudio")
        if spec is not None:
            import torchaudio as _ta
            return web.json_response({"installed": True, "version": _ta.__version__})
        return web.json_response({"installed": False})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


@PromptServer.instance.routes.post("/flower-tools/install-torchaudio")
async def install_torchaudio(request):
    """嘗試透過 pip 安裝 torchaudio。"""
    print("--- Flower Tools: 嘗試安裝 torchaudio ---")
    try:
        cmd = [sys.executable, "-m", "pip", "install", "torchaudio"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        log = result.stdout + "\n" + result.stderr
        print(log)
        success = result.returncode == 0
        print(f"--- Flower Tools: torchaudio 安裝{'成功' if success else '失敗'} ---")
        return web.json_response({"success": success, "log": log})
    except Exception as e:
        print(f"--- Flower Tools: 安裝 torchaudio 時發生例外：{e} ---")
        return web.json_response({"success": False, "log": str(e)}, status=500)


NODE_CLASS_MAPPINGS = {"FlowerAudioMerge": FlowerAudioMerge}
NODE_DISPLAY_NAME_MAPPINGS = {"FlowerAudioMerge": "🌸Flower Audio Merge"}
