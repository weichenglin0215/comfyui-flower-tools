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
                "continuous_processing": ("INT", {"default": 1, "min": 1, "max": 9999999}),
                # Index 3: output_mode
                "output_mode": ("BOOLEAN", {"default": False, "label_on": "循序模式(Sequential)", "label_off": "組合模式(Combination)"}),
                # Index 4: file_configs
                "file_configs": ("STRING", {"default": "{}", "multiline": True}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "select_multiline_prompt"
    CATEGORY = "flower-tools"
    OUTPUT_NODE = True 

    def select_multiline_prompt(self, directory, seed, continuous_processing, output_mode, file_configs="{}"):
        base_dir = directory.strip()
        default_dir = os.path.join(os.path.dirname(__file__), "wildcards")
        
        if not base_dir:
            base_dir = default_dir
        elif os.path.isabs(base_dir) or ":" in base_dir:
            pass # Use as absolute path
        else:
            base_dir = os.path.abspath(os.path.join(default_dir, base_dir))
        
        if not os.path.exists(base_dir):
            return {"ui": {"text": ["Error: Dir not found"]}, "result": ("Error",)}

        try: configs = json.loads(file_configs)
        except: configs = {}

        try:
            # 🌸 核心修正: 完全使用 Frontend JSON 的排列順序 🌸
            ordered_files = []
            for fn in configs.keys():
                if fn.endswith(".txt") and os.path.exists(os.path.join(base_dir, fn)):
                    ordered_files.append(fn)

            # 將可能尚未存儲於 configs 的剩餘檔案加在後面
            import re
            def fallback_sort_key(f):
                is_ascii = ord(f[0]) < 128 if f else True
                parts = re.split(r'(\d+)', f)
                parts = [int(p) if p.isdigit() else p for p in parts]
                return (0 if is_ascii else 1, parts)
                
            remaining = [f for f in os.listdir(base_dir) if f.endswith(".txt") and f not in configs]
            remaining.sort(key=fallback_sort_key)
            
            files = ordered_files + remaining
        except Exception as e:
            return {"ui": {"text": [str(e)]}, "result": ("Error",)}

        # 🌸 核心運算邏輯: seed 除以 continuous_processing 🌸
        process_idx = seed // max(1, continuous_processing)

        if output_mode: # 循序模式
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
                        random.Random(process_idx).shuffle(temp_lines)
                        global_pool.extend(temp_lines)
                except: continue

            if not global_pool: result = ""
            else: result = global_pool[process_idx % len(global_pool)]
        else: # 組合模式
            combined_parts = []
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
                        if line: combined_parts.append(line)
                    elif status == "ordered":
                        combined_parts.append(lines[process_idx % len(lines)])
                    elif status == "random":
                        temp_lines = lines.copy()
                        random.Random(process_idx).shuffle(temp_lines)
                        combined_parts.append(temp_lines[process_idx % len(temp_lines)])
                except: continue
            
            result = ", ".join(combined_parts)

        return {"ui": {"text": [result]}, "result": (result,)}

# --- API ---
@PromptServer.instance.routes.get("/flower-tools/list-files")
async def list_files(request):
    directory = request.query.get("directory", "").strip()
    default_dir = os.path.join(os.path.dirname(__file__), "wildcards")
    if not directory: 
        directory = default_dir
    elif os.path.isabs(directory) or ":" in directory:
        pass
    else:
        directory = os.path.abspath(os.path.join(default_dir, directory))
    
    if not os.path.isdir(directory):
        return web.json_response({"error": "Directory not found", "path": directory}, status=404)

    import re
    def sort_key(f):
        is_ascii = ord(f[0]) < 128 if f else True
        parts = re.split(r'(\d+)', f)
        parts = [int(p) if p.isdigit() else p for p in parts]
        return (0 if is_ascii else 1, parts)

    files = []
    try:
        f_list = [f for f in os.listdir(directory) if f.endswith(".txt")]
        f_list.sort(key=sort_key)
        for f in f_list:
            path = os.path.join(directory, f)
            try:
                with open(path, "r", encoding="utf-8") as file:
                    count = sum(1 for line in file if line.strip())
                files.append({"name": f, "count": count})
            except: pass
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
    
    return web.json_response({"files": files})

@PromptServer.instance.routes.get("/flower-tools/get-file-content")
async def get_file_content(request):
    directory = request.query.get("directory", "").strip()
    filename = request.query.get("filename", "")
    default_dir = os.path.join(os.path.dirname(__file__), "wildcards")
    if not directory: 
        directory = default_dir
    elif os.path.isabs(directory) or ":" in directory:
        pass
    else:
        directory = os.path.abspath(os.path.join(default_dir, directory))
    
    path = os.path.join(directory, filename)
    if not os.path.isfile(path):
        return web.json_response({"error": "File not found", "path": path}, status=404)

    lines = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
    
    return web.json_response({"lines": lines})

NODE_CLASS_MAPPINGS = { "FlowerMultilinePromptSelector": FlowerMultilinePromptSelector }
NODE_DISPLAY_NAME_MAPPINGS = { "FlowerMultilinePromptSelector": "🌸Flower Multiline Prompt Selector" }
