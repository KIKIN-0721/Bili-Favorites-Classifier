from __future__ import annotations

import queue
import threading
import webbrowser
from datetime import datetime
from tkinter import END, Menu, VERTICAL, W, StringVar, Tk, filedialog, messagebox
from tkinter import ttk

from . import __version__
from .api import BilibiliApiClient, BilibiliApiError
from .classifier import SAMPLE_CUSTOM_RULES, classify_videos, move_video_to_group
from .exporter import save_classification_result
from .models import ClassificationResult, ClassificationRule, FavoriteFolder, VideoItem


class FavoritesClassifierApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title(f"B站公开收藏夹分类工具 v{__version__}")
        self.root.geometry("1360x780")
        self.root.minsize(1120, 700)

        self.api_client = BilibiliApiClient()
        self.status_queue: queue.Queue[tuple[str, object]] = queue.Queue()

        self.mode_var = StringVar(value="default")
        self.user_mid_var = StringVar()
        self.status_var = StringVar(value="请输入 Bilibili 用户 ID，然后开始分类。")
        self.summary_var = StringVar(value="尚未生成分类结果。")
        self.version_var = StringVar(value=f"版本 v{__version__}")

        self.rule_rows: list[tuple[ttk.Frame, ttk.Entry, ttk.Entry]] = []
        self.current_result: ClassificationResult | None = None
        self.current_folders: list[FavoriteFolder] = []
        self.current_videos: list[VideoItem] = []
        self.current_owner_name = ""
        self.current_mid = 0
        self.item_metadata: dict[str, dict[str, str]] = {}

        self._build_layout()
        self._set_sample_rules()
        self._poll_status_queue()

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        top_frame = ttk.Frame(self.root, padding=12)
        top_frame.grid(row=0, column=0, sticky="ew")
        top_frame.columnconfigure(12, weight=1)

        ttk.Label(top_frame, text="Bilibili 用户 ID").grid(row=0, column=0, sticky=W, padx=(0, 8))
        self.user_mid_entry = ttk.Entry(top_frame, width=20, textvariable=self.user_mid_var)
        self.user_mid_entry.grid(row=0, column=1, sticky=W, padx=(0, 12))

        ttk.Radiobutton(top_frame, text="默认分类", value="default", variable=self.mode_var, command=self._update_rule_state).grid(
            row=0, column=2, sticky=W, padx=(0, 8)
        )
        ttk.Radiobutton(top_frame, text="自定义分类", value="custom", variable=self.mode_var, command=self._update_rule_state).grid(
            row=0, column=3, sticky=W, padx=(0, 16)
        )

        self.fetch_button = ttk.Button(top_frame, text="抓取并分类", command=self._start_classification)
        self.fetch_button.grid(row=0, column=4, sticky=W, padx=(0, 8))

        self.save_button = ttk.Button(top_frame, text="保存结果", command=self._save_result, state="disabled")
        self.save_button.grid(row=0, column=5, sticky=W, padx=(0, 8))

        self.sample_button = ttk.Button(top_frame, text="填入示例规则", command=self._set_sample_rules)
        self.sample_button.grid(row=0, column=6, sticky=W, padx=(0, 8))

        self.add_rule_button = ttk.Button(top_frame, text="新增分类", command=self._add_rule_row)
        self.add_rule_button.grid(row=0, column=7, sticky=W, padx=(0, 8))

        self.remove_rule_button = ttk.Button(top_frame, text="删除末行", command=self._remove_rule_row)
        self.remove_rule_button.grid(row=0, column=8, sticky=W, padx=(0, 8))

        content = ttk.Panedwindow(self.root, orient="horizontal")
        content.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        rules_container = ttk.Frame(content, padding=12)
        rules_container.columnconfigure(0, weight=1)
        rules_container.rowconfigure(2, weight=1)
        content.add(rules_container, weight=3)

        ttk.Label(rules_container, text="自定义分类规则").grid(row=0, column=0, sticky=W)
        self.rule_hint_label = ttk.Label(
            rules_container,
            text="默认模式下不使用这里的规则。自定义模式下，每个类别填写一个或多个 tag 关键词；同一视频可同时进入多个类别。",
            foreground="#555555",
        )
        self.rule_hint_label.grid(row=1, column=0, sticky=W, pady=(4, 12))

        self.rules_scroll = ttk.Frame(rules_container)
        self.rules_scroll.grid(row=2, column=0, sticky="nsew")
        self.rules_scroll.columnconfigure(0, weight=1)

        results_container = ttk.Frame(content, padding=12)
        results_container.columnconfigure(0, weight=1)
        results_container.rowconfigure(2, weight=1)
        content.add(results_container, weight=7)

        ttk.Label(results_container, text="分类结果").grid(row=0, column=0, sticky=W)
        ttk.Label(
            results_container,
            text="双击视频可打开网页，右键视频可移动到其他分类。",
            foreground="#555555",
        ).grid(row=1, column=0, sticky=W, pady=(4, 12))

        tree_frame = ttk.Frame(results_container)
        tree_frame.grid(row=2, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        columns = ("title", "bvid", "partition", "tags", "folders", "url")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="tree headings")
        self.tree.heading("#0", text="分类")
        self.tree.heading("title", text="标题")
        self.tree.heading("bvid", text="BV号")
        self.tree.heading("partition", text="视频分区")
        self.tree.heading("tags", text="标签")
        self.tree.heading("folders", text="来源收藏夹")
        self.tree.heading("url", text="视频链接")
        self.tree.column("#0", width=220, anchor="w")
        self.tree.column("title", width=300, anchor="w")
        self.tree.column("bvid", width=140, anchor="w")
        self.tree.column("partition", width=150, anchor="w")
        self.tree.column("tags", width=220, anchor="w")
        self.tree.column("folders", width=170, anchor="w")
        self.tree.column("url", width=250, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", self._open_selected_video)
        self.tree.bind("<Button-3>", self._show_context_menu)

        scrollbar = ttk.Scrollbar(tree_frame, orient=VERTICAL, command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        bottom_frame = ttk.Frame(self.root, padding=(12, 0, 12, 12))
        bottom_frame.grid(row=2, column=0, sticky="ew")
        bottom_frame.columnconfigure(0, weight=1)
        bottom_frame.columnconfigure(1, weight=1)
        bottom_frame.columnconfigure(2, weight=0)

        ttk.Label(bottom_frame, textvariable=self.status_var).grid(row=0, column=0, sticky=W)
        ttk.Label(bottom_frame, textvariable=self.summary_var, anchor="e").grid(row=0, column=1, sticky="e", padx=(12, 12))
        ttk.Label(bottom_frame, textvariable=self.version_var, foreground="#666666").grid(row=0, column=2, sticky="e")

        self._build_rule_editor_headers()
        self._update_rule_state()

    def _build_rule_editor_headers(self) -> None:
        for child in self.rules_scroll.winfo_children():
            child.destroy()

        header_frame = ttk.Frame(self.rules_scroll)
        header_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header_frame.columnconfigure(1, weight=1)
        ttk.Label(header_frame, text="类别名", width=14).grid(row=0, column=0, sticky=W, padx=(0, 8))
        ttk.Label(header_frame, text="关键 tag（英文逗号分隔）").grid(row=0, column=1, sticky=W)

    def _add_rule_row(self, name: str = "", keywords: str = "") -> None:
        if not self.rules_scroll.winfo_exists():
            return
        if not self.rule_rows:
            self._build_rule_editor_headers()

        row_index = len(self.rule_rows) + 1
        row_frame = ttk.Frame(self.rules_scroll)
        row_frame.grid(row=row_index, column=0, sticky="ew", pady=4)
        row_frame.columnconfigure(1, weight=1)

        name_entry = ttk.Entry(row_frame, width=16)
        name_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        keywords_entry = ttk.Entry(row_frame)
        keywords_entry.grid(row=0, column=1, sticky="ew")

        if name:
            name_entry.insert(0, name)
        if keywords:
            keywords_entry.insert(0, keywords)

        self.rule_rows.append((row_frame, name_entry, keywords_entry))
        self._update_rule_state()

    def _remove_rule_row(self) -> None:
        if len(self.rule_rows) <= 1:
            return
        row_frame, _, _ = self.rule_rows.pop()
        row_frame.destroy()
        self._update_rule_state()

    def _set_sample_rules(self) -> None:
        for row_frame, _, _ in self.rule_rows:
            row_frame.destroy()
        self.rule_rows.clear()
        self._build_rule_editor_headers()
        for rule in SAMPLE_CUSTOM_RULES:
            self._add_rule_row(rule.name, ", ".join(rule.keywords))
        self._update_rule_state()

    def _update_rule_state(self) -> None:
        custom_enabled = self.mode_var.get() == "custom"
        entry_state = "normal" if custom_enabled else "disabled"
        button_state = "normal" if custom_enabled else "disabled"

        for _, name_entry, keywords_entry in self.rule_rows:
            name_entry.configure(state=entry_state)
            keywords_entry.configure(state=entry_state)

        self.sample_button.configure(state=button_state)
        self.add_rule_button.configure(state=button_state)
        self.remove_rule_button.configure(state=button_state)

    def _start_classification(self) -> None:
        user_mid_text = self.user_mid_var.get().strip()
        if not user_mid_text.isdigit():
            messagebox.showerror("输入错误", "请输入纯数字格式的 Bilibili 用户 ID。")
            return

        rules = self._collect_custom_rules()
        if self.mode_var.get() == "custom" and not rules:
            messagebox.showerror("规则错误", "自定义模式下至少需要填写一条完整规则。")
            return

        self.fetch_button.configure(state="disabled")
        self.save_button.configure(state="disabled")
        self.status_var.set("准备开始抓取 B 站公开收藏夹数据...")
        self.summary_var.set("正在处理中，请稍候。")
        self._clear_tree()

        user_mid = int(user_mid_text)
        worker = threading.Thread(
            target=self._run_classification_worker,
            args=(user_mid, self.mode_var.get(), rules),
            daemon=True,
        )
        worker.start()

    def _run_classification_worker(
        self,
        user_mid: int,
        mode: str,
        rules: list[ClassificationRule],
    ) -> None:
        try:
            folders, videos, owner_name = self.api_client.fetch_user_videos(
                user_mid,
                progress_callback=lambda text: self.status_queue.put(("status", text)),
            )
            result = classify_videos(videos, mode=mode, custom_rules=rules)
            self.status_queue.put(
                (
                    "done",
                    {
                        "user_mid": user_mid,
                        "owner_name": owner_name,
                        "folders": folders,
                        "videos": videos,
                        "result": result,
                    },
                )
            )
        except (BilibiliApiError, RuntimeError, ValueError) as exc:
            self.status_queue.put(("error", str(exc)))
        except Exception as exc:  # pragma: no cover
            self.status_queue.put(("error", f"处理过程中出现未预期错误：{exc}"))

    def _poll_status_queue(self) -> None:
        try:
            while True:
                event_type, payload = self.status_queue.get_nowait()
                if event_type == "status":
                    self.status_var.set(str(payload))
                elif event_type == "done":
                    self._handle_finished_result(payload)
                elif event_type == "error":
                    self.fetch_button.configure(state="normal")
                    self.save_button.configure(state="disabled")
                    self.status_var.set("执行失败。")
                    self.summary_var.set("没有可用结果。")
                    messagebox.showerror("执行失败", str(payload))
        except queue.Empty:
            pass
        self.root.after(150, self._poll_status_queue)

    def _handle_finished_result(self, payload: dict[str, object]) -> None:
        self.fetch_button.configure(state="normal")
        self.save_button.configure(state="normal")

        self.current_mid = int(payload["user_mid"])
        self.current_owner_name = str(payload["owner_name"])
        self.current_folders = list(payload["folders"])
        self.current_videos = list(payload["videos"])
        self.current_result = payload["result"]

        self._render_result(self.current_result)
        self._refresh_summary()
        owner_name = self.current_owner_name or f"用户 {self.current_mid}"
        self.status_var.set(f"分类完成：{owner_name}，共读取 {len(self.current_folders)} 个公开收藏夹。")

    def _render_result(self, result: ClassificationResult) -> None:
        self._clear_tree()
        for group in result.groups:
            group_id = self.tree.insert(
                "",
                "end",
                text=f"{group.name} ({len(group.videos)})",
                values=("", "", "", "", "", ""),
                open=True,
            )
            for video in group.videos:
                item_id = self.tree.insert(
                    group_id,
                    "end",
                    text="",
                    values=(
                        video.title,
                        video.bvid,
                        video.partition_name,
                        " / ".join(video.tags),
                        " / ".join(video.source_folders),
                        video.url,
                    ),
                )
                self.item_metadata[item_id] = {
                    "url": video.url,
                    "bvid": video.bvid,
                    "group": group.name,
                }

    def _clear_tree(self) -> None:
        self.item_metadata.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)

    def _open_selected_video(self, _event: object) -> None:
        selected = self.tree.focus()
        metadata = self.item_metadata.get(selected)
        if metadata and metadata.get("url"):
            webbrowser.open_new_tab(metadata["url"])

    def _show_context_menu(self, event: object) -> None:
        if self.current_result is None:
            return

        row_id = self.tree.identify_row(event.y)
        if row_id not in self.item_metadata:
            return

        metadata = self.item_metadata[row_id]
        source_group = metadata["group"]
        url = metadata["url"]

        self.tree.selection_set(row_id)
        self.tree.focus(row_id)

        menu = Menu(self.root, tearoff=0)
        menu.add_command(label="打开视频", command=lambda: webbrowser.open_new_tab(url))

        move_menu = Menu(menu, tearoff=0)
        target_names = [group.name for group in self.current_result.groups if group.name != source_group]
        if target_names:
            for target_name in target_names:
                move_menu.add_command(
                    label=f"移动到 {target_name}",
                    command=lambda item_id=row_id, target=target_name: self._move_selected_video(item_id, target),
                )
        else:
            move_menu.add_command(label="没有可用目标分类", state="disabled")
        menu.add_cascade(label="移动到其他分类", menu=move_menu)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _move_selected_video(self, item_id: str, target_group_name: str) -> None:
        if self.current_result is None:
            return

        metadata = self.item_metadata.get(item_id)
        if not metadata:
            return

        video = self._find_video(metadata["bvid"])
        if video is None:
            return

        moved = move_video_to_group(self.current_result, video, metadata["group"], target_group_name)
        if not moved:
            return

        self._render_result(self.current_result)
        self._refresh_summary()
        self.status_var.set(f"已将 {video.title} 从“{metadata['group']}”移动到“{target_group_name}”。")

    def _find_video(self, bvid: str) -> VideoItem | None:
        for video in self.current_videos:
            if video.bvid == bvid:
                return video
        return None

    def _collect_custom_rules(self) -> list[ClassificationRule]:
        rules: list[ClassificationRule] = []
        for _, name_entry, keywords_entry in self.rule_rows:
            name = name_entry.get().strip()
            keywords_text = keywords_entry.get().strip()
            keywords = [keyword.strip() for keyword in keywords_text.split(",") if keyword.strip()]
            if name and keywords:
                rules.append(ClassificationRule(name=name, keywords=keywords))
        return rules

    def _refresh_summary(self) -> None:
        if not self.current_result:
            self.summary_var.set("尚未生成分类结果。")
            return
        self.summary_var.set(
            f"视频数 {self.current_result.total_videos}，分组数 {len(self.current_result.groups)}，未分类 {self.current_result.unclassified_count}"
        )

    def _save_result(self) -> None:
        if not self.current_result:
            messagebox.showinfo("暂无结果", "请先完成一次分类。")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"bili_favorites_{self.current_mid}_{timestamp}.json"
        file_path = filedialog.asksaveasfilename(
            title="保存分类结果",
            defaultextension=".json",
            initialfile=default_name,
            filetypes=[("JSON 文件", "*.json"), ("CSV 文件", "*.csv")],
        )
        if not file_path:
            return

        try:
            saved_path = save_classification_result(
                file_path,
                user_mid=self.current_mid,
                owner_name=self.current_owner_name,
                folders=self.current_folders,
                result=self.current_result,
            )
        except Exception as exc:  # pragma: no cover
            messagebox.showerror("保存失败", f"保存结果时发生错误：{exc}")
            return

        self.status_var.set(f"结果已保存到：{saved_path}")
        messagebox.showinfo("保存成功", f"分类结果已保存到：\n{saved_path}")


def run_app() -> None:
    root = Tk()
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    app = FavoritesClassifierApp(root)
    app.user_mid_entry.focus_set()
    root.mainloop()
