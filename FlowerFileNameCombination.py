import datetime
import re


def _sanitize_path(s, keep_trailing_slash=False):
    """
    統一路徑分隔符、折疊重複斜線、移除非法字元。
    適用於含路徑的輸出：FullNameOut、PathNameOut。
    Windows 磁碟機代號（如 C:）中的冒號會保留，其餘 : 一律移除。
    """
    # 1. 反斜線統一為正斜線
    s = s.replace('\\', '/')
    # 2. 移除非法字元（路徑中的 / 保留）
    s = re.sub(r'[*?"<>|]', '', s)
    # 3. 保留磁碟機代號的冒號（如 C:），其餘 : 移除
    drive = ''
    if len(s) >= 2 and s[1] == ':' and s[0].isalpha():
        drive = s[:2]
        s = s[2:]
    s = s.replace(':', '')
    s = drive + s
    # 4. 記錄原始是否有尾部斜線（給 PathNameOut 保留用）
    had_trailing = s.endswith('/')
    # 5. 折疊連續斜線（// → /）
    s = re.sub(r'/+', '/', s)
    # 6. 清除各路徑段落的頭尾空白，移除因此產生的空段落
    parts = s.split('/')
    if parts and parts[0] == '':
        # Unix 絕對路徑開頭空段落須保留
        cleaned = [''] + [p.strip() for p in parts[1:] if p.strip()]
    else:
        cleaned = [p.strip() for p in parts if p.strip()]
    s = '/'.join(cleaned)
    # 7. 視需求保留/移除尾部斜線
    if keep_trailing_slash and had_trailing and not s.endswith('/'):
        s += '/'
    return s


def _sanitize_filename(s):
    """
    移除所有路徑分隔符與非法字元，適用於純檔名輸出：FileNameOut。
    """
    # 移除路徑分隔符
    s = s.replace('\\', '').replace('/', '')
    # 移除非法字元
    s = re.sub(r'[*?"<>|:]', '', s)
    return s.strip()


class FlowerFileNameCombination:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "MainFolderName": ("STRING", {"default": "WildcardsTest/Z-Image-Turbo"}),
                "SubFolderName": ("STRING", {"default": "ArtStyle_Test"}),
                "same_as_subfolder": ("BOOLEAN", {"default": True}),
                "FileName": ("STRING", {"default": "ArtStyle_Test"}),
                "Suffix": ("STRING", {"default": "Take2"}),
                "DATE_format": ("STRING", {"default": "%Y-%m-%d"}),
                "TIME_format": ("STRING", {"default": "%H-%M-%S"}),
                "DATETIME_format": ("STRING", {"default": "%Y%m%d-%H%M%S"}),
                "FullNameFormat": ("STRING", {"default": "%MainFolderName/%DATE-%SubFolderName/%FileName-%DATETIME-%Suffix"}),
                "PathNameFormat": ("STRING", {"default": "%MainFolderName/%DATE-%SubFolderName/"}),
                "FileNameFormat": ("STRING", {"default": "%FileName-%DATETIME-%Suffix"}),
            }
        }

    @classmethod
    def IS_CHANGED(s, **kwargs):
        # 回傳當前時間戳，強制 ComfyUI 認為節點已改變，從而重新執行並更新時間
        import time
        return time.time()

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("FullNameOut", "PathNameOut", "FileNameOut")
    FUNCTION = "process"
    CATEGORY = "flower-tools"

    def process(self, MainFolderName, SubFolderName, same_as_subfolder, FileName, Suffix, 
                DATE_format, TIME_format, DATETIME_format, 
                FullNameFormat, PathNameFormat, FileNameFormat, note=""):
        
        # Handle same_as_subfolder logic
        actual_file_name = SubFolderName if same_as_subfolder else FileName
        
        # Get current time
        now = datetime.datetime.now()
        
        # Format time strings
        date_str = now.strftime(DATE_format)
        time_str = now.strftime(TIME_format)
        datetime_str = now.strftime(DATETIME_format)
        
        # Replacement mapping
        replacements = {
            "%MainFolderName": MainFolderName,
            "%SubFolderName": SubFolderName,
            "%FileName": actual_file_name,
            "%Suffix": Suffix,
            "%DATETIME": datetime_str,
            "%DATE": date_str,
            "%TIME": time_str,
        }
        
        def apply_format(fmt):
            result = fmt
            for key, val in replacements.items():
                result = result.replace(key, val)
            return result

        # 套用格式樣板後，對三種輸出各別進行路徑/檔名規格修正
        full_name_out = _sanitize_path(apply_format(FullNameFormat), keep_trailing_slash=False)
        path_name_out = _sanitize_path(apply_format(PathNameFormat), keep_trailing_slash=True)
        file_name_out = _sanitize_filename(apply_format(FileNameFormat))

        return (full_name_out, path_name_out, file_name_out)

NODE_CLASS_MAPPINGS = {
    "FlowerFileNameCombination": FlowerFileNameCombination
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "FlowerFileNameCombination": "🌸Flower File Name Combination"
}
