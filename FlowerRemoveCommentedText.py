class FlowerRemoveCommentedText:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "text": ("STRING", {"multiline": True, "default": ""}),
                "starting_line_comment": ("STRING", {"default": "//"}),
                "enclosed_comment_start": ("STRING", {"default": "/*"}),
                "enclosed_comment_end": ("STRING", {"default": "*/"}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "process"
    CATEGORY = "flower-tools"

    def process(self, text, starting_line_comment, enclosed_comment_start, enclosed_comment_end):
        has_block = bool(enclosed_comment_start) and bool(enclosed_comment_end)
        has_line = bool(starting_line_comment)

        result = []
        i = 0
        in_block = False

        while i < len(text):
            if not in_block:
                idx_block = text.find(enclosed_comment_start, i) if has_block else -1
                idx_line = text.find(starting_line_comment, i) if has_line else -1

                if idx_block == -1 and idx_line == -1:
                    result.append(text[i:])
                    break

                if idx_block != -1 and (idx_line == -1 or idx_block < idx_line):
                    result.append(text[i:idx_block])
                    in_block = True
                    i = idx_block + len(enclosed_comment_start)
                else:
                    result.append(text[i:idx_line])
                    next_nl = text.find('\n', idx_line)
                    if next_nl == -1:
                        break
                    else:
                        i = next_nl
            else:
                idx_end = text.find(enclosed_comment_end, i)
                if idx_end == -1:
                    break
                else:
                    in_block = False
                    i = idx_end + len(enclosed_comment_end)

        return ("".join(result),)

NODE_CLASS_MAPPINGS = {
    "FlowerRemoveCommentedText": FlowerRemoveCommentedText
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "FlowerRemoveCommentedText": "🌸Flower Remove Commented Text"
}
