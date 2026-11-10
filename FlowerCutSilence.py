# -*- coding: utf-8 -*-
"""
FlowerCutSilence - 偵測並刪除音訊中過長的靜音片段

運作方式（概念上與 Audacity「截斷靜音 Truncate Silence」相同）：
  1. 將音訊切成 10 毫秒的小幀，取每幀所有聲道的「峰值」振幅。
  2. 峰值低於 threshold_db（相對於滿刻度 dBFS）的幀視為靜音幀。
  3. 連續靜音幀累積長度 >= min_silence_sec 的區段才會被處理。
  4. 處理方式：保留該靜音區段「開頭」與「結尾」各 keep_sec 秒，刪除中間部分，
     讓前後語音之間仍保有自然的停頓。
  5. 輸出裁切後的 AUDIO，並存成暫存 WAV 供前端播放器試聽。
"""

import os
import re
import glob
import uuid
import wave

import numpy as np
import torch
import torch.nn.functional as F

import folder_paths

# 偵測用的幀長度（秒）：10 毫秒，兼顧解析度與速度
_FRAME_SEC = 0.01

# 試聽檔案的檔名前綴（同一個節點的舊試聽檔會在新檔產生前被清除）
_PREVIEW_PREFIX = "FlowerCutSilence"


def _cut_silence_single(waveform, sample_rate, threshold_db, min_silence_sec, keep_sec):
    """
    對單一音訊（shape = [聲道數, 取樣點數]）執行靜音裁切。

    回傳 (裁切後 waveform, 被裁切的靜音區段數)。

    注意：本函式在 FlowerAudioMerge.py 中有一份相同的複本（專案規範：每個節點檔案各自獨立），
    修改演算法時兩邊必須同步。
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

        # 保留規則（目的：不切到有效內容的漸強／漸弱）：
        #   前後都有內容 → 保留靜音「前面」與「後面」各 keep_sec
        #   只有前面有內容（靜音一路到檔尾）→ 只保留靜音「前面」keep_sec
        #   只有後面有內容（靜音從檔首開始）→ 只保留靜音「後面」keep_sec
        left_keep = 0 if start == 0 else keep_samples      # 前面沒有內容就不必保留前面
        right_keep = 0 if end >= n else keep_samples       # 後面沒有內容就不必保留後面

        cut_start = start + left_keep
        cut_end = end - right_keep
        if cut_end > cut_start:
            keep_mask[cut_start:cut_end] = False
            cut_count += 1

    # 整段音訊都是靜音時，全部刪除會產生長度 0 的音訊而導致下游節點出錯，改為原樣輸出
    if not keep_mask.any():
        print("[FlowerCutSilence] 警告：整段音訊皆低於臨界值，為避免輸出空音訊，保持原樣輸出")
        return waveform, 0

    if cut_count == 0:
        return waveform, 0
    return waveform[:, keep_mask], cut_count


def _save_preview_wav(waveform, sample_rate, path):
    """
    將 waveform（[聲道數, 取樣點數]，float -1~1）以 16-bit PCM 存成 WAV，供瀏覽器試聽。
    使用 Python 內建 wave 模組，不依賴 torchaudio / ffmpeg。
    """
    preview = waveform[:2]  # 瀏覽器試聽只需前兩個聲道
    pcm = (preview.clamp(-1.0, 1.0) * 32767.0).round().to(torch.int16)
    # (聲道, 點數) 轉置為 (點數, 聲道) 後攤平即為 WAV 要求的交錯排列
    data = pcm.t().contiguous().cpu().numpy().astype("<i2").tobytes()
    with wave.open(path, "wb") as wf:
        wf.setnchannels(preview.shape[0])
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(data)


class FlowerCutSilence:
    """
    ComfyUI 節點：偵測音檔中超過指定長度的靜音並刪除。

    輸入 audio、輸出 audio；節點上附有播放器，執行後可直接試聽裁切結果。
    """

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "audio": ("AUDIO",),
                # 1. 臨界值：峰值低於此分貝（dBFS）視為靜音
                "threshold_db": ("FLOAT", {
                    "default": -20.0, "min": -100.0, "max": 0.0, "step": 0.5,
                    "tooltip": "臨界值(dB)：音量低於此值視為靜音，0 dB 為滿刻度。",
                }),
                # 2. 持續時間：靜音至少持續多久才會被刪除
                "min_silence_sec": ("FLOAT", {
                    "default": 2.0, "min": 0.1, "max": 600.0, "step": 0.1,
                    "tooltip": "持續時間(秒)：靜音持續超過此長度才會被刪除。",
                }),
                # 3. 保留秒數：刪除靜音時，在截斷點前後各保留多少秒
                "keep_sec": ("FLOAT", {
                    "default": 0.3, "min": 0.0, "max": 60.0, "step": 0.05,
                    "tooltip": "截斷前後的保留秒數：被刪除的靜音區段，其開頭與結尾各保留這麼多秒。",
                }),
            },
            # unique_id 用來讓每個節點只留一個試聽暫存檔
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("AUDIO",)
    RETURN_NAMES = ("audio",)
    FUNCTION = "cut_silence"
    CATEGORY = "flower-tools"
    OUTPUT_NODE = True

    def cut_silence(self, audio, threshold_db, min_silence_sec, keep_sec, unique_id=None):
        """
        主執行方法：逐批次（batch）裁切靜音，回傳裁切後的 AUDIO，
        並在 ui 中附上試聽暫存檔資訊（鍵名 flower_audio_preview，由 web/extension.js 讀取）。
        """
        waveform = audio["waveform"]          # shape: [batch, 聲道, 取樣點]
        sample_rate = int(audio["sample_rate"])

        # 逐批次處理；各批次裁切後長度可能不同，結尾補零對齊後再堆疊
        results = []
        total_cuts = 0
        for b in range(waveform.shape[0]):
            cut_wave, cuts = _cut_silence_single(
                waveform[b], sample_rate, threshold_db, min_silence_sec, keep_sec
            )
            results.append(cut_wave)
            total_cuts += cuts

        max_len = max(r.shape[-1] for r in results)
        results = [F.pad(r, (0, max_len - r.shape[-1])) if r.shape[-1] < max_len else r
                   for r in results]
        out_wave = torch.stack(results, dim=0)

        old_sec = waveform.shape[-1] / sample_rate
        new_sec = out_wave.shape[-1] / sample_rate
        print(f"[FlowerCutSilence] 裁切 {total_cuts} 段靜音："
              f"{old_sec:.2f}s → {new_sec:.2f}s（刪除 {old_sec - new_sec:.2f}s）")

        audio_out = {"waveform": out_wave, "sample_rate": sample_rate}

        # ── 產生試聽暫存檔（只預覽第一個批次）──────────────────────────────────
        ui = {}
        try:
            temp_dir = folder_paths.get_temp_directory()
            # unique_id 在子圖中可能含有「:」，Windows 檔名不允許，需先清理
            safe_id = re.sub(r"[^0-9A-Za-z_-]", "_", str(unique_id))
            # 先清掉此節點上一次的試聽檔，避免長音檔反覆執行佔滿暫存空間
            for old in glob.glob(os.path.join(temp_dir, f"{_PREVIEW_PREFIX}_{safe_id}_*.wav")):
                try:
                    os.remove(old)
                except OSError:
                    pass  # 檔案可能仍被瀏覽器串流中，略過即可
            filename = f"{_PREVIEW_PREFIX}_{safe_id}_{uuid.uuid4().hex[:8]}.wav"
            _save_preview_wav(out_wave[0], sample_rate, os.path.join(temp_dir, filename))
            ui = {"flower_audio_preview": [{"filename": filename, "subfolder": "", "type": "temp"}]}
        except Exception as e:
            # 試聽檔失敗不應中斷工作流程，音訊輸出仍然正常
            print(f"[FlowerCutSilence] 警告：試聽檔產生失敗：{e}")

        return {"ui": ui, "result": (audio_out,)}


NODE_CLASS_MAPPINGS = {"FlowerCutSilence": FlowerCutSilence}
NODE_DISPLAY_NAME_MAPPINGS = {"FlowerCutSilence": "🌸Flower Cut Silence"}
