from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.cookies import SimpleCookie
from typing import Callable

from .models import (
    AuthInfo,
    ClassificationResult,
    FavoriteFolder,
    FavoriteSourceRef,
    SyncSummary,
    VideoItem,
)
from .partition_map import resolve_partition_info


ProgressCallback = Callable[[str], None]


class BilibiliApiError(RuntimeError):
    """Raised when a Bilibili API call fails."""


class BilibiliApiClient:
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )
    FAVORITE_FOLDER_URL = "https://api.bilibili.com/x/v3/fav/folder/created/list-all"
    FAVORITE_RESOURCE_URL = "https://api.bilibili.com/x/v3/fav/resource/list"
    FAVORITE_FOLDER_ADD_URL = "https://api.bilibili.com/x/v3/fav/folder/add"
    FAVORITE_RESOURCE_COPY_URL = "https://api.bilibili.com/x/v3/fav/resource/copy"
    FAVORITE_RESOURCE_MOVE_URL = "https://api.bilibili.com/x/v3/fav/resource/move"
    VIDEO_TAGS_URL = "https://api.bilibili.com/x/web-interface/view/detail/tag"
    VIDEO_VIEW_URL = "https://api.bilibili.com/x/web-interface/view"
    NAV_URL = "https://api.bilibili.com/x/web-interface/nav"

    def __init__(
        self,
        request_interval: float = 0.05,
        tag_workers: int = 8,
        max_retries: int = 3,
        auth_cookie: str | None = None,
    ) -> None:
        self.request_interval = request_interval
        self.tag_workers = tag_workers
        self.max_retries = max_retries
        self.auth_cookie = ""
        if auth_cookie:
            self.set_auth_cookie(auth_cookie)

    def set_auth_cookie(self, raw_cookie: str) -> None:
        normalized = "; ".join(
            segment.strip()
            for segment in raw_cookie.replace("\n", ";").split(";")
            if segment.strip()
        )
        self.auth_cookie = normalized

    def clear_auth_cookie(self) -> None:
        self.auth_cookie = ""

    def has_auth_cookie(self) -> bool:
        return bool(self.auth_cookie)

    def fetch_authenticated_user(self) -> AuthInfo:
        payload = self._request_json(
            "GET",
            self.NAV_URL,
            referer="https://www.bilibili.com/",
            require_auth=False,
            allowed_codes={0, -101},
        )
        data = payload.get("data") or {}
        if payload.get("code") == -101 or not data.get("isLogin"):
            raise BilibiliApiError("当前 Cookie 未登录，或缺少必要字段。请检查 SESSDATA 与 bili_jct。")
        return AuthInfo(
            mid=int(data.get("mid", 0) or 0),
            uname=str(data.get("uname", "") or ""),
            is_login=bool(data.get("isLogin")),
        )

    def fetch_user_videos(
        self,
        user_mid: int,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[list[FavoriteFolder], list[VideoItem], str]:
        folders = self.fetch_public_favorite_folders(user_mid)
        if not folders:
            raise BilibiliApiError("该用户没有公开收藏夹，或当前网络环境下无法访问公开收藏夹数据。")

        if progress_callback:
            progress_callback(f"已获取 {len(folders)} 个公开收藏夹，正在拉取视频列表...")

        videos_by_bvid: dict[str, VideoItem] = {}
        owner_name = ""

        for index, folder in enumerate(folders, start=1):
            medias, folder_owner_name = self.fetch_folder_medias(folder.folder_id, owner_mid=folder.owner_mid)
            if folder_owner_name and not owner_name:
                owner_name = folder_owner_name

            if progress_callback:
                progress_callback(
                    f"正在读取收藏夹 {index}/{len(folders)}：{folder.title}，发现 {len(medias)} 条视频记录"
                )

            for media in medias:
                bvid = media.get("bvid") or media.get("bv_id")
                if not bvid:
                    continue

                video = videos_by_bvid.get(bvid)
                if video is None:
                    video = VideoItem(
                        bvid=bvid,
                        title=media.get("title", "").strip() or bvid,
                        url=f"https://www.bilibili.com/video/{bvid}",
                        owner_name=media.get("upper", {}).get("name", ""),
                        owner_mid=int(media.get("upper", {}).get("mid", 0) or 0),
                        intro=(media.get("intro") or "").strip(),
                        aid=int(media.get("id", 0) or 0),
                        source_folders=[],
                    )
                    videos_by_bvid[bvid] = video

                if folder.title not in video.source_folders:
                    video.source_folders.append(folder.title)

                video.add_source_ref(
                    FavoriteSourceRef(
                        folder_id=folder.folder_id,
                        folder_title=folder.title,
                        owner_mid=folder.owner_mid,
                        resource_id=int(media.get("id", 0) or 0),
                        resource_type=int(media.get("type", 2) or 2),
                    )
                )

        videos = list(videos_by_bvid.values())
        if not videos:
            raise BilibiliApiError("公开收藏夹存在，但没有读取到可分类的视频。")

        if progress_callback:
            progress_callback(f"共整理出 {len(videos)} 个唯一视频，正在获取标签与分区信息...")

        self._populate_video_metadata(videos, progress_callback=progress_callback)
        return folders, sorted(videos, key=lambda item: item.title), owner_name

    def fetch_public_favorite_folders(self, user_mid: int) -> list[FavoriteFolder]:
        payload = self._get_json(self.FAVORITE_FOLDER_URL, {"up_mid": user_mid})
        data = payload.get("data") or {}
        folder_list = data.get("list") or []
        return [
            FavoriteFolder(
                folder_id=int(folder["id"]),
                fid=int(folder.get("fid", 0) or 0),
                owner_mid=int(folder.get("mid", 0) or 0),
                title=folder.get("title", "").strip() or f"收藏夹 {folder['id']}",
                media_count=int(folder.get("media_count", 0) or 0),
            )
            for folder in folder_list
        ]

    def fetch_folder_medias(
        self,
        folder_id: int,
        page_size: int = 20,
        owner_mid: int | None = None,
    ) -> tuple[list[dict], str]:
        page = 1
        medias: list[dict] = []
        owner_name = ""

        while True:
            payload = self._get_json(
                self.FAVORITE_RESOURCE_URL,
                {
                    "media_id": folder_id,
                    "pn": page,
                    "ps": page_size,
                    "keyword": "",
                    "order": "mtime",
                    "type": 0,
                    "tid": 0,
                    "platform": "web",
                },
                referer=f"https://space.bilibili.com/{owner_mid}/favlist" if owner_mid else "https://space.bilibili.com/",
            )
            data = payload.get("data") or {}
            info = data.get("info") or {}
            upper = info.get("upper") or {}
            owner_name = owner_name or upper.get("name", "")
            page_medias = data.get("medias") or []
            medias.extend(page_medias)

            if not data.get("has_more") or not page_medias:
                break
            page += 1

        return medias, owner_name

    def fetch_video_tags(self, bvid: str) -> list[str]:
        payload = self._get_json(self.VIDEO_TAGS_URL, {"bvid": bvid})
        tags = payload.get("data") or []
        names = []
        for item in tags:
            tag_name = (item.get("tag_name") or "").strip()
            if tag_name and tag_name not in names:
                names.append(tag_name)
        return names

    def fetch_video_view(self, bvid: str) -> dict:
        payload = self._get_json(self.VIDEO_VIEW_URL, {"bvid": bvid})
        return payload.get("data") or {}

    def create_favorite_folder(
        self,
        title: str,
        privacy: int = 1,
        intro: str = "由 Bili Favorites Classifier 自动创建",
    ) -> FavoriteFolder:
        csrf = self._get_csrf_token()
        auth_info = self.fetch_authenticated_user()
        payload = self._post_form(
            self.FAVORITE_FOLDER_ADD_URL,
            {
                "title": title,
                "intro": intro,
                "privacy": privacy,
                "cover": "",
                "csrf": csrf,
            },
            referer="https://space.bilibili.com/",
            require_auth=True,
        )
        data = payload.get("data") or {}
        folder_id = int(data.get("id", data.get("media_id", 0)) or 0)
        if not folder_id:
            raise BilibiliApiError(f"创建收藏夹“{title}”失败，接口未返回收藏夹 ID。")
        return FavoriteFolder(
            folder_id=folder_id,
            fid=int(data.get("fid", 0) or 0),
            owner_mid=auth_info.mid,
            title=title,
            media_count=0,
        )

    def sync_classification_result(
        self,
        result: ClassificationResult,
        target_user_mid: int,
        include_unclassified: bool = False,
        sync_mode: str = "copy",
        privacy: int = 1,
        progress_callback: ProgressCallback | None = None,
    ) -> SyncSummary:
        if sync_mode not in {"copy", "move"}:
            raise BilibiliApiError("同步模式仅支持 copy 或 move。")

        auth_info = self.fetch_authenticated_user()
        if auth_info.mid != target_user_mid:
            raise BilibiliApiError(
                f"当前登录账号 MID={auth_info.mid}，与已分类 UID={target_user_mid} 不一致。"
                "当前版本仅支持同步到与已分类 UID 相同的 B 站账号。"
            )

        if sync_mode == "move":
            conflicts = self._collect_multi_group_videos(result, include_unclassified=include_unclassified)
            if conflicts:
                raise BilibiliApiError(
                    "当前分类结果中有视频同时属于多个分类，无法安全执行“移动”。"
                    f"请改用“复制”，或先在界面中调整分类。冲突示例：{conflicts[0]}"
                )

        existing_folders = self.fetch_public_favorite_folders(target_user_mid)
        folder_map = {folder.title: folder for folder in existing_folders}
        summary = SyncSummary(mode=sync_mode, target_mid=target_user_mid)

        target_groups = [
            group for group in result.groups if include_unclassified or group.name != "未分类"
        ]
        if not target_groups:
            raise BilibiliApiError("当前没有可同步的分类组。")

        if progress_callback:
            progress_callback("正在检查和创建目标收藏夹...")

        for group in target_groups:
            if group.name in folder_map:
                summary.reused_folders.append(group.name)
                continue
            folder = self.create_favorite_folder(group.name, privacy=privacy)
            folder_map[group.name] = folder
            summary.created_folders.append(group.name)
            if progress_callback:
                progress_callback(f"已创建收藏夹：{group.name}")

        existing_target_resource_ids = {
            folder.folder_id: self._fetch_folder_resource_ids(folder)
            for folder in folder_map.values()
        }
        action_map: dict[tuple[int, int], list[str]] = defaultdict(list)

        for group in target_groups:
            target_folder = folder_map[group.name]
            target_resources = existing_target_resource_ids.get(target_folder.folder_id, set())
            for video in group.videos:
                source_ref = video.get_primary_source_ref()
                if source_ref is None:
                    summary.skipped_videos.append(f"{video.title}：缺少来源收藏夹信息")
                    continue
                if sync_mode == "move" and source_ref.owner_mid != auth_info.mid:
                    summary.skipped_videos.append(f"{video.title}：来源收藏夹不属于当前登录用户，无法移动")
                    continue
                resource_id = int(video.aid or source_ref.resource_id)
                if resource_id in target_resources:
                    summary.skipped_videos.append(f"{video.title}：目标收藏夹已存在")
                    continue
                if source_ref.folder_id == target_folder.folder_id:
                    summary.skipped_videos.append(f"{video.title}：来源收藏夹与目标收藏夹相同")
                    continue
                resource_item = f"{resource_id}:{source_ref.resource_type}"
                action_map[(source_ref.folder_id, target_folder.folder_id)].append(resource_item)
                target_resources.add(resource_id)

        if progress_callback:
            progress_callback("正在将分类结果同步到 B 站收藏夹...")

        for (source_folder_id, target_folder_id), resources in action_map.items():
            for chunk in self._chunk_resources(resources, size=20):
                if sync_mode == "copy":
                    self._copy_resources(
                        source_folder_id=source_folder_id,
                        target_folder_id=target_folder_id,
                        mid=auth_info.mid,
                        resources=chunk,
                    )
                    summary.copied_count += len(chunk)
                else:
                    self._move_resources(
                        source_folder_id=source_folder_id,
                        target_folder_id=target_folder_id,
                        resources=chunk,
                    )
                    summary.moved_count += len(chunk)

        return summary

    def _fetch_folder_resource_ids(self, folder: FavoriteFolder) -> set[int]:
        medias, _ = self.fetch_folder_medias(folder.folder_id, owner_mid=folder.owner_mid)
        return {int(media.get("id", 0) or 0) for media in medias}

    def _copy_resources(
        self,
        source_folder_id: int,
        target_folder_id: int,
        mid: int,
        resources: list[str],
    ) -> None:
        csrf = self._get_csrf_token()
        self._post_form(
            self.FAVORITE_RESOURCE_COPY_URL,
            {
                "src_media_id": source_folder_id,
                "tar_media_id": target_folder_id,
                "mid": mid,
                "resources": ",".join(resources),
                "platform": "web",
                "csrf": csrf,
            },
            referer="https://space.bilibili.com/",
            require_auth=True,
        )

    def _move_resources(
        self,
        source_folder_id: int,
        target_folder_id: int,
        resources: list[str],
    ) -> None:
        csrf = self._get_csrf_token()
        self._post_form(
            self.FAVORITE_RESOURCE_MOVE_URL,
            {
                "src_media_id": source_folder_id,
                "tar_media_id": target_folder_id,
                "resources": ",".join(resources),
                "platform": "web",
                "csrf": csrf,
            },
            referer="https://space.bilibili.com/",
            require_auth=True,
        )

    def _collect_multi_group_videos(
        self,
        result: ClassificationResult,
        include_unclassified: bool,
    ) -> list[str]:
        ownership: dict[str, list[str]] = defaultdict(list)
        for group in result.groups:
            if not include_unclassified and group.name == "未分类":
                continue
            for video in group.videos:
                ownership[video.bvid].append(group.name)
        conflicts = []
        for bvid, group_names in ownership.items():
            if len(group_names) > 1:
                conflicts.append(f"{bvid} -> {', '.join(group_names)}")
        return conflicts

    def _fetch_video_metadata(self, bvid: str) -> dict[str, object]:
        tags = self.fetch_video_tags(bvid)
        view = self.fetch_video_view(bvid)
        partition_name, partition_id, partition_parent_name, partition_parent_id = resolve_partition_info(
            int(view.get("tid", 0) or 0),
            int(view.get("tid_v2", 0) or 0),
            str(view.get("tname", "") or ""),
            str(view.get("tname_v2", "") or ""),
        )
        return {
            "tags": tags,
            "intro": str(view.get("desc", "") or ""),
            "aid": int(view.get("aid", 0) or 0),
            "partition_name": partition_name,
            "partition_id": partition_id,
            "partition_parent_name": partition_parent_name,
            "partition_parent_id": partition_parent_id,
        }

    def _populate_video_metadata(
        self,
        videos: list[VideoItem],
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        completed = 0
        total = len(videos)

        with ThreadPoolExecutor(max_workers=self.tag_workers) as executor:
            future_map = {executor.submit(self._fetch_video_metadata, video.bvid): video for video in videos}
            for future in as_completed(future_map):
                video = future_map[future]
                try:
                    metadata = future.result()
                    video.tags = list(metadata["tags"])
                    video.aid = int(metadata["aid"] or video.aid)
                    if metadata["intro"] and not video.intro:
                        video.intro = str(metadata["intro"])
                    video.partition_name = str(metadata["partition_name"])
                    video.partition_id = int(metadata["partition_id"])
                    video.partition_parent_name = str(metadata["partition_parent_name"])
                    video.partition_parent_id = int(metadata["partition_parent_id"])
                except Exception:
                    video.tags = []
                    if not video.partition_name:
                        video.partition_name = "未知分类"
                completed += 1
                if progress_callback:
                    progress_callback(f"正在获取视频标签与分区 {completed}/{total}：{video.title}")

    def _get_csrf_token(self) -> str:
        csrf = self._extract_cookie_value("bili_jct")
        if not csrf:
            raise BilibiliApiError("当前 Cookie 缺少 bili_jct，无法进行收藏夹写操作。")
        return csrf

    def _extract_cookie_value(self, cookie_name: str) -> str:
        if not self.auth_cookie:
            return ""
        simple_cookie = SimpleCookie()
        try:
            simple_cookie.load(self.auth_cookie)
        except Exception:
            return ""
        morsel = simple_cookie.get(cookie_name)
        return morsel.value if morsel else ""

    def _get_json(
        self,
        base_url: str,
        params: dict[str, object] | None = None,
        referer: str | None = None,
        require_auth: bool = False,
    ) -> dict:
        return self._request_json(
            "GET",
            base_url,
            params=params,
            referer=referer,
            require_auth=require_auth,
            allowed_codes={0},
        )

    def _post_form(
        self,
        base_url: str,
        form_data: dict[str, object],
        referer: str | None = None,
        require_auth: bool = True,
    ) -> dict:
        return self._request_json(
            "POST",
            base_url,
            form_data=form_data,
            referer=referer,
            require_auth=require_auth,
            allowed_codes={0},
        )

    def _request_json(
        self,
        method: str,
        base_url: str,
        params: dict[str, object] | None = None,
        form_data: dict[str, object] | None = None,
        referer: str | None = None,
        require_auth: bool = False,
        allowed_codes: set[int] | None = None,
    ) -> dict:
        if require_auth and not self.auth_cookie:
            raise BilibiliApiError("该操作需要登录 Cookie。请先在界面中粘贴完整 Cookie。")

        params = params or {}
        query = urllib.parse.urlencode(params)
        url = f"{base_url}?{query}" if query else base_url
        headers = self._build_headers(base_url, params, referer=referer, include_cookie=require_auth or bool(self.auth_cookie))
        body = None
        if form_data is not None:
            body = urllib.parse.urlencode(form_data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"

        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    payload = json.load(response)
                break
            except urllib.error.HTTPError as exc:
                if exc.code in {412, 429} and attempt < self.max_retries:
                    time.sleep(1.2 * (attempt + 1))
                    continue
                if exc.code == 412:
                    raise BilibiliApiError(
                        "Bilibili 接口触发了风控限制（HTTP 412）。这通常是接口临时反爬、访问过快，"
                        "或当前网络环境被限制导致。请稍后重试，必要时降低请求频率或更换网络环境。"
                    ) from exc
                raise BilibiliApiError(f"Bilibili HTTP 请求失败：{exc.code}") from exc
            except urllib.error.URLError as exc:
                if attempt < self.max_retries:
                    time.sleep(0.8 * (attempt + 1))
                    continue
                raise BilibiliApiError(f"网络请求失败：{exc.reason}") from exc

        allowed = allowed_codes or {0}
        code = payload.get("code", 0)
        if code not in allowed:
            raise BilibiliApiError(payload.get("message", f"Bilibili API request failed with code {code}."))

        if self.request_interval:
            time.sleep(self.request_interval)
        return payload

    def _build_headers(
        self,
        base_url: str,
        params: dict[str, object],
        referer: str | None = None,
        include_cookie: bool = False,
    ) -> dict[str, str]:
        resolved_referer = referer or "https://www.bilibili.com/"
        if referer is None and base_url in {self.VIDEO_TAGS_URL, self.VIDEO_VIEW_URL}:
            bvid = params.get("bvid", "")
            resolved_referer = f"https://www.bilibili.com/video/{bvid}"

        headers = {
            "User-Agent": self.USER_AGENT,
            "Referer": resolved_referer,
            "Origin": "https://www.bilibili.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if include_cookie and self.auth_cookie:
            headers["Cookie"] = self.auth_cookie
        return headers

    def _chunk_resources(self, resources: list[str], size: int) -> list[list[str]]:
        return [resources[index : index + size] for index in range(0, len(resources), size)]
