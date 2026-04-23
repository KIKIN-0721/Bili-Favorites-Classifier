from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class FavoriteFolder:
    folder_id: int
    fid: int
    owner_mid: int
    title: str
    media_count: int = 0


@dataclass(slots=True)
class FavoriteSourceRef:
    folder_id: int
    folder_title: str
    owner_mid: int
    resource_id: int
    resource_type: int = 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "folder_id": self.folder_id,
            "folder_title": self.folder_title,
            "owner_mid": self.owner_mid,
            "resource_id": self.resource_id,
            "resource_type": self.resource_type,
        }


@dataclass(slots=True)
class VideoItem:
    bvid: str
    title: str
    url: str
    owner_name: str = ""
    owner_mid: int = 0
    aid: int = 0
    tags: list[str] = field(default_factory=list)
    source_folders: list[str] = field(default_factory=list)
    source_refs: list[FavoriteSourceRef] = field(default_factory=list)
    intro: str = ""
    partition_name: str = ""
    partition_id: int = 0
    partition_parent_name: str = ""
    partition_parent_id: int = 0

    def search_blob(self) -> str:
        parts = [
            self.title,
            self.intro,
            self.partition_name,
            self.partition_parent_name,
            " ".join(self.tags),
            " ".join(self.source_folders),
        ]
        return " ".join(part.strip().lower() for part in parts if part)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bvid": self.bvid,
            "title": self.title,
            "url": self.url,
            "owner_name": self.owner_name,
            "owner_mid": self.owner_mid,
            "aid": self.aid,
            "tags": self.tags,
            "source_folders": self.source_folders,
            "source_refs": [source_ref.to_dict() for source_ref in self.source_refs],
            "intro": self.intro,
            "partition_name": self.partition_name,
            "partition_id": self.partition_id,
            "partition_parent_name": self.partition_parent_name,
            "partition_parent_id": self.partition_parent_id,
        }

    def add_source_ref(self, source_ref: FavoriteSourceRef) -> None:
        exists = any(
            current.folder_id == source_ref.folder_id and current.resource_id == source_ref.resource_id
            for current in self.source_refs
        )
        if not exists:
            self.source_refs.append(source_ref)

    def get_primary_source_ref(self) -> FavoriteSourceRef | None:
        return self.source_refs[0] if self.source_refs else None


@dataclass(slots=True)
class ClassificationRule:
    name: str
    keywords: list[str]

    def normalized_keywords(self) -> list[str]:
        return [keyword.strip().lower() for keyword in self.keywords if keyword.strip()]


@dataclass(slots=True)
class ClassifiedGroup:
    name: str
    videos: list[VideoItem]
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "reason": self.reason,
            "count": len(self.videos),
            "videos": [video.to_dict() for video in self.videos],
        }


@dataclass(slots=True)
class ClassificationResult:
    mode: str
    groups: list[ClassifiedGroup]
    total_videos: int
    unclassified_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "total_videos": self.total_videos,
            "unclassified_count": self.unclassified_count,
            "groups": [group.to_dict() for group in self.groups],
        }


@dataclass(slots=True)
class AuthInfo:
    mid: int
    uname: str
    is_login: bool


@dataclass(slots=True)
class SyncSummary:
    mode: str
    target_mid: int
    created_folders: list[str] = field(default_factory=list)
    reused_folders: list[str] = field(default_factory=list)
    skipped_videos: list[str] = field(default_factory=list)
    copied_count: int = 0
    moved_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "target_mid": self.target_mid,
            "created_folders": self.created_folders,
            "reused_folders": self.reused_folders,
            "skipped_videos": self.skipped_videos,
            "copied_count": self.copied_count,
            "moved_count": self.moved_count,
        }
