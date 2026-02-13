class FlowerStringComparison:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "字串A": ("STRING", {"multiline": True, "default": ""}),
                "字串B": ("STRING", {"multiline": True, "default": ""}),
                "比對模式": (["從前面開始比對", "從後面開始比對"], {"default": "從前面開始比對"}),
                "比對次數": ("INT", {"default": 1, "min": 1, "max": 999, "step": 1}),
                "區分大小寫": ("BOOLEAN", {"default": True}),
            }
        }

    RETURN_TYPES = ("INT", "BOOLEAN")
    RETURN_NAMES = ("位置", "是否有找到")
    FUNCTION = "compare"
    CATEGORY = "flower-tools"

    def compare(self, 字串A, 字串B, 比對模式, 比對次數, 區分大小寫):
        a = 字串A
        b = 字串B

        if not 區分大小寫:
            a = a.lower()
            b = b.lower()

        index = -1
        if 比對模式 == "從前面開始比對":
            current_pos = -1
            for _ in range(比對次數):
                current_pos = a.find(b, current_pos + 1)
                if current_pos == -1:
                    break
            index = current_pos
        else: # 從後面開始比對
            current_pos = len(a)
            for _ in range(比對次數):
                if current_pos <= 0:
                    current_pos = -1
                    break
                current_pos = a.rfind(b, 0, current_pos)
                if current_pos == -1:
                    break
            index = current_pos

        found = index != -1
        return (index, found)

NODE_CLASS_MAPPINGS = {
    "FlowerStringComparison": FlowerStringComparison
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "FlowerStringComparison": "🌸Flower 字串比對"
}
