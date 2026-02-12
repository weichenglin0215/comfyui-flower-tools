"""
FlowerCSTSConverter - Simplified/Traditional Chinese Text Converter
Uses OpenCC library to convert between Simplified and Traditional Chinese with Taiwan localization.
"""

import sys
import subprocess
import importlib
from server import PromptServer
from aiohttp import web

# Config mappings for OpenCC
CONFIG_MAPPINGS = {
    "Traditional (TW) -> Simplified": "tw2sp",
    "Simplified -> Traditional (TW)": "s2twp"
}

# Try to import opencc, set flag if successful
try:
    import opencc
    HAS_OPENCC = True
except ImportError:
    HAS_OPENCC = False

class FlowerCSTSConverter:
    """
    ComfyUI node for converting between Simplified and Traditional Chinese.
    Supports Taiwan-specific vocabulary using OpenCC library.
    """
    
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "text_input": ("STRING", {"multiline": True, "dynamic": True}),
                "conversion_mode": (
                    list(CONFIG_MAPPINGS.keys()),
                    {"default": "Traditional (TW) -> Simplified"}
                ),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text_output",)
    FUNCTION = "convert_text"
    CATEGORY = "flower-tools"
    OUTPUT_NODE = True

    def convert_text(self, text_input, conversion_mode):
        """
        Convert text between Simplified and Traditional Chinese.
        
        Args:
            text_input: Input text to convert
            conversion_mode: Conversion direction (see CONFIG_MAPPINGS)
            
        Returns:
            Dictionary with UI display and result tuple
        """
        if not HAS_OPENCC:
            error_msg = "Error: opencc not installed. Please use the Install button below. OPENCC套件尚未安裝，請點擊下方的 Install_btn 按鈕進行安裝。"
            return {"ui": {"text": [error_msg]}, "result": ("Error: opencc missing",)}
        
        try:
            config_base = CONFIG_MAPPINGS[conversion_mode]
            converter = self._create_converter(config_base)
            
            result = converter.convert(text_input)
            return {"ui": {"text": [result]}, "result": (result,)}
        except Exception as e:
            error_msg = f"Error: {str(e)}"
            return {"ui": {"text": [error_msg]}, "result": (error_msg,)}

    def _create_converter(self, config_base):
        """
        Create OpenCC converter instance with fallback for different versions.
        
        Args:
            config_base: Base config name (e.g., 'tw2sp', 's2twp')
            
        Returns:
            OpenCC converter instance
            
        Raises:
            Exception: If converter creation fails with all options
        """
        # Try with and without .json extension to support different OpenCC versions
        # opencc-python-reimplemented adds .json automatically
        # standard opencc often requires the .json extension
        options = [config_base, config_base + '.json']
        
        last_error = ""
        for opt in options:
            try:
                converter = opencc.OpenCC(opt)
                # Test conversion to ensure config is valid
                converter.convert("test")
                return converter
            except Exception as e:
                last_error = str(e)
                continue
        
        raise Exception(f"Failed to load OpenCC config '{config_base}'. Last error: {last_error}")


# --- API Endpoints ---

@PromptServer.instance.routes.get("/flower-tools/check-opencc")
async def check_opencc(request):
    """Check if OpenCC library is installed and return its location."""
    try:
        importlib.invalidate_caches()
        spec = importlib.util.find_spec("opencc")
        
        if spec is not None:
            # Get installation location
            location = _get_install_location().strip()
            return web.json_response({
                "installed": True, 
                "location": location if location else "Location unavailable"
            })
        else:
            return web.json_response({"installed": False, "location": ""})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


@PromptServer.instance.routes.post("/flower-tools/install-opencc")
async def install_opencc(request):
    """
    Install OpenCC library with automatic fallback.
    Tries 'opencc' first, falls back to 'opencc-python-reimplemented' if build fails.
    """
    try:
        # 1. Try installing standard 'opencc'
        print("--- Flower Tools: Attempting to install 'opencc' ---")
        result = _install_package("opencc")
        
        final_log = f"--- opencc install log ---\n{result.stdout}\n{result.stderr}\n"
        success = result.returncode == 0

        # 2. If failed, try 'opencc-python-reimplemented' (Common fix for Windows)
        if not success:
            print("--- Flower Tools: 'opencc' failed. Attempting 'opencc-python-reimplemented' ---")
            final_log += "\n'opencc' installation failed. Retrying with 'opencc-python-reimplemented'...\n"
            
            result = _install_package("opencc-python-reimplemented")
            final_log += f"--- opencc-python-reimplemented install log ---\n{result.stdout}\n{result.stderr}\n"
            success = result.returncode == 0

        if success:
            print("--- Flower Tools: Installation Successful ---")
            final_log += _get_install_location()
            return web.json_response({"success": True, "log": final_log})
        else:
            print("--- Flower Tools: Installation FAILED ---")
            return web.json_response({"success": False, "log": final_log})
            
    except Exception as e:
        print(f"--- Flower Tools: Critical Error ---\n{str(e)}")
        return web.json_response({"success": False, "log": str(e)}, status=500)


def _install_package(package_name):
    """
    Install a Python package using pip.
    
    Args:
        package_name: Name of the package to install
        
    Returns:
        subprocess.CompletedProcess with stdout/stderr
    """
    print(f"Installing {package_name}...")
    cmd = [sys.executable, "-m", "pip", "install", package_name]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    # Print to console for user visibility
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    
    return result


def _get_install_location():
    """
    Get the installation location of site-packages.
    
    Returns:
        String with location info, or empty string if unavailable
    """
    try:
        import site
        loc = site.getsitepackages()[0]
        msg = f"\nInstalled to: {loc}"
        print(msg)
        return msg
    except:
        return ""


NODE_CLASS_MAPPINGS = {"FlowerCSTSConverter": FlowerCSTSConverter}
NODE_DISPLAY_NAME_MAPPINGS = {"FlowerCSTSConverter": "🌸Flower CSTS Converter"}
