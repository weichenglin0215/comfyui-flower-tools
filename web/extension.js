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

            // 1. 固定鎖定前 7 個 (索引 0-6)。其餘全部移除
            if (node.widgets.length > 7) {
                for (let i = node.widgets.length - 1; i >= 7; i--) {
                    const w = node.widgets[i];
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

            // 3. 從索引 7 開始新增按鈕，並加入間距
            for (const file of displayList) {
                const widget = node.addWidget("button", file.name, null, () => {
                    node.showSelectionPopup(file.name);
                });
                widget.type = "button";
                widget.last_count = file.count || "?";

                // 設定高度 40 (35橫條 + 5間隔)
                widget.computeSize = (w) => [w, 40];

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

            // --- 🌸 建立核心地基 (索引順序守護) 🌸 ---

            // 前面四個由 Python 給定: directory(0), seed(1), seed_control(2), continuous_processing(3), file_configs(4)

            // 4. Result (result_dialog) - 改用 ComfyUI 標準元件寫法。
            if (!this.widgets.find(w => w.name === "result_dialog")) {
                // 使用 ComfyUI 官方的多行文本元件建立方式
                const res = ComfyWidgets["STRING"](this, "result_dialog", ["STRING", { multiline: true }], app).widget;

                res.label = "Final Selection";
                res.value = "";
                res.serialize = false;

                // 設定高度
                res.computeSize = () => [this.size[0], 150];

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
            }

            // --- 強制地基排序 (防止索引偏移) ---
            const baseOrder = ["directory", "seed", "seed_control", "continuous_processing", "file_configs", "result_dialog", "refresh_btn"];
            const reorder = () => {
                baseOrder.forEach((name, targetIdx) => {
                    const idx = this.widgets.findIndex(w => w.name === name);
                    if (idx !== -1 && idx !== targetIdx) {
                        const w = this.widgets.splice(idx, 1)[0];
                        this.widgets.splice(targetIdx, 0, w);
                    }
                });
            };
            reorder();

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
                mainText.computeSize = (w) => [w, 120];
            }

            // 調整節點寬度以容納多組輸入
            this.size[0] = 440;
        };
    };

    app.registerExtension({
        name: "Flower.MultilinePromptSelector.V31",
        async beforeRegisterNodeDef(nodeType, nodeData, app) {
            if (nodeData.name === TARGET_KEY) {
                setupNode(nodeType, nodeData.name);
            } else if (nodeData.name === "FlowerKeywordReplacer") {
                setupKeywordReplacer(nodeType, nodeData.name);
            }
        }
    });
})();
