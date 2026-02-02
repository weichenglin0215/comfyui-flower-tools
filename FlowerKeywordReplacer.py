class FlowerKeywordReplacer:
    @classmethod
    def INPUT_TYPES(s):
        inputs = {
            "required": {
                "text": ("STRING", {"multiline": True, "default": ""}),
            },
            "optional": {}
        }
        
        for i in range(1, 11):
            inputs["optional"][f"keyword_{i}"] = ("STRING", {"default": ""})
            inputs["optional"][f"replacement_{i}"] = ("STRING", {"multiline": True, "default": ""})
            
        return inputs

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "replace_keywords"
    CATEGORY = "flower-tools"

    def replace_keywords(self, text, **kwargs):
        result = text
        for i in range(1, 11):
            keyword = kwargs.get(f"keyword_{i}", "")
            replacement = kwargs.get(f"replacement_{i}", "")
            
            if keyword:
                result = result.replace(keyword, replacement)
        
        return (result,)

NODE_CLASS_MAPPINGS = {
    "FlowerKeywordReplacer": FlowerKeywordReplacer
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "FlowerKeywordReplacer": "Flower Keyword Replacer"
}
