from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from . import __version__
from .models import ClassificationResult, FavoriteFolder


def save_classification_result(
    destination: str,
    user_mid: int,
    owner_name: str,
    folders: list[FavoriteFolder],
    result: ClassificationResult,
) -> Path:
    path = Path(destination)
    if path.suffix.lower() == ".csv":
        _save_csv(path, result)
    else:
        _save_json(path, user_mid, owner_name, folders, result)
    return path


def _save_json(
    path: Path,
    user_mid: int,
    owner_name: str,
    folders: list[FavoriteFolder],
    result: ClassificationResult,
) -> None:
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "app_version": __version__,
        "user_mid": user_mid,
        "owner_name": owner_name,
        "favorite_folders": [
            {
                "folder_id": folder.folder_id,
                "fid": folder.fid,
                "title": folder.title,
                "media_count": folder.media_count,
            }
            for folder in folders
        ],
        "classification": result.to_dict(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _save_csv(path: Path, result: ClassificationResult) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.writer(file_obj)
        writer.writerow(["版本", __version__])
        writer.writerow(["分类", "标题", "BV号", "视频分区", "视频链接", "标签", "来源收藏夹"])
        for group in result.groups:
            for video in group.videos:
                writer.writerow(
                    [
                        group.name,
                        video.title,
                        video.bvid,
                        video.partition_name,
                        video.url,
                        " / ".join(video.tags),
                        " / ".join(video.source_folders),
                    ]
                )
