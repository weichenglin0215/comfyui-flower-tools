// 🌸 Flower Multiline Prompt Selector V30: 終極穩定、間距優化與手動 JSON 同步版
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
// 確保在腳本頂部能存取到 ComfyWidgets (通常在擴充功能的 beforeRegisterNodeDef 裡)
import { ComfyWidgets } from "../../scripts/widgets.js";

console.log("🌸🌸🌸 Flower Multiline Prompt Selector: The Final Solution V31 🌸🌸🌸");

(function () {
    const TARGET_KEY = "FlowerMultilinePromptSelector";

    const setupNode = (nodeType, nodeName) => {
        if (nodeType.__flower_setup_done) return;
        nodeType.__flower_setup_done = true;

        // --- 核心排序函數 (優先英文，後中文) ---
        const asciiSort = (a, b) => {
            const na = (typeof a === 'string' ? a : a.name) || "";
            const nb = (typeof b === 'string' ? b : b.name) || "";

            // 檢查是否以 ASCII 字元 (如英文、數字) 開頭
            const aFirst = na.charCodeAt(0) || 0;
            const bFirst = nb.charCodeAt(0) || 0;

            const isAAscii = aFirst < 128;
            const isBAscii = bFirst < 128;

            if (isAAscii && !isBAscii) return -1;
            if (!isAAscii && isBAscii) return 1;

            return na.localeCompare(nb, undefined, { numeric: true, sensitivity: 'base' });
        };

        const rebuildFileButtons = function (node, filesFromApi) {
            if (!node.widgets) return;

            // 1. 固定保留前面的基底元件，清除其他產生的檔案按鈕
            for (let i = node.widgets.length - 1; i >= 0; i--) {
                const w = node.widgets[i];
                if (!w.is_base_widget) {
                    if (w.inputEl) w.inputEl.remove();
                    node.widgets.splice(i, 1);
                }
            }

            // 2. 確定數據來源 (API 優先，但保持 ASCII)
            let displayList = [];
            if (filesFromApi) {
                displayList = filesFromApi.sort(asciiSort);
            } else {
                displayList = Object.keys(node.fileConfigs || {}).sort(asciiSort).map(n => ({
                    name: n,
                    count: node.fileConfigs[n].count || "?"
                }));
            }

            // 🌸 關鍵修復：這裡我們將這由 asciiSort 排好序的清單「依序」存回 node.fileConfigs 🌸
            // Python 後端只要照著 configs.keys() 的順序解析，就絕對會和 Frontend 完全相同的順序執行。
            if (Object.keys(node.fileConfigs || {}).length > 0) {
                const orderedConfigs = {};
                for (const file of displayList) {
                    if (node.fileConfigs[file.name]) {
                        orderedConfigs[file.name] = node.fileConfigs[file.name];
                    } else {
                        orderedConfigs[file.name] = { status: "disabled", count: file.count || "?" };
                    }
                }
                node.fileConfigs = orderedConfigs;

                const cfw = node.widgets.find(w => w.name === "file_configs");
                if (cfw) cfw.value = JSON.stringify(node.fileConfigs, null, 2);
            }

            // 3. 依序重新加入這堆按鈕，確保維持間距
            for (const file of displayList) {
                const widget = node.addWidget("button", file.name, null, () => {
                    node.showSelectionPopup(file.name);
                });
                widget.type = "button";
                widget.serialize = false;
                widget.is_base_widget = false;
                widget.last_count = file.count || "?";

                // 設定高度 40 (35橫條 + 5間隔)
                widget.computeSize = (w) => [220, 40];

                widget.draw = function (ctx, node, width, y, height) {
                    const config = node.fileConfigs?.[this.name] || { status: "disabled" };
                    const status = config.status;

                    // 繪製 35px 高的橫條，保留 5px 間隙
                    ctx.fillStyle = "#121212";
                    ctx.beginPath();
                    ctx.roundRect(20, y, width - 40, 35, 8);
                    ctx.fill();
                    ctx.strokeStyle = "#444";
                    ctx.lineWidth = 1.5;
                    ctx.stroke();

                    let sc, st;
                    switch (status) {
                        case "random": sc = "#3498db"; st = "RANDOM"; break;
                        case "ordered": sc = "#9b59b6"; st = "ORDERED"; break;
                        case "selected": sc = "#27ae60"; st = "PICKED"; break;
                        default: sc = "#444"; st = "OFF"; break;
                    }

                    ctx.fillStyle = sc; ctx.beginPath(); ctx.roundRect(25, y + 3, 80, 29, 4); ctx.fill();
                    ctx.fillStyle = "#fff"; ctx.font = "bold 16px Arial"; ctx.textAlign = "center";
                    ctx.fillText(st, 65, y + 23);

                    ctx.textAlign = "left"; ctx.font = "20px Arial";
                    ctx.fillText(this.name, 120, y + 23);

                    ctx.textAlign = "right";
                    ctx.fillText(`[${this.last_count}]`, width - 35, y + 23);

                    if (status === "selected" && config.selected_line) {
                        ctx.fillStyle = "#fff"; ctx.font = "italic 18px Arial";
                        let pr = config.selected_line;
                        if (ctx.measureText(pr).width > width - 450) pr = pr.substring(0, 30) + "...";
                        ctx.fillText(pr, width - 110, y + 23);
                    }
                };
            }
            node.setDirtyCanvas(true);
        };

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            onNodeCreated?.apply(this, arguments);
            this.size = [800, 550]; // 預設給大一點，確保存活
            this.fileConfigs = {};

            // 標記來自 Python 的原生 Widgets
            if (this.widgets) {
                for (const w of this.widgets) {
                    w.is_base_widget = true;
                }
            }

            // --- 🌸 建立核心地基 (索引順序守護) 🌸 ---
            // 4. Result (result_dialog) - 改用 ComfyUI 標準元件寫法。
            if (!this.widgets.find(w => w.name === "result_dialog")) {
                // 使用 ComfyUI 官方的多行文本元件建立方式
                const res = ComfyWidgets["STRING"](this, "result_dialog", ["STRING", { multiline: true }], app).widget;

                res.label = "Final Selection";
                res.value = "";
                res.serialize = false;

                // 設定高度
                res.computeSize = (w) => [220, 150];
                res.is_base_widget = true;

                this.resultWidget = res;
            }

            // 5. Refresh (內部同步版)
            if (!this.widgets.find(w => w.name === "refresh_btn")) {
                const btn = this.addWidget("button", "Refresh Files (點擊同步目錄並更新設定)", null, async () => {
                    // 🌸 第一步：從介面的文字框回讀 JSON 🌸
                    const cfw = this.widgets.find(w => w.name === "file_configs");
                    if (cfw && cfw.value) {
                        try { this.fileConfigs = JSON.parse(cfw.value); } catch (e) { console.error("JSON Read Error"); }
                    }

                    const dir = (this.widgets.find(w => w.name === "directory")?.value || "").trim();
                    try {
                        const response = await api.fetchApi(`/flower-tools/list-files?directory=${encodeURIComponent(dir)}`);
                        if (!response.ok) {
                            console.warn("Directory not found or API error, keeping current configs.");
                            const errorMsg = `Directory [ ${dir} ] 並不存在，請檢查並重新輸入。`;
                            console.warn(errorMsg);
                            window.alert(errorMsg);
                            return;
                        }
                        const data = await response.json();
                        if (data && data.files && data.files.length > 0) {
                            // 🌸 重新構建 fileConfigs，只保留當前目錄有的檔案，避免不同目錄檔案混雜 (Fix Task 1) 🌸
                            const newConfigs = {};
                            data.files.forEach(f => {
                                if (this.fileConfigs[f.name]) {
                                    newConfigs[f.name] = this.fileConfigs[f.name];
                                    newConfigs[f.name].count = f.count;
                                } else {
                                    newConfigs[f.name] = { status: "disabled", count: f.count };
                                }
                            });
                            this.fileConfigs = newConfigs;

                            // 反饋回 JSON 框，使用多行格式 (Fix Task 2)
                            if (cfw) cfw.value = JSON.stringify(this.fileConfigs, null, 2);
                            rebuildFileButtons(this, data.files);
                        } else if (data && data.files && data.files.length === 0) {
                            // 如果目錄存在但真的是空的，可以選擇清空或保持。這裡選擇清空，因為 API 成功回傳了空陣列。
                            this.fileConfigs = {};
                            if (cfw) cfw.value = "{}";
                            rebuildFileButtons(this, []);
                        }
                    } catch (e) {
                        console.error("Refresh failed:", e);
                        // 發生錯誤（如網路問題或 Python 端報錯）時，保持原樣不變
                    }
                });
                btn.name = "refresh_btn";
                btn.serialize = false;
                btn.is_base_widget = true;
            }

            // 我們不再使用 reorder，因為重排序會打亂 ComfyUI Widget 陣列索引，導致讀檔時數值對錯欄位！
            return undefined;
        };

        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (config) {
            onConfigure?.apply(this, arguments);
            const fw = this.widgets.find(w => w.name === "file_configs");
            if (fw && fw.value) {
                try {
                    this.fileConfigs = JSON.parse(fw.value);
                    rebuildFileButtons(this, null);
                } catch (e) { }
            }
        };

        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            onExecuted?.apply(this, arguments);
            if (message.text) {
                // 使用全新的 result_dialog 名稱進行同步
                const res = this.widgets.find(w => w.name === "result_dialog");
                if (res) {
                    res.value = message.text[0];
                    if (res.inputEl) res.inputEl.value = res.value;
                    this.setDirtyCanvas(true);
                }
            }
        };

        nodeType.prototype.showSelectionPopup = async function (fileName) {
            const dir = (this.widgets.find(w => w.name === "directory")?.value || "").trim();
            const resp = await api.fetchApi(`/flower-tools/get-file-content?directory=${encodeURIComponent(dir)}&filename=${encodeURIComponent(fileName)}`);
            const data = await resp.json();
            const lines = data.lines || [];

            const overlay = document.createElement('div');
            Object.assign(overlay.style, { position: 'fixed', top: '0', left: '0', width: '100%', height: '100%', backgroundColor: 'rgba(0,0,0,0.85)', zIndex: '10000', display: 'flex', justifyContent: 'center', alignItems: 'center', backdropFilter: 'blur(6px)' });
            const dialog = document.createElement('div');
            Object.assign(dialog.style, { width: '1200px', height: '85%', backgroundColor: '#181818', color: '#eee', borderRadius: '20px', display: 'flex', flexDirection: 'column', boxShadow: '0 30px 60px rgba(0,0,0,0.8)', border: '2px solid #333', overflow: 'hidden' });

            const header = document.createElement('div');
            header.innerHTML = `<div style="padding:25px; border-bottom:2px solid #333; background:#222; font-size:24px; font-weight:bold; color:#fff;">🌸 ${fileName}</div>`;
            dialog.appendChild(header);

            const updateCfg = (cfg) => {
                this.fileConfigs[fileName] = { ...this.fileConfigs[fileName], ...cfg };
                const cfw = this.widgets.find(w => w.name === "file_configs");
                if (cfw) { cfw.value = JSON.stringify(this.fileConfigs, null, 2); this.triggerSlotElementChange?.(); }
                this.setDirtyCanvas(true);
            };

            const modes = document.createElement('div');
            Object.assign(modes.style, { padding: '20px', display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '15px' });
            const btnTpl = (l, bg, s) => {
                const b = document.createElement('button'); b.textContent = l;
                Object.assign(b.style, { padding: '15px', fontSize: '18px', fontWeight: 'bold', border: 'none', borderRadius: '10px', backgroundColor: bg, color: '#fff', cursor: 'pointer' });
                b.onclick = () => { updateCfg({ status: s }); document.body.removeChild(overlay); }; return b;
            };
            modes.appendChild(btnTpl("❌ Disable", "#444", "disabled"));
            modes.appendChild(btnTpl("🎲 Random", "#007acc", "random"));
            modes.appendChild(btnTpl("🔢 Ordered", "#8e44ad", "ordered"));
            dialog.appendChild(modes);

            const searchInput = document.createElement('input');
            Object.assign(searchInput.style, { width: 'calc(100% - 40px)', margin: '0 20px 20px 20px', padding: '15px', backgroundColor: '#000', color: '#fff', border: '2px solid #444', borderRadius: '10px', fontSize: '18px' });
            dialog.appendChild(searchInput);

            const listDiv = document.createElement('div');
            Object.assign(listDiv.style, { flex: '1', overflowY: 'auto', padding: '0 10px 20px 10px' });
            dialog.appendChild(listDiv);

            const render = (f) => {
                listDiv.innerHTML = "";
                lines.filter(l => l.toLowerCase().includes(f.toLowerCase())).forEach(line => {
                    const item = document.createElement('div');
                    item.textContent = line; Object.assign(item.style, { padding: '15px', cursor: 'pointer', borderRadius: '8px', fontSize: '18px' });
                    item.onmouseover = () => item.style.backgroundColor = "#333"; item.onmouseout = () => item.style.backgroundColor = "transparent";
                    item.onclick = () => { updateCfg({ status: "selected", selected_line: line }); document.body.removeChild(overlay); };
                    listDiv.appendChild(item);
                });
            };
            searchInput.oninput = (e) => render(e.target.value);
            render(""); overlay.appendChild(dialog);
            overlay.onclick = (e) => { if (e.target === overlay) document.body.removeChild(overlay); };
            document.body.appendChild(overlay); searchInput.focus();
        };
    };

    const setupStringComparison = (nodeType, nodeName) => {
        if (nodeType.__flower_comparison_setup_done) return;
        nodeType.__flower_comparison_setup_done = true;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            if (onNodeCreated) onNodeCreated.apply(this, arguments);

            const textA = this.widgets.find(w => w.name === "字串A");
            const textB = this.widgets.find(w => w.name === "字串B");

            // Define minimum heights (approx 2 lines = 50px)
            const minWHeight = 60;

            this.computeSize = function () {
                let h = 30; // Title
                for (const w of this.widgets) {
                    if (w === textA || w === textB) {
                        h += (w._last_h || minWHeight) + 4;
                    } else {
                        h += (w.computeSize ? w.computeSize(this.size[0])[1] : 24) + 4;
                    }
                }
                return [220, Math.max(h, 250)];
            };

            const adjustHeights = () => {
                let fixedHeight = 40; // Title + margins
                for (const w of this.widgets) {
                    if (w !== textA && w !== textB) {
                        fixedHeight += (w.computeSize ? w.computeSize(this.size[0])[1] : 24) + 4;
                    }
                }

                const remaining = Math.max(minWHeight * 2, this.size[1] - fixedHeight - 10);
                const half = remaining / 2;

                if (textA) {
                    textA.computeSize = (w) => [220, half];
                    textA._last_h = half;
                }
                if (textB) {
                    textB.computeSize = (w) => [220, half];
                    textB._last_h = half;
                }
            };

            const oldOnResize = this.onResize;
            this.onResize = function (size) {
                adjustHeights();
                return oldOnResize ? oldOnResize.apply(this, arguments) : undefined;
            };

            this.size = [400, 350];
            setTimeout(adjustHeights, 100);
        };
    };

    const setupKeywordReplacer = (nodeType, nodeName) => {
        if (nodeType.__flower_replacer_setup_done) return;
        nodeType.__flower_replacer_setup_done = true;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            if (onNodeCreated) onNodeCreated.apply(this, arguments);

            // 尋找主輸入文字框 "text"
            const mainText = this.widgets.find(w => w.name === "text");
            if (mainText) {
                // ComfyUI 預設多行文本高度通常是 80 左右
                // 我們將其設為 120 (約 6 行高度) 以符合用戶「目前的1.5倍高度」與「至少 3 行」
                mainText.computeSize = (w) => [220, 120];
            }

            // 調整節點寬度以容納多組輸入
            this.size[0] = 440;
        };
    };

    const setupTCSCConverter = (nodeType, nodeName) => {
        if (nodeType.__flower_tcsc_setup_done) return;
        nodeType.__flower_tcsc_setup_done = true;

        // Message constants
        const MESSAGES = {
            INSTALLED: "✅ OpenCC is already installed! (OpenCC 已安裝)\n\nLocation info is printed in the server console.",
            CONFIRM_INSTALL: "⚠️ OpenCC not found. Install now? (這可能需要一點時間)\n\n未檢測到 OpenCC。是否立即安裝？\n\n檢查後台黑色視窗(Console)可看到即時進度。\nCheck your ComfyUI console for live progress.",
            INSTALLING: "⏳ Installing... Please wait.\n\nCheck the console window for live progress.\n正在安裝... 請稍候。請查看後台黑色視窗以獲取即時進度。",
            SUCCESS: "✅ Installation Successful! Please RESTART ComfyUI.\n\n安裝成功！請重新啟動 ComfyUI 以生效。\n\n",
            SUCCESS_ALERT: "✅ Installation Successful! Please RESTART ComfyUI.",
            FAILED: "❌ Installation Failed.\n\n安裝失敗。\n\n",
            FAILED_ALERT: "❌ Installation Failed. See details in the result box."
        };

        // Helper function to update result widget with text
        const updateResultWidget = (widget, text) => {
            if (!widget) return;
            widget.value = text;
            if (widget.inputEl) {
                widget.inputEl.value = text;
            }
        };

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            if (onNodeCreated) onNodeCreated.apply(this, arguments);

            // 1. Create read-only result widget
            if (ComfyWidgets && ComfyWidgets["STRING"]) {
                if (!this.widgets.find(w => w.name === "result_dialog")) {
                    const res = ComfyWidgets["STRING"](this, "result_dialog", ["STRING", { multiline: true }], app).widget;
                    res.label = "Conversion Result";
                    res.value = "";
                    res.serialize = false;

                    // Set to read-only style
                    if (res.inputEl) {
                        res.inputEl.readOnly = true;
                        res.inputEl.style.opacity = "0.7";
                    }

                    res.computeSize = (w) => [220, 100];
                    this.resultWidget = res;
                }
            }

            // 2. Create auto-install button
            if (!this.widgets.find(w => w.name === "install_btn")) {
                const btn = this.addWidget("button", "自動偵測並安裝 OPENCC", null, async () => {
                    try {
                        // Check if OpenCC is already installed
                        const checkResp = await api.fetchApi("/flower-tools/check-opencc");
                        const checkData = await checkResp.json();
                        const resWidget = this.widgets.find(w => w.name === "result_dialog");

                        if (checkData.installed) {
                            const locationMsg = checkData.location ? `\n\n安裝位置 / Installed at:\n${checkData.location}` : "";
                            const msg = `✅ OpenCC is already installed! (OpenCC 已安裝)${locationMsg}`;
                            window.alert(msg);
                            updateResultWidget(resWidget, msg);
                            return;
                        }

                        // Confirm installation
                        if (!window.confirm(MESSAGES.CONFIRM_INSTALL)) return;

                        // Show installing message
                        updateResultWidget(resWidget, MESSAGES.INSTALLING);

                        // Execute installation
                        const installResp = await api.fetchApi("/flower-tools/install-opencc", { method: "POST" });
                        const installData = await installResp.json();

                        // Handle result
                        if (installData.success) {
                            window.alert(MESSAGES.SUCCESS_ALERT);
                            updateResultWidget(resWidget, MESSAGES.SUCCESS + installData.log);
                        } else {
                            window.alert(MESSAGES.FAILED_ALERT);
                            updateResultWidget(resWidget, MESSAGES.FAILED + installData.log);
                        }
                        this.setDirtyCanvas(true);

                    } catch (e) {
                        window.alert("Error checking/installing OpenCC: " + e);
                    }
                });
                btn.name = "install_btn";
                btn.serialize = false;
            }

            // Set default node size
            this.size[0] = 400;
        };

        // Handle execution results
        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            if (onExecuted) onExecuted.apply(this, arguments);
            if (message.text) {
                const res = this.widgets.find(w => w.name === "result_dialog");
                if (res) {
                    res.value = message.text[0];
                    if (res.inputEl) res.inputEl.value = res.value;
                    this.setDirtyCanvas(true);
                }
            }
        };
    };
    const setupLoadTextFromFolder = (nodeType, nodeName) => {
        if (nodeType.__flower_ltff_setup_done) return;
        nodeType.__flower_ltff_setup_done = true;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            if (onNodeCreated) onNodeCreated.apply(this, arguments);

            if (!ComfyWidgets || !ComfyWidgets["STRING"]) return;

            // 6. 檔案內容預覽欄（唯讀）
            if (!this.widgets.find(w => w.name === "content_preview")) {
                const cp = ComfyWidgets["STRING"](this, "content_preview", ["STRING", { multiline: true }], app).widget;
                cp.label = "檔案內容 (Content Preview)";
                cp.value = "";
                cp.serialize = false;
                if (cp.inputEl) {
                    cp.inputEl.readOnly = true;
                    cp.inputEl.style.opacity = "0.7";
                }
                cp.computeSize = (w) => [220, 150];
                this.contentPreviewWidget = cp;
            }

            // 7. 檔案清單顯示欄（唯讀，顯示 0- 開頭的編號清單）
            if (!this.widgets.find(w => w.name === "file_list_display")) {
                const fl = ComfyWidgets["STRING"](this, "file_list_display", ["STRING", { multiline: true }], app).widget;
                fl.label = "檔案清單 (File List)";
                fl.value = "";
                fl.serialize = false;
                if (fl.inputEl) {
                    fl.inputEl.readOnly = true;
                    fl.inputEl.style.opacity = "0.7";
                }
                fl.computeSize = (w) => [220, 120];
                this.fileListWidget = fl;
            }

            this.size = [500, 450];
        };

        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            if (onExecuted) onExecuted.apply(this, arguments);

            if (message.text) {
                const cp = this.widgets.find(w => w.name === "content_preview");
                if (cp) {
                    cp.value = message.text[0];
                    if (cp.inputEl) cp.inputEl.value = cp.value;
                }
            }
            if (message.file_list) {
                const fl = this.widgets.find(w => w.name === "file_list_display");
                if (fl) {
                    fl.value = message.file_list[0];
                    if (fl.inputEl) fl.inputEl.value = fl.value;
                }
            }
            this.setDirtyCanvas(true);
        };
    };

    const setupSplitSentences = (nodeType, nodeName) => {
        if (nodeType.__flower_split_setup_done) return;
        nodeType.__flower_split_setup_done = true;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            if (onNodeCreated) onNodeCreated.apply(this, arguments);

            const mainText = this.widgets.find(w => w.name === "text");

            if (ComfyWidgets && ComfyWidgets["STRING"]) {
                if (!this.widgets.find(w => w.name === "result_dialog")) {
                    const res = ComfyWidgets["STRING"](this, "result_dialog", ["STRING", { multiline: true }], app).widget;
                    res.label = "Output Preview (輸出預覽)";
                    res.value = "";
                    res.serialize = false;

                    if (res.inputEl) {
                        res.inputEl.readOnly = true;
                        res.inputEl.style.opacity = "0.7";
                    }

                    res.computeSize = (w) => [220, 60];
                    this.resultWidget = res;
                }
            }

            const adjustHeights = () => {
                let fixedHeight = 40; 
                for (const w of this.widgets) {
                    if (w !== mainText) {
                        fixedHeight += (w.computeSize ? w.computeSize(this.size[0])[1] : 24) + 4;
                    }
                }

                const remaining = Math.max(60, this.size[1] - fixedHeight - 10);
                if (mainText) {
                    mainText.computeSize = (w) => [220, remaining];
                    mainText._last_h = remaining;
                }
            };

            const oldOnResize = this.onResize;
            this.onResize = function (size) {
                adjustHeights();
                return oldOnResize ? oldOnResize.apply(this, arguments) : undefined;
            };

            this.size = [400, 250];
            setTimeout(adjustHeights, 100);
        };

        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            if (onExecuted) onExecuted.apply(this, arguments);
            if (message.text) {
                const res = this.widgets.find(w => w.name === "result_dialog");
                if (res) {
                    res.value = message.text[0];
                    if (res.inputEl) res.inputEl.value = res.value;
                    this.setDirtyCanvas(true);
                }
            }
        };
    };

    app.registerExtension({
        name: "Flower.MultilinePromptSelector.V31",
        async beforeRegisterNodeDef(nodeType, nodeData, app) {
            if (nodeData.name === TARGET_KEY) {
                setupNode(nodeType, nodeData.name);
            } else if (nodeData.name === "FlowerKeywordReplacer") {
                setupKeywordReplacer(nodeType, nodeData.name);
            } else if (nodeData.name === "FlowerStringComparison") {
                setupStringComparison(nodeType, nodeData.name);
            } else if (nodeData.name === "FlowerTCSCConverter") {
                setupTCSCConverter(nodeType, nodeData.name);
            } else if (nodeData.name === "FlowerSplitSentences") {
                setupSplitSentences(nodeType, nodeData.name);
            } else if (nodeData.name === "FlowerLoadTextFromFolder") {
                setupLoadTextFromFolder(nodeType, nodeData.name);
            } else if (nodeData.name === "FlowerFileNameCombination") {
                // 修改原型以增加幫助按鈕與預設寬度
                const onNodeCreated = nodeType.prototype.onNodeCreated;
                nodeType.prototype.onNodeCreated = function () {
                    const r = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;
                    this.size[0] = 600;

                    if (!this.widgets.find(w => w.name === "help_btn")) {
                        const hb = this.addWidget("button", "Help / 使用說明 (?)", null, () => {
                            window.alert(`🌸 [Flower FileNameCombination] 使用說明 🌸

本節點用於組合複雜的存檔檔名，支援動態日期與路徑。

1. 變數預覽：
   可以使用以下標籤於 Format 欄位中：
   - %MainFolderName : 第一欄輸入的主目錄
   - %SubFolderName  : 第二欄輸入的次目錄
   - %FileName       : 檔名 (如果勾選 same_as_subfolder 則等同於 SubFolderName)
   - %Suffix         : 後綴字
   - %DATE           : 格式化日期 (依 DATE format)
   - %TIME           : 格式化時間 (依 TIME format)
   - %DATETIME       : 格式化日期時間 (依 DATETIME format)

2. 格式範例：
   - FullNameOut: %MainFolderName/%DATE-%SubFolderName/%FileName-%Suffix
   - 輸出的 / 會自動被轉換成作業系統對應的路徑分隔符。

3. 自動同步：
   - 勾選 "same_as_subfolder" 會自動聯動 SubFolderName 與 FileName，
     簡化「目錄名即檔名」的常見需求。

⚠️ 注意：系統會自動過濾非法字元 (* : ? " < > | 等)。`);
                        });
                        hb.name = "help_btn";
                        hb.serialize = false;
                    }
                    return r;
                };
            }
        },
        nodeCreated(node, app) {
            if (node.comfyClass === "FlowerFileNameCombination") {
                const illegalCharsAll = /[\\/:*?"<>|]/g;
                const illegalCharsPath = /[:*?"<>|]/g;

                const setupWidget = (name, regex) => {
                    const w = node.widgets.find(x => x.name === name);
                    if (!w) return null;

                    // 覆蓋 callback 進行過濾
                    const oldCallback = w.callback;
                    w.callback = function (v) {
                        const cleaned = typeof v === "string" ? v.replace(regex, "") : v;
                        if (v !== cleaned) {
                            w.value = cleaned;
                            if (w.inputEl) w.inputEl.value = cleaned;
                        }
                        return oldCallback ? oldCallback.apply(this, [w.value]) : undefined;
                    };
                    return w;
                };

                const mainFolderName = setupWidget("MainFolderName", illegalCharsPath);
                const subFolderName = setupWidget("SubFolderName", illegalCharsAll);
                const fileName = setupWidget("FileName", illegalCharsAll);
                const suffix = setupWidget("Suffix", illegalCharsAll);
                const sameAsSub = node.widgets.find(w => w.name === "same_as_subfolder");
                const noteWidget = node.widgets.find(w => w.name === "note");

                // 1. 定義節點整體的「最小尺寸限制」
                // 這是解決「縮小時反應遲鈍/黏住」的關鍵：告訴引擎這節點最少可以縮到多小
                node.computeSize = function () {
                    let minH = 35; // 標題列高度
                    for (const w of this.widgets) {
                        if (w === noteWidget) {
                            minH += 60; // Note 最小只佔 60
                        } else {
                            // 其他元件按標準或自訂高度計算
                            const wh = w.computeSize ? w.computeSize(this.size[0])[1] : 24;
                            minH += wh + 4;
                        }
                    }
                    return [220, minH];
                };

                // 2. 填充邏輯：將 Note 擴展到當前節點所剩的所有空間
                const adjustNoteWidgetHeight = () => {
                    if (!noteWidget) return;
                    let consumedHeight = 30; // 初始偏移
                    for (const w of node.widgets) {
                        if (w === noteWidget) continue;
                        const h = w.computeSize ? w.computeSize(node.size[0])[1] : 24;
                        consumedHeight += h + 4;
                    }

                    // 填滿剩餘高度
                    const footerMargin = 10;
                    const newHeight = Math.max(60, node.size[1] - consumedHeight - footerMargin);

                    if (noteWidget._last_computed_height !== newHeight) {
                        // 更新 computeSize 讓 Note 的 DOM 元素 (textarea) 實際變長
                        noteWidget.computeSize = (w) => [220, newHeight];
                        noteWidget._last_computed_height = newHeight;
                    }
                };

                // 攔截縮放事件
                const oldOnResize = node.onResize;
                node.onResize = function (size) {
                    adjustNoteWidgetHeight();
                    return oldOnResize ? oldOnResize.apply(this, arguments) : undefined;
                };

                // 初始執行一次
                setTimeout(adjustNoteWidgetHeight, 100);

                // 強制執行邏輯
                node.onDrawBackground = function () {
                    if (this.size[0] < 220) this.size[0] = 220;

                    if (sameAsSub && subFolderName && fileName) {
                        const isSame = !!sameAsSub.value;
                        if (isSame) {
                            if (fileName.value !== subFolderName.value) {
                                fileName.value = subFolderName.value;
                            }
                        }

                        // 處理 UI 狀態
                        if (fileName.inputEl) {
                            fileName.inputEl.readOnly = isSame;
                            fileName.inputEl.style.opacity = isSame ? "0.4" : "1.0";
                            fileName.inputEl.style.pointerEvents = isSame ? "none" : "auto";
                        }
                    }

                    // 執行即時過濾 (針對正在輸入的狀況)
                    const filterTargets = [];
                    if (mainFolderName) filterTargets.push({ w: mainFolderName, r: illegalCharsPath });
                    if (subFolderName) filterTargets.push({ w: subFolderName, r: illegalCharsAll });
                    if (fileName) filterTargets.push({ w: fileName, r: illegalCharsAll });
                    if (suffix) filterTargets.push({ w: suffix, r: illegalCharsAll });

                    filterTargets.forEach(item => {
                        if (item.w && item.w.inputEl) {
                            const v = item.w.inputEl.value;
                            const c = v.replace(item.r, "");
                            if (v !== c) {
                                item.w.inputEl.value = c;
                                item.w.value = c;
                            }
                        }
                    });
                };
            }
        }
    });
})();
