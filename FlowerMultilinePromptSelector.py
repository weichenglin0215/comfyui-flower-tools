import os
import json
import random
from server import PromptServer
from aiohttp import web

class FlowerMultilinePromptSelector:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                # Index 0: directory
                "directory": ("STRING", {"default": ""}),
                # Index 1 & 2: seed & seed_control
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                # Index 3: file_configs
                "file_configs": ("STRING", {"default": "{}", "multiline": True}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "select_multiline_prompt"
    CATEGORY = "flower-tools"
    OUTPUT_NODE = True 

    def select_multiline_prompt(self, directory, seed, file_configs="{}"):
        base_dir = directory.strip()
        if not base_dir:
            base_dir = os.path.join(os.path.dirname(__file__), "wildcards")
        
        if not os.path.exists(base_dir):
            return {"ui": {"text": ["Error: Dir not found"]}, "result": ("Error",)}

        try: configs = json.loads(file_configs)
        except: configs = {}

        try:
            # 使用標準 ASCII 排序
            files = [f for f in os.listdir(base_dir) if f.endswith(".txt")]
            files.sort() # Python 預設對 string list 做 ASCII 排序
        except Exception as e:
            return {"ui": {"text": [str(e)]}, "result": ("Error",)}

        global_pool = []
        for filename in files:
            file_cfg = configs.get(filename, {"status": "disabled"})
            status = file_cfg.get("status", "disabled")
            if status == "disabled": continue
            
            filepath = os.path.join(base_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    lines = [line.strip() for line in f.readlines() if line.strip()]
                if not lines: continue
                if status == "selected":
                    line = file_cfg.get("selected_line", "")
                    if line: global_pool.append(line)
                elif status == "ordered":
                    global_pool.extend(lines)
                elif status == "random":
                    temp_lines = lines.copy()
                    random.Random(seed).shuffle(temp_lines)
                    global_pool.extend(temp_lines)
            except: continue

        if not global_pool: result = ""
        else: result = global_pool[seed % len(global_pool)]

        return {"ui": {"text": [result]}, "result": (result,)}

# --- API ---
@PromptServer.instance.routes.get("/flower-tools/list-files")
async def list_files(request):
    directory = request.query.get("directory", "").strip()
    if not directory: directory = os.path.join(os.path.dirname(__file__), "wildcards")
    files = []
    try:
        f_list = sorted([f for f in os.listdir(directory) if f.endswith(".txt")])
        for f in f_list:
            path = os.path.join(directory, f)
            try:
                with open(path, "r", encoding="utf-8") as file:
                    count = sum(1 for line in file if line.strip())
                files.append({"name": f, "count": count})
            except: pass
    except: pass
    return web.json_response({"files": files})

@PromptServer.instance.routes.get("/flower-tools/get-file-content")
async def get_file_content(request):
    directory = request.query.get("directory", "").strip()
    filename = request.query.get("filename", "")
    if not directory: directory = os.path.join(os.path.dirname(__file__), "wildcards")
    lines = []
    try:
        with open(os.path.join(directory, filename), "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]
    except: pass
    return web.json_response({"lines": lines})

NODE_CLASS_MAPPINGS = { "FlowerMultilinePromptSelector": FlowerMultilinePromptSelector }
NODE_DISPLAY_NAME_MAPPINGS = { "FlowerMultilinePromptSelector": "Flower Multiline Prompt Selector" }
