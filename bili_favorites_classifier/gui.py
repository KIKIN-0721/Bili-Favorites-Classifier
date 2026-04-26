from __future__ import annotations

import queue
import threading
import webbrowser
from datetime import datetime
from tkinter import BooleanVar, END, Menu, StringVar, Text, filedialog, messagebox

import ttkbootstrap as tb
from ttkbootstrap.scrolled import ScrolledFrame
from ttkbootstrap.constants import BOTH

from . import __version__
from .api import BilibiliApiClient, BilibiliApiError
from .classifier import SAMPLE_CUSTOM_RULES, classify_videos, move_video_to_group
from .exporter import save_classification_result
from .models import AuthInfo, ClassificationResult, ClassificationRule, FavoriteFolder, SyncSummary, VideoItem


COOKIE_HELP_TEXT = """获取 Cookie 的推荐方式：
1. 在浏览器中打开 https://www.bilibili.com 并确认已经登录目标账号。
2. 按 F12 打开开发者工具。
3. 切到 Network（网络）面板，刷新页面。
4. 点击任意一个发往 bilibili.com 的请求，找到 Request Headers 里的 Cookie。
5. 复制整段 Cookie 字符串，粘贴到本工具。

最低要求：
- Cookie 中至少需要包含 SESSDATA 与 bili_jct。

安全提示：
- Cookie 等同于登录态，请不要发给他人。
- 推荐只在本地临时使用，需要时再粘贴。"""


