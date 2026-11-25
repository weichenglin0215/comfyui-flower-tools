# -*- coding: utf-8 -*-
"""
🌸Flower Int From Lines

輸入多行文字（每一行一個整數），依 seed 取出其中一行，輸出成 INT。
"""


def _parse_int_lines(text):
    """
    把多行文字解析成整數清單。

    規則：
      - 每一行一個整數；整數後面可以加逗號，也可以不加（半形 , 或全形 ， 都能辨識）。
      - 整數前後與逗號前後的空白會忽略；可以是負數（例如 -5,）。
      - 空白行直接忽略，不算一行（所以結尾多按了 Enter 不會多出一個項目）。
      - 任何一行不是整數就丟出錯誤並指出是第幾行，不會默默跳過
        （默默跳過會讓後面各行的順序往前移，輸出錯的數字卻不易發現）。

    錯誤訊息中的「第幾行」是文字框中實際的行號（空白行也計入），方便直接找到那一行。
    """
    values = []
    # 開頭的 BOM（\ufeff）常見於 Windows 記事本存出的 .txt；若由其他節點讀檔接進來，要先去掉
    for line_no, raw in enumerate(text.lstrip("\ufeff").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        number = line.rstrip(",，").strip()   # 去掉行尾逗號，再去掉逗號前的空白
        try:
            values.append(int(number))
        except ValueError:
            # 例如：abc、3.5、1,2,3（一行放了多個數字）、只有一個逗號
            raise ValueError(f"第 {line_no} 行不是整數：{raw!r}（每一行只能放一個整數，行尾逗號可有可無）")
    return values


class FlowerIntFromLines:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                # 1. 多行文字：每一行一個整數
                "text": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "placeholder": "每一行一個整數，行尾逗號可有可無，例如：\n100,\n200\n300,",
                }),
                # 2. seed：指定輸出哪一行。依專案規範從 0 開始（0 = 第一個整數），
                #    到達總行數就繞回第一行（只有 FlowerLoadTextFromFolder 的 seed 例外，從 1 開始）
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                # 3. 生成後控制，與 seed 綁定：同一行連續處理幾次（依專案規範，有 seed 的節點一律提供）
                "continuous_processing": ("INT", {"default": 1, "min": 1, "max": 9999999}),
            },
        }

    RETURN_TYPES = ("INT",)
    RETURN_NAMES = ("int",)
    FUNCTION = "select_int"
    CATEGORY = "flower-tools"

    def select_int(self, text, seed, continuous_processing=1):
        values = _parse_int_lines(text)
        if not values:
            raise ValueError("輸入的文字中沒有任何整數（每一行放一個整數）。")

        # 專案規範 1-4：seed 除以 continuous_processing 取整，再對總數取餘數。
        # continuous_processing = k 代表 seed 0 ~ k-1 都輸出第 1 行、k ~ 2k-1 輸出第 2 行，依此類推。
        process_idx = seed // max(1, continuous_processing)

        # ui.line_count：把實際讀到的行數回傳給前端的「總行數」欄位（見 web/extension.js 的 setupIntFromLines）。
        # 打字時前端自己就會即時計算；但 text 由其他節點接線傳入時前端看不到內容，要靠這裡在執行後更新。
        return {
            "ui": {"line_count": [len(values)]},
            "result": (values[process_idx % len(values)],),
        }


NODE_CLASS_MAPPINGS = {"FlowerIntFromLines": FlowerIntFromLines}
NODE_DISPLAY_NAME_MAPPINGS = {"FlowerIntFromLines": "🌸Flower Int From Lines"}
