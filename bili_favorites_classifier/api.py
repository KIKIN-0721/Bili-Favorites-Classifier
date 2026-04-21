from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from .models import FavoriteFolder, VideoItem
from .partition_map import resolve_partition_info


ProgressCallback = Callable[[str], None]


class BilibiliApiError(RuntimeError):
    """Raised when a public Bilibili API call fails."""


class BilibiliApiClient:
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )
    FAVORITE_FOLDER_URL = "https://api.bilibili.com/x/v3/fav/folder/created/list-all"
    FAVORITE_RESOURCE_URL = "https://api.bilibili.com/x/v3/fav/resource/list"
    VIDEO_TAGS_URL = "https://api.bilibili.com/x/web-interface/view/detail/tag"
    VIDEO_VIEW_URL = "https://api.bilibili.com/x/web-interface/view"

    def __init__(
        self,
        request_interval: float = 0.05,
        tag_workers: int = 8,
        max_retries: int = 3,
    ) -> None:
        self.request_interval = request_interval
        self.tag_workers = tag_workers
        self.max_retries = max_retries

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
                        source_folders=[],
                    )
                    videos_by_bvid[bvid] = video

                if folder.title not in video.source_folders:
                    video.source_folders.append(folder.title)

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
        folders = [
            FavoriteFolder(
                folder_id=int(folder["id"]),
                fid=int(folder.get("fid", 0) or 0),
                owner_mid=int(folder.get("mid", 0) or 0),
                title=folder.get("title", "").strip() or f"收藏夹 {folder['id']}",
                media_count=int(folder.get("media_count", 0) or 0),
            )
            for folder in folder_list
        ]
        return folders

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

    def _get_json(
        self,
        base_url: str,
        params: dict[str, object],
        referer: str | None = None,
    ) -> dict:
        query = urllib.parse.urlencode(params)
        url = f"{base_url}?{query}"
        headers = self._build_headers(base_url, params, referer=referer)

        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(url, headers=headers)
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

        code = payload.get("code", 0)
        if code != 0:
            raise BilibiliApiError(payload.get("message", "Bilibili API request failed."))

        if self.request_interval:
            time.sleep(self.request_interval)
        return payload

    def _build_headers(
        self,
        base_url: str,
        params: dict[str, object],
        referer: str | None = None,
    ) -> dict[str, str]:
        resolved_referer = referer or "https://www.bilibili.com/"
        if referer is None and base_url in {self.VIDEO_TAGS_URL, self.VIDEO_VIEW_URL}:
            bvid = params.get("bvid", "")
            resolved_referer = f"https://www.bilibili.com/video/{bvid}"

        return {
            "User-Agent": self.USER_AGENT,
            "Referer": resolved_referer,
            "Origin": "https://www.bilibili.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
