# -*- coding: utf-8 -*-
"""
FlowerAudioMerge - 合併多個音訊檔案為單一輸出
支援 WAV、MP3、FLAC 輸入，統一重新取樣至 48000 Hz 後串接，
輸出 ComfyUI 標準 AUDIO 格式（waveform tensor + sample_rate）。
"""

import os
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

# torch 是 ComfyUI 必備依賴，一定存在
import torch

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


class FlowerAudioMerge:
    """
    ComfyUI 節點：從指定目錄中讀取音訊檔案，依使用者勾選清單串接為單一輸出。

    工作流程：
      1. 輸入目錄路徑、篩選關鍵字、輸入格式
      2. 點擊 refresh_btn 重新整理音檔清單（透過 JS 呼叫 API）
      3. 在清單中勾選要合併的音檔
      4. 執行節點，輸出合併後的 AUDIO tensor
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
            },
        }

    RETURN_TYPES = ("AUDIO", "INT", "FLOAT", "STRING")
    RETURN_NAMES = ("audio", "count", "length", "fileName")
    FUNCTION = "merge_audio"
    CATEGORY = "flower-tools"
    OUTPUT_NODE = True

    def merge_audio(self, directory, filterKeyword, negativeKeyword, inputFormatSelector,
                    appendOutputName, fileConfigs):
        """
        主執行方法：載入已勾選音檔，統一重新取樣後串接，回傳 ComfyUI AUDIO 格式。

        輸出為格式無關的 float32 tensor，下游 Save Audio 節點負責格式轉換：
          - 取樣率：48000 Hz
          - 聲道數：立體聲（2 ch），單聲道複製為雙聲道
          - 精度：float32

        回傳：
          audio    - ComfyUI AUDIO {waveform: Tensor(1, 2, N), sample_rate: 48000}
          count    - 實際成功載入並合併的音檔數量
          length   - 合併後總時長（秒，小數點以下兩位）
          fileName - 第一個被勾選音檔的檔名（不含路徑與副檔名）+ appendOutputName
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

        TARGET_SR = 48000  # 統一取樣率：48000 Hz
        segments = []

        for filename in enabled_files:
            full_path = os.path.join(base_dir, filename)
            if not os.path.isfile(full_path):
                print(f"[FlowerAudioMerge] ⚠ 檔案不存在，跳過：{filename}")
                continue
            try:
                # 載入音訊（torchaudio 支援 WAV、FLAC；MP3 需要 ffmpeg backend）
                waveform, sr = torchaudio.load(full_path)

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

                # 統一聲道數為立體聲（2 聲道）
                if waveform.shape[0] == 1:
                    # 單聲道 → 複製為雙聲道
                    waveform = waveform.repeat(2, 1)
                elif waveform.shape[0] > 2:
                    # 多聲道（如 5.1）→ 只取前兩聲道
                    waveform = waveform[:2, :]

                segments.append(waveform)
                dur = waveform.shape[1] / TARGET_SR
                print(f"[FlowerAudioMerge] ✓ 已載入：{filename}（{dur:.2f}s）")
            except Exception as e:
                print(f"[FlowerAudioMerge] ✗ 載入失敗 {filename}：{e}")

        if not segments:
            err = "錯誤：所有選取的音檔均無法載入。MP3 格式需要 ffmpeg，請確認是否已安裝。"
            return {"ui": {"text": [err]}, "result": (_empty_audio(), 0, 0.0, "")}

        # 沿時間軸（dim=1）串接所有音訊片段
        merged = torch.cat(segments, dim=1)

        # 計算統計資訊
        count = len(segments)
        length_sec = round(merged.shape[1] / TARGET_SR, 2)

        # 組成 ComfyUI AUDIO 格式：shape = (batch=1, channels=2, samples=N)
        audio_out = {
            "waveform": merged.unsqueeze(0),
            "sample_rate": TARGET_SR,
        }

        # fileName：第一個被勾選音檔的檔名（去除副檔名）+ appendOutputName
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
