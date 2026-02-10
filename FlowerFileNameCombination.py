import datetime

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
                "note": ("STRING", {"multiline": True, "default": "🌸 使用變數標籤：\n%MainFolderName, %SubFolderName, %FileName, %Suffix, %DATE, %TIME, %DATETIME"}),
            }
        }

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
            "%DATE": date_str,
            "%TIME": time_str,
            "%DATETIME": datetime_str,
        }
        
        def apply_format(fmt):
            result = fmt
            for key, val in replacements.items():
                result = result.replace(key, val)
            return result

        full_name_out = apply_format(FullNameFormat)
        path_name_out = apply_format(PathNameFormat)
        file_name_out = apply_format(FileNameFormat)
        
        return (full_name_out, path_name_out, file_name_out)

NODE_CLASS_MAPPINGS = {
    "FlowerFileNameCombination": FlowerFileNameCombination
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "FlowerFileNameCombination": "🌸Flower File Name Combination"
}
