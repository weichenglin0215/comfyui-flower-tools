class FlowerSplitSentences:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "text": ("STRING", {"multiline": True, "default": ""}),
                "split_symbols": ("STRING", {"default": ",.?!，。？！"}),
            }
        }
    
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("split_text",)
    FUNCTION = "split_sentences"
    CATEGORY = "flower-tools"
    OUTPUT_NODE = True

    def split_sentences(self, text, split_symbols):
        if not text:
            return {"ui": {"text": [""]}, "result": ("",)}
            
        result = text
        for sym in split_symbols:
            result = result.replace(sym, sym + '\n')
            
        lines = result.split('\n')
        # Strip each line to remove unnecessary spaces, and filter empty lines
        cleaned_lines = [line.strip() for line in lines]
        cleaned_lines = [line for line in cleaned_lines if line]
        
        result = '\n'.join(cleaned_lines)
            
        return {"ui": {"text": [result]}, "result": (result,)}

NODE_CLASS_MAPPINGS = {
    "FlowerSplitSentences": FlowerSplitSentences
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "FlowerSplitSentences": "🌸Flower Split Sentences"
}
