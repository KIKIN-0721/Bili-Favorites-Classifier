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
class VideoItem:
    bvid: str
    title: str
    url: str
    owner_name: str = ""
    owner_mid: int = 0
    tags: list[str] = field(default_factory=list)
    source_folders: list[str] = field(default_factory=list)
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
            "tags": self.tags,
            "source_folders": self.source_folders,
            "intro": self.intro,
            "partition_name": self.partition_name,
            "partition_id": self.partition_id,
            "partition_parent_name": self.partition_parent_name,
            "partition_parent_id": self.partition_parent_id,
        }


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