class FavoritesClassifierApp:
    def __init__(self, root: tb.Window) -> None:
        self.root = root
        self.root.title(f"B站公开收藏夹分类工具 v{__version__}")
        self.root.geometry("1520x920")
        self.root.minsize(1240, 780)
        self.root.configure(padx=0, pady=0)

        self.style = tb.Style()
        self.api_client = BilibiliApiClient()
        self.status_queue: queue.Queue[tuple[str, object]] = queue.Queue()

        self.mode_var = StringVar(value="default")
        self.user_mid_var = StringVar()
        self.status_var = StringVar(value="请输入 Bilibili 用户 ID，然后开始分类。")
        self.summary_var = StringVar(value="尚未生成分类结果。")
        self.version_var = StringVar(value=f"Version {__version__}")
        self.login_info_var = StringVar(value="未检测登录状态")
        self.sync_mode_var = StringVar(value="copy")
        self.sync_privacy_var = StringVar(value="1")
        self.include_unclassified_var = BooleanVar(value=False)
        self.metric_videos_var = StringVar(value="0")
        self.metric_groups_var = StringVar(value="0")
        self.metric_unclassified_var = StringVar(value="0")

        self.rule_rows: list[tuple[tb.Frame, tb.Entry, tb.Entry]] = []
        self.current_result: ClassificationResult | None = None
        self.current_folders: list[FavoriteFolder] = []
        self.current_videos: list[VideoItem] = []
        self.current_owner_name = ""
        self.current_mid = 0
        self.current_auth_info: AuthInfo | None = None
        self.item_metadata: dict[str, dict[str, str]] = {}

        self._build_styles()
        self._build_layout()
        self._set_sample_rules()
        self._poll_status_queue()

    def _build_styles(self) -> None:
        colors = self.style.colors
        self.style.configure("Hero.TFrame", background="#0F172A")
        self.style.configure("HeroTitle.TLabel", background="#0F172A", foreground="#F8FAFC", font=("Segoe UI Semibold", 22))
        self.style.configure("HeroBody.TLabel", background="#0F172A", foreground="#CBD5E1", font=("Segoe UI", 10))
        self.style.configure("HeroBadge.TLabel", background="#1D4ED8", foreground="#F8FAFC", font=("Segoe UI Semibold", 9), padding=(10, 5))
        self.style.configure("AppCard.TFrame", background=colors.bg, relief="flat")
        self.style.configure("MetricCard.TFrame", background="#F8FAFC", relief="solid", borderwidth=1)
        self.style.configure("MetricTitle.TLabel", background="#F8FAFC", foreground="#64748B", font=("Segoe UI", 10))
        self.style.configure("MetricValue.TLabel", background="#F8FAFC", foreground="#0F172A", font=("Segoe UI Semibold", 22))
        self.style.configure("PanelTitle.TLabel", foreground="#0F172A", font=("Segoe UI Semibold", 13))
        self.style.configure("PanelHint.TLabel", foreground="#64748B", font=("Segoe UI", 9))
        self.style.configure("App.Treeview", rowheight=34, font=("Segoe UI", 10))
        self.style.configure("App.Treeview.Heading", font=("Segoe UI Semibold", 10))

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        hero = tb.Frame(self.root, style="Hero.TFrame", padding=(24, 22))
        hero.grid(row=0, column=0, sticky="nsew")
        hero.columnconfigure(0, weight=1)
        hero.columnconfigure(1, weight=0)

        title_box = tb.Frame(hero, style="Hero.TFrame")
        title_box.grid(row=0, column=0, sticky="w")
        tb.Label(title_box, text="B站收藏夹分类与同步工作台", style="HeroTitle.TLabel").pack(anchor="w")
        tb.Label(
            title_box,
            text="抓取公开收藏夹、按分区或自定义规则分类，并把结果同步回你的 B 站账号。",
            style="HeroBody.TLabel",
        ).pack(anchor="w", pady=(6, 0))

        hero_badges = tb.Frame(hero, style="Hero.TFrame")
        hero_badges.grid(row=0, column=1, sticky="e")
        tb.Label(hero_badges, text=self.version_var.get(), style="HeroBadge.TLabel").pack(anchor="e", pady=(0, 8))
        self.hero_status_label = tb.Label(hero_badges, textvariable=self.login_info_var, style="HeroBody.TLabel")
        self.hero_status_label.pack(anchor="e")

        toolbar = tb.Frame(self.root, padding=(20, 16), bootstyle="light")
        toolbar.grid(row=1, column=0, sticky="ew", padx=16, pady=(16, 12))
        toolbar.columnconfigure(10, weight=1)

        tb.Label(toolbar, text="Bilibili UID", style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.user_mid_entry = tb.Entry(toolbar, width=18, textvariable=self.user_mid_var)
        self.user_mid_entry.grid(row=0, column=1, sticky="w", padx=(0, 14))

        self.default_mode_radio = tb.Radiobutton(
            toolbar,
            text="默认分区分类",
            variable=self.mode_var,
            value="default",
            command=self._update_rule_state,
            bootstyle="primary-toolbutton",
        )
        self.default_mode_radio.grid(row=0, column=2, sticky="w", padx=(0, 8))

        self.custom_mode_radio = tb.Radiobutton(
            toolbar,
            text="自定义标签分类",
            variable=self.mode_var,
            value="custom",
            command=self._update_rule_state,
            bootstyle="primary-toolbutton",
        )
        self.custom_mode_radio.grid(row=0, column=3, sticky="w", padx=(0, 14))

        self.fetch_button = tb.Button(toolbar, text="抓取并分类", command=self._start_classification, bootstyle="primary")
        self.fetch_button.grid(row=0, column=4, sticky="w", padx=(0, 8))

        self.save_button = tb.Button(toolbar, text="保存结果", command=self._save_result, state="disabled", bootstyle="secondary")
        self.save_button.grid(row=0, column=5, sticky="w", padx=(0, 8))

        self.sample_button = tb.Button(toolbar, text="填入示例规则", command=self._set_sample_rules, bootstyle="info")
        self.sample_button.grid(row=0, column=6, sticky="w", padx=(0, 8))

        self.add_rule_button = tb.Button(toolbar, text="新增分类", command=self._add_rule_row, bootstyle="success")
        self.add_rule_button.grid(row=0, column=7, sticky="w", padx=(0, 8))

        self.remove_rule_button = tb.Button(toolbar, text="删除末行", command=self._remove_rule_row, bootstyle="danger-outline")
        self.remove_rule_button.grid(row=0, column=8, sticky="w")

        content = tb.Panedwindow(self.root, orient="horizontal")
        content.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 12))

        left_panel = tb.Frame(content, padding=0)
        left_panel.columnconfigure(0, weight=1)
        left_panel.rowconfigure(0, weight=1)
        content.add(left_panel, weight=4)

        right_panel = tb.Frame(content, padding=0)
        right_panel.columnconfigure(0, weight=1)
        right_panel.rowconfigure(1, weight=1)
        content.add(right_panel, weight=8)

        sidebar_card = tb.Frame(left_panel, padding=16, bootstyle="light")
        sidebar_card.grid(row=0, column=0, sticky="nsew")
        sidebar_card.columnconfigure(0, weight=1)
        sidebar_card.rowconfigure(1, weight=1)

        tb.Label(sidebar_card, text="配置中心", style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")
        tb.Label(sidebar_card, text="分类规则和账号同步设置分开放置，减少操作干扰。", style="PanelHint.TLabel").grid(
            row=1, column=0, sticky="w", pady=(4, 12)
        )

        sidebar_tabs = tb.Notebook(sidebar_card, bootstyle="primary")
        sidebar_tabs.grid(row=2, column=0, sticky="nsew")

        rules_tab = tb.Frame(sidebar_tabs, padding=16)
        rules_tab.columnconfigure(0, weight=1)
        rules_tab.rowconfigure(2, weight=1)
        sidebar_tabs.add(rules_tab, text="分类规则")

        sync_tab = tb.Frame(sidebar_tabs, padding=16)
        sync_tab.columnconfigure(0, weight=1)
        sidebar_tabs.add(sync_tab, text="账号同步")

        tb.Label(rules_tab, text="自定义分类规则", style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")
        tb.Label(
            rules_tab,
            text="每个类别填写一个或多个关键 tag。同一视频可以同时进入多个分类。",
            style="PanelHint.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 12))

        self.rules_container = ScrolledFrame(rules_tab, autohide=True, bootstyle="round")
        self.rules_container.grid(row=2, column=0, sticky="nsew")
        self.rules_content = self.rules_container
        self._build_rule_editor_headers()

        tb.Label(sync_tab, text="账号同步到 B 站", style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")
        tb.Label(
            sync_tab,
            text="支持 Cookie 登录检测，并将当前分类结果复制或移动到 B 站收藏夹。",
            style="PanelHint.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 12))

        tb.Label(sync_tab, text="完整 Cookie", font=("Segoe UI Semibold", 10)).grid(row=2, column=0, sticky="w", pady=(0, 6))
        cookie_frame = tb.Frame(sync_tab, bootstyle="light")
        cookie_frame.grid(row=3, column=0, sticky="ew")
        cookie_frame.columnconfigure(0, weight=1)
        self.cookie_text = Text(
            cookie_frame,
            height=7,
            wrap="word",
            relief="flat",
            bd=0,
            font=("Consolas", 9),
            background="#F8FAFC",
            foreground="#0F172A",
            insertbackground="#0F172A",
        )
        self.cookie_text.grid(row=0, column=0, sticky="ew")

        auth_button_row = tb.Frame(sync_tab)
        auth_button_row.grid(row=4, column=0, sticky="ew", pady=(10, 12))
        auth_button_row.columnconfigure(4, weight=1)

        self.check_login_button = tb.Button(auth_button_row, text="检测登录", command=self._start_login_check, bootstyle="primary")
        self.check_login_button.grid(row=0, column=0, sticky="w", padx=(0, 8))
        tb.Button(auth_button_row, text="清空 Cookie", command=self._clear_cookie_text, bootstyle="secondary").grid(
            row=0, column=1, sticky="w", padx=(0, 8)
        )
        tb.Button(auth_button_row, text="获取说明", command=self._show_cookie_help, bootstyle="info-outline").grid(
            row=0, column=2, sticky="w"
        )
        tb.Label(auth_button_row, textvariable=self.login_info_var, style="PanelHint.TLabel").grid(row=0, column=4, sticky="e")

        sync_mode_card = tb.Frame(sync_tab, padding=14, bootstyle="light")
        sync_mode_card.grid(row=5, column=0, sticky="ew", pady=(0, 12))
        sync_mode_card.columnconfigure(0, weight=1)
        sync_mode_card.columnconfigure(1, weight=1)

        left_sync = tb.Frame(sync_mode_card)
        left_sync.grid(row=0, column=0, sticky="w")
        tb.Label(left_sync, text="同步方式", font=("Segoe UI Semibold", 10)).grid(row=0, column=0, sticky="w", pady=(0, 6))
        tb.Radiobutton(left_sync, text="复制到分类收藏夹", value="copy", variable=self.sync_mode_var, bootstyle="success-toolbutton").grid(
            row=1, column=0, sticky="w", padx=(0, 8)
        )
        tb.Radiobutton(left_sync, text="移动到分类收藏夹", value="move", variable=self.sync_mode_var, bootstyle="warning-toolbutton").grid(
            row=1, column=1, sticky="w"
        )

        right_sync = tb.Frame(sync_mode_card)
        right_sync.grid(row=0, column=1, sticky="e")
        tb.Label(right_sync, text="新建收藏夹权限", font=("Segoe UI Semibold", 10)).grid(row=0, column=0, sticky="w", pady=(0, 6))
        tb.Radiobutton(right_sync, text="私密", value="1", variable=self.sync_privacy_var, bootstyle="secondary-toolbutton").grid(
            row=1, column=0, sticky="w", padx=(0, 8)
        )
        tb.Radiobutton(right_sync, text="公开", value="0", variable=self.sync_privacy_var, bootstyle="secondary-toolbutton").grid(
            row=1, column=1, sticky="w", padx=(0, 8)
        )
        tb.Checkbutton(right_sync, text="包含未分类", variable=self.include_unclassified_var, bootstyle="round-toggle").grid(
            row=1, column=2, sticky="w"
        )

        self.sync_button = tb.Button(
            sync_tab,
            text="应用分类结果到 B 站",
            command=self._start_sync_to_bilibili,
            state="disabled",
            bootstyle="primary",
        )
        self.sync_button.grid(row=6, column=0, sticky="ew")

        results_card = tb.Frame(right_panel, padding=16, bootstyle="light")
        results_card.grid(row=0, column=0, rowspan=2, sticky="nsew")
        results_card.columnconfigure(0, weight=1)
        results_card.rowconfigure(2, weight=1)

        results_header = tb.Frame(results_card)
        results_header.grid(row=0, column=0, sticky="ew")
        results_header.columnconfigure(0, weight=1)
        tb.Label(results_header, text="分类结果", style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")
        tb.Label(
            results_header,
            text="双击视频打开网页，右键可移动到其他分类；同步前建议先在这里整理结果。",
            style="PanelHint.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        metrics_row = tb.Frame(results_card)
        metrics_row.grid(row=1, column=0, sticky="ew", pady=(14, 14))
        metrics_row.columnconfigure((0, 1, 2), weight=1)
        self._build_metric_card(metrics_row, 0, "视频总数", self.metric_videos_var, "primary")
        self._build_metric_card(metrics_row, 1, "分类数量", self.metric_groups_var, "info")
        self._build_metric_card(metrics_row, 2, "未分类", self.metric_unclassified_var, "warning")

        tree_shell = tb.Frame(results_card, padding=10, bootstyle="default")
        tree_shell.grid(row=2, column=0, sticky="nsew")
        tree_shell.columnconfigure(0, weight=1)
        tree_shell.rowconfigure(0, weight=1)

        columns = ("title", "bvid", "partition", "tags", "folders", "url")
        self.tree = tb.Treeview(tree_shell, columns=columns, show="tree headings", style="App.Treeview")
        self.tree.heading("#0", text="分类")
        self.tree.heading("title", text="标题")
        self.tree.heading("bvid", text="BV号")
        self.tree.heading("partition", text="视频分区")
        self.tree.heading("tags", text="标签")
        self.tree.heading("folders", text="来源收藏夹")
        self.tree.heading("url", text="视频链接")
        self.tree.column("#0", width=240, anchor="w")
        self.tree.column("title", width=340, anchor="w")
        self.tree.column("bvid", width=150, anchor="w")
        self.tree.column("partition", width=150, anchor="w")
        self.tree.column("tags", width=260, anchor="w")
        self.tree.column("folders", width=200, anchor="w")
        self.tree.column("url", width=260, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", self._open_selected_video)
        self.tree.bind("<Button-3>", self._show_context_menu)

        scrollbar = tb.Scrollbar(tree_shell, orient="vertical", command=self.tree.yview, bootstyle="round")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        footer = tb.Frame(self.root, padding=(18, 12), bootstyle="light")
        footer.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 16))
        footer.columnconfigure(0, weight=1)
        footer.columnconfigure(1, weight=1)
        footer.columnconfigure(2, weight=0)
        tb.Label(footer, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        tb.Label(footer, textvariable=self.summary_var).grid(row=0, column=1, sticky="e", padx=(12, 12))
        tb.Label(footer, textvariable=self.version_var, style="PanelHint.TLabel").grid(row=0, column=2, sticky="e")

        self._update_rule_state()

    def _build_metric_card(self, parent: tb.Frame, column: int, title: str, value_var: StringVar, accent: str) -> None:
        card = tb.Frame(parent, padding=14, style="MetricCard.TFrame")
        card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 8, 8 if column < 2 else 0))
        card.columnconfigure(0, weight=1)
        badge = tb.Label(card, text=title, bootstyle=f"{accent}-inverse", padding=(10, 4))
        badge.grid(row=0, column=0, sticky="w")
        tb.Label(card, textvariable=value_var, style="MetricValue.TLabel").grid(row=1, column=0, sticky="w", pady=(10, 4))

    def _build_rule_editor_headers(self) -> None:
        for child in self.rules_content.winfo_children():
            child.destroy()
        header_frame = tb.Frame(self.rules_content)
        header_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header_frame.columnconfigure(1, weight=1)
        tb.Label(header_frame, text="类别名", font=("Segoe UI Semibold", 10)).grid(row=0, column=0, sticky="w", padx=(0, 10))
        tb.Label(header_frame, text="关键 tag（英文逗号分隔）", font=("Segoe UI Semibold", 10)).grid(row=0, column=1, sticky="w")

    def _add_rule_row(self, name: str = "", keywords: str = "") -> None:
        row_index = len(self.rule_rows) + 1
        row_frame = tb.Frame(self.rules_content, padding=(0, 0, 0, 4))
        row_frame.grid(row=row_index, column=0, sticky="ew", pady=(0, 8))
        row_frame.columnconfigure(1, weight=1)

        name_entry = tb.Entry(row_frame, width=16)
        name_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        keywords_entry = tb.Entry(row_frame)
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
        self.sync_button.configure(state="disabled")
        self.status_var.set("准备开始抓取 B 站公开收藏夹数据...")
        self.summary_var.set("正在处理中，请稍候。")
        self._clear_tree()
        self._set_metric_values(0, 0, 0)

        self._apply_cookie_from_input()
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
                elif event_type == "auth_done":
                    self._handle_auth_result(payload)
                elif event_type == "sync_done":
                    self._handle_sync_result(payload)
                elif event_type == "error":
                    self.fetch_button.configure(state="normal")
                    self.check_login_button.configure(state="normal")
                    self._refresh_sync_button_state()
                    if self.current_result is None:
                        self.save_button.configure(state="disabled")
                    self.status_var.set("执行失败。")
                    if self.current_result is None:
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
        self._refresh_sync_button_state()
        owner_name = self.current_owner_name or f"用户 {self.current_mid}"
        self.status_var.set(f"分类完成：{owner_name}，共读取 {len(self.current_folders)} 个公开收藏夹。")

    def _handle_auth_result(self, auth_info: AuthInfo) -> None:
        self.current_auth_info = auth_info
        self.check_login_button.configure(state="normal")
        self.login_info_var.set(f"已登录：{auth_info.uname} (MID {auth_info.mid})")
        self.status_var.set("登录检测完成。")
        self._refresh_sync_button_state()

    def _handle_sync_result(self, summary: SyncSummary) -> None:
        self.fetch_button.configure(state="normal")
        self.check_login_button.configure(state="normal")
        self._refresh_sync_button_state()

        changed_count = summary.copied_count if summary.mode == "copy" else summary.moved_count
        action_label = "复制" if summary.mode == "copy" else "移动"
        self.status_var.set(f"已完成同步：{action_label} {changed_count} 条视频到 B 站收藏夹。")

        parts = [
            f"同步模式：{'复制' if summary.mode == 'copy' else '移动'}",
            f"目标用户 MID：{summary.target_mid}",
            f"创建收藏夹：{', '.join(summary.created_folders) if summary.created_folders else '无'}",
            f"复用收藏夹：{', '.join(summary.reused_folders) if summary.reused_folders else '无'}",
            f"成功处理数量：{changed_count}",
            f"跳过数量：{len(summary.skipped_videos)}",
        ]
        if summary.skipped_videos:
            preview = "\n".join(summary.skipped_videos[:8])
            parts.append(f"跳过明细（最多显示 8 条）：\n{preview}")

        messagebox.showinfo("同步完成", "\n".join(parts))

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
            self._set_metric_values(0, 0, 0)
            return

        total_videos = self.current_result.total_videos
        total_groups = len(self.current_result.groups)
        unclassified = self.current_result.unclassified_count
        self.summary_var.set(f"视频数 {total_videos}，分组数 {total_groups}，未分类 {unclassified}")
        self._set_metric_values(total_videos, total_groups, unclassified)

    def _set_metric_values(self, videos: int, groups: int, unclassified: int) -> None:
        self.metric_videos_var.set(str(videos))
        self.metric_groups_var.set(str(groups))
        self.metric_unclassified_var.set(str(unclassified))

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

    def _apply_cookie_from_input(self) -> None:
        raw_cookie = self.cookie_text.get("1.0", END).strip()
        if raw_cookie:
            self.api_client.set_auth_cookie(raw_cookie)
        else:
            self.api_client.clear_auth_cookie()
            self.current_auth_info = None
            self.login_info_var.set("未检测登录状态")

    def _clear_cookie_text(self) -> None:
        self.cookie_text.delete("1.0", END)
        self.api_client.clear_auth_cookie()
        self.current_auth_info = None
        self.login_info_var.set("未检测登录状态")
        self._refresh_sync_button_state()

    def _show_cookie_help(self) -> None:
        messagebox.showinfo("Cookie 获取说明", COOKIE_HELP_TEXT)

    def _start_login_check(self) -> None:
        self._apply_cookie_from_input()
        if not self.api_client.has_auth_cookie():
            messagebox.showerror("缺少 Cookie", "请先粘贴完整 Cookie，再进行登录检测。")
            return

        self.check_login_button.configure(state="disabled")
        self.status_var.set("正在检测当前 Cookie 的登录状态...")
        worker = threading.Thread(target=self._run_login_check_worker, daemon=True)
        worker.start()

    def _run_login_check_worker(self) -> None:
        try:
            auth_info = self.api_client.fetch_authenticated_user()
            self.status_queue.put(("auth_done", auth_info))
        except Exception as exc:
            self.status_queue.put(("error", str(exc)))

    def _start_sync_to_bilibili(self) -> None:
        if self.current_result is None:
            messagebox.showerror("没有结果", "请先完成一次分类。")
            return

        self._apply_cookie_from_input()
        if not self.api_client.has_auth_cookie():
            messagebox.showerror("缺少 Cookie", "请先粘贴完整 Cookie，并检测登录后再同步。")
            return

        mode_label = "复制" if self.sync_mode_var.get() == "copy" else "移动"
        privacy_label = "私密" if self.sync_privacy_var.get() == "1" else "公开"
        include_unclassified = "包含" if self.include_unclassified_var.get() else "不包含"
        confirmed = messagebox.askyesno(
            "确认同步",
            f"将把当前分类结果{mode_label}到 B 站收藏夹。\n\n"
            f"目标 UID：{self.current_mid}\n"
            f"同步方式：{mode_label}\n"
            f"新建收藏夹权限：{privacy_label}\n"
            f"未分类：{include_unclassified}\n\n"
            "是否继续？",
        )
        if not confirmed:
            return

        self.fetch_button.configure(state="disabled")
        self.check_login_button.configure(state="disabled")
        self.sync_button.configure(state="disabled")
        self.status_var.set("正在将分类结果同步到 B 站收藏夹，请稍候...")
        worker = threading.Thread(target=self._run_sync_worker, daemon=True)
        worker.start()

    def _run_sync_worker(self) -> None:
        try:
            summary = self.api_client.sync_classification_result(
                self.current_result,
                target_user_mid=self.current_mid,
                include_unclassified=self.include_unclassified_var.get(),
                sync_mode=self.sync_mode_var.get(),
                privacy=int(self.sync_privacy_var.get()),
                progress_callback=lambda text: self.status_queue.put(("status", text)),
            )
            self.status_queue.put(("sync_done", summary))
        except Exception as exc:
            self.status_queue.put(("error", str(exc)))

    def _refresh_sync_button_state(self) -> None:
        if self.current_result is None:
            self.sync_button.configure(state="disabled")
            return
        self.sync_button.configure(state="normal")


def run_app() -> None:
    root = tb.Window(themename="flatly")
    app = FavoritesClassifierApp(root)
    app.user_mid_entry.focus_set()
    root.mainloop()
