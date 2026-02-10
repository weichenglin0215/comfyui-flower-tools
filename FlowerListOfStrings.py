class FlowerListOfStrings:
    @classmethod
    def INPUT_TYPES(s):
        inputs = {
            "optional": {},
            "required": {
                "delimiter": ("STRING", {"default": ""}),
                "add_newline": ("BOOLEAN", {"default": True})
            }
        }
        
        for i in range(1, 11):
            inputs["optional"][f"string_{i}"] = ("STRING", {"multiline": True, "default": ""})
            
        return inputs

    RETURN_TYPES = ("STRING", "LIST")
    RETURN_NAMES = ("combined_string", "string_list")
    FUNCTION = "process"
    CATEGORY = "flower-tools"

    def process(self, delimiter, add_newline, **kwargs):
        # Process the delimiter to handle escaped newlines
        actual_delimiter = delimiter.replace("\\n", "\n")
        
        string_list = []
        combined_parts = []
        
        for i in range(1, 11):
            val = kwargs.get(f"string_{i}", "")
            # Even if empty, we might want to include it if the user intended 10 fixed slots
            # But usually, it's better to only process non-empty ones or all of them?
            # The prompt says "固定十個STRING輸入", implying they exist.
            
            string_list.append(val)
            
            # Combine logic
            part = val + actual_delimiter
            if add_newline:
                part += "\n"
            combined_parts.append(part)
            
        combined_string = "".join(combined_parts)
        
        return (combined_string, string_list)

NODE_CLASS_MAPPINGS = {
    "FlowerListOfStrings": FlowerListOfStrings
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "FlowerListOfStrings": "🌸Flower List of Strings"
}
