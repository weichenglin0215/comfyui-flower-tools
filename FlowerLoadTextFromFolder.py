# -*- coding: utf-8 -*-
import os
import re


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


class FlowerLoadTextFromFolder:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                # 1. 絕對目錄路徑
                "directory": ("STRING", {"default": ""}),
                # 2. 篩選關鍵字（空白＝全選）
                "filter_keyword": ("STRING", {"default": ""}),
                # 3. seed：指定選取第幾個檔案
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                # 4. 生成後控制，與 seed 綁定
                "continuous_processing": ("INT", {"default": 1, "min": 1, "max": 9999999}),
                # 5. 排序方式
                "sort_mode": (["字母排序(Alphabetical)", "自然排序(Natural)"],),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("text", "full_path", "file_name", "file_name_no_ext")
    FUNCTION = "load_text_from_folder"
    CATEGORY = "flower-tools"
    OUTPUT_NODE = True

    def load_text_from_folder(self, directory, filter_keyword, seed, continuous_processing, sort_mode):
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

        # 以關鍵字篩選檔名
        keyword = filter_keyword.strip()
        if keyword:
            all_files = [f for f in all_files if keyword in f]

        # 排序
        all_files.sort(key=_get_sort_key(sort_mode))

        if not all_files:
            empty_msg = "沒有符合條件的文字檔案"
            return {"ui": {"text": [empty_msg], "file_list": [""]}, "result": (empty_msg, "", "", "")}

        # 根據 seed 與 continuous_processing 算出檔案索引
        process_idx = seed // max(1, continuous_processing)
        file_idx = process_idx % len(all_files)

        selected_file = all_files[file_idx]
        full_path = os.path.join(base_dir, selected_file)
        file_name_no_ext = os.path.splitext(selected_file)[0]

        # 讀取檔案內容
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
            "ui": {
                "text": [content],
                "file_list": [file_list_str],
            },
            "result": (content, full_path, selected_file, file_name_no_ext),
        }


NODE_CLASS_MAPPINGS = {"FlowerLoadTextFromFolder": FlowerLoadTextFromFolder}
NODE_DISPLAY_NAME_MAPPINGS = {"FlowerLoadTextFromFolder": "🌸Flower Load Text From Folder"}
