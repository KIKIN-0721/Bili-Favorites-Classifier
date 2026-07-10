import unittest
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from bili_favorites_classifier.api import BilibiliApiClient, BilibiliApiError
from bili_favorites_classifier.classifier import classify_videos, move_video_to_group
from bili_favorites_classifier.models import (
    AuthInfo,
    ClassifiedGroup,
    ClassificationResult,
    ClassificationRule,
    FavoriteFolder,
    FavoriteSourceRef,
    VideoItem,
)
from bili_favorites_classifier.partition_map import resolve_partition_info
from bili_favorites_classifier.settings import load_custom_rules, save_custom_rules


def make_video(
    title: str,
    bvid: str,
    tags: list[str],
    partition_name: str = "知识",
    aid: int = 10001,
    source_folder_id: int = 20001,
) -> VideoItem:
    video = VideoItem(
        bvid=bvid,
        title=title,
        url=f"https://www.bilibili.com/video/{bvid}",
        tags=tags,
        source_folders=["默认收藏夹"],
        partition_name=partition_name,
        aid=aid,
    )
    video.add_source_ref(
        FavoriteSourceRef(
            folder_id=source_folder_id,
            folder_title="默认收藏夹",
            owner_mid=123456,
            resource_id=aid,
            resource_type=2,
        )
    )
    return video


class ClassifierTests(unittest.TestCase):
    def test_default_mode_groups_by_partition_name(self) -> None:
        videos = [
            make_video("Python 编程入门教程", "BV1test1111", ["教程", "编程"], partition_name="知识", aid=101),
            make_video("新手机开箱测评", "BV1test2222", ["数码", "开箱"], partition_name="数码", aid=102),
        ]

        result = classify_videos(videos, mode="default")
        grouped = {group.name: [video.bvid for video in group.videos] for group in result.groups}

        self.assertEqual(grouped["知识"], ["BV1test1111"])
        self.assertEqual(grouped["数码"], ["BV1test2222"])
        self.assertEqual(result.unclassified_count, 0)

    def test_custom_mode_uses_first_matching_rule_and_unclassified(self) -> None:
        videos = [
            make_video("家常菜做法", "BV1test3333", ["美食", "做饭"], partition_name="美食", aid=201),
            make_video("效率软件分享", "BV1test4444", ["软件应用", "效率"], partition_name="数码", aid=202),
            make_video("冷门纪录片推荐", "BV1test5555", ["纪录片"], partition_name="纪录片", aid=203),
        ]
        rules = [
            ClassificationRule(name="吃饭区", keywords=["美食", "做饭"]),
            ClassificationRule(name="效率工具", keywords=["软件应用", "效率"]),
            ClassificationRule(name="混合兴趣", keywords=["美食", "效率"]),
        ]

        result = classify_videos(videos, mode="custom", custom_rules=rules)
        grouped = {group.name: [video.bvid for video in group.videos] for group in result.groups}

        self.assertEqual(grouped["吃饭区"], ["BV1test3333"])
        self.assertEqual(grouped["效率工具"], ["BV1test4444"])
        self.assertEqual(grouped["混合兴趣"], [])
        self.assertEqual(grouped["未分类"], ["BV1test5555"])
        self.assertEqual(result.unclassified_count, 1)

    def test_custom_mode_preserves_rule_order_in_result_groups(self) -> None:
        videos = [make_video("效率软件分享", "BV1test4444", ["软件应用", "效率"], partition_name="数码", aid=202)]
        rules = [
            ClassificationRule(name="后备分类", keywords=["不存在"]),
            ClassificationRule(name="效率工具", keywords=["效率"]),
            ClassificationRule(name="混合兴趣", keywords=["软件应用"]),
        ]

        result = classify_videos(videos, mode="custom", custom_rules=rules)

        self.assertEqual([group.name for group in result.groups], ["后备分类", "效率工具", "混合兴趣", "未分类"])
        self.assertEqual([video.bvid for video in result.groups[1].videos], ["BV1test4444"])
        self.assertEqual(result.groups[2].videos, [])

    def test_custom_mode_matches_similar_tags(self) -> None:
        videos = [make_video("AI 入门", "BV1test6666", ["人工智能"], partition_name="知识", aid=301)]
        rules = [ClassificationRule(name="AI", keywords=["人工智能技术"])]

        result = classify_videos(videos, mode="custom", custom_rules=rules)
        grouped = {group.name: [video.bvid for video in group.videos] for group in result.groups}

        self.assertEqual(grouped["AI"], ["BV1test6666"])
        self.assertEqual(grouped["未分类"], [])

    def test_move_video_between_groups_updates_unclassified_count(self) -> None:
        video = make_video("纪录片推荐", "BV1test7777", ["纪录片"], partition_name="纪录片", aid=401)
        rules = [ClassificationRule(name="历史", keywords=["历史"])]
        result = classify_videos([video], mode="custom", custom_rules=rules)

        moved = move_video_to_group(result, video, "未分类", "历史")

        grouped = {group.name: [item.bvid for item in group.videos] for group in result.groups}
        self.assertTrue(moved)
        self.assertEqual(grouped["历史"], ["BV1test7777"])
        self.assertEqual(grouped["未分类"], [])
        self.assertEqual(result.unclassified_count, 0)

    def test_partition_map_prefers_known_v2_mapping_and_unknown_falls_back_cleanly(self) -> None:
        known = resolve_partition_info(0, 2005)
        unknown = resolve_partition_info(0, 999999)

        self.assertEqual(known[0], "短剧短片")
        self.assertEqual(known[2], "影视")
        self.assertEqual(unknown[0], "未知分类")
        self.assertEqual(unknown[2], "未知分类")

    def test_api_client_converts_http_412_into_readable_error(self) -> None:
        client = BilibiliApiClient(max_retries=0)
        http_error = urllib.error.HTTPError(
            url="https://api.bilibili.com/x/v3/fav/resource/list",
            code=412,
            msg="Precondition Failed",
            hdrs=None,
            fp=None,
        )

        with mock.patch("urllib.request.urlopen", side_effect=http_error):
            with self.assertRaises(BilibiliApiError) as context:
                client.fetch_folder_medias(123456)

        self.assertIn("HTTP 412", str(context.exception))

    def test_api_client_extracts_csrf_from_cookie(self) -> None:
        client = BilibiliApiClient()
        client.set_auth_cookie("SESSDATA=test_session; bili_jct=test_csrf; DedeUserID=123456")

        self.assertEqual(client._get_csrf_token(), "test_csrf")

    def test_api_client_clamps_rate_limit_settings(self) -> None:
        client = BilibiliApiClient(request_interval=0.2, tag_workers=4)
        client.configure_rate_limit(request_interval=-1, tag_workers=99)

        self.assertEqual(client.request_interval, 0.0)
        self.assertEqual(client.tag_workers, 16)

    def test_api_client_fetches_only_requested_metadata_mode(self) -> None:
        client = BilibiliApiClient(metadata_cache_enabled=False)

        with (
            mock.patch.object(client, "fetch_video_tags", return_value=["效率"]),
            mock.patch.object(client, "fetch_video_view") as fetch_video_view,
        ):
            metadata = client._fetch_video_metadata("BV1mode0001", metadata_mode="tags")

        self.assertEqual(metadata["tags"], ["效率"])
        fetch_video_view.assert_not_called()

    def test_api_client_uses_metadata_cache_before_network(self) -> None:
        client = BilibiliApiClient()
        video = make_video("缓存视频", "BV1cache001", [], partition_name="")
        cached = {"BV1cache001": {"tags": ["缓存标签"], "partition_name": "知识", "partition_id": 36}}

        with (
            mock.patch("bili_favorites_classifier.api.load_video_metadata_cache", return_value=cached),
            mock.patch.object(client, "_fetch_video_metadata") as fetch_metadata,
        ):
            client._populate_video_metadata([video], metadata_mode="tags")

        fetch_metadata.assert_not_called()
        self.assertEqual(video.tags, ["缓存标签"])

    def test_api_client_fetches_videos_from_selected_folders(self) -> None:
        client = BilibiliApiClient(metadata_cache_enabled=False)
        folder = FavoriteFolder(folder_id=1001, fid=0, owner_mid=123456, title="精选收藏夹", media_count=1)
        media = {
            "bvid": "BV1selected1",
            "title": "精选视频",
            "id": 2001,
            "type": 2,
            "upper": {"name": "upper", "mid": 3001},
        }

        with (
            mock.patch.object(client, "fetch_public_favorite_folders") as fetch_folders,
            mock.patch.object(client, "fetch_folder_medias", return_value=([media], "owner")),
            mock.patch.object(client, "_populate_video_metadata"),
        ):
            folders, videos, owner_name = client.fetch_folders_videos([folder], metadata_mode="tags")

        fetch_folders.assert_not_called()
        self.assertEqual(folders, [folder])
        self.assertEqual(owner_name, "owner")
        self.assertEqual([video.bvid for video in videos], ["BV1selected1"])
        self.assertEqual(videos[0].source_folders, ["精选收藏夹"])

    def test_custom_rules_can_roundtrip_to_json(self) -> None:
        rules = [ClassificationRule(name="效率工具", keywords=["软件应用", "效率"])]

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "rules.json"
            saved_path = save_custom_rules(rules, path)
            loaded_rules = load_custom_rules(saved_path)

        self.assertEqual(saved_path, path)
        self.assertEqual(loaded_rules, rules)

    def test_sync_copy_creates_missing_folder_and_batches_resources(self) -> None:
        client = BilibiliApiClient()
        video = make_video("软件技巧", "BV1sync0001", ["软件应用"], partition_name="数码", aid=501, source_folder_id=601)
        result = classify_videos(
            [video],
            mode="custom",
            custom_rules=[ClassificationRule(name="效率工具", keywords=["软件应用"])],
        )

        created_folder = FavoriteFolder(folder_id=701, fid=0, owner_mid=123456, title="效率工具", media_count=0)
        copied_calls: list[tuple[int, int, int, list[str]]] = []

        with (
            mock.patch.object(client, "fetch_authenticated_user", return_value=AuthInfo(mid=123456, uname="tester", is_login=True)),
            mock.patch.object(client, "fetch_public_favorite_folders", return_value=[]),
            mock.patch.object(client, "create_favorite_folder", return_value=created_folder),
            mock.patch.object(client, "_fetch_folder_resource_ids", return_value=set()),
            mock.patch.object(
                client,
                "_copy_resources",
                side_effect=lambda source_folder_id, target_folder_id, mid, resources: copied_calls.append(
                    (source_folder_id, target_folder_id, mid, resources)
                ),
            ),
        ):
            summary = client.sync_classification_result(result, target_user_mid=123456, sync_mode="copy")

        self.assertEqual(summary.created_folders, ["效率工具"])
        self.assertEqual(summary.copied_count, 1)
        self.assertEqual(copied_calls, [(601, 701, 123456, ["501:2"])])

    def test_sync_move_rejects_multi_group_conflict(self) -> None:
        client = BilibiliApiClient()
        video = make_video("冲突视频", "BV1sync0002", ["效率", "美食"], partition_name="生活", aid=801, source_folder_id=901)
        result = ClassificationResult(
            mode="custom",
            groups=[
                ClassifiedGroup(name="效率工具", videos=[video]),
                ClassifiedGroup(name="生活兴趣", videos=[video]),
            ],
            total_videos=1,
            unclassified_count=0,
        )

        with mock.patch.object(client, "fetch_authenticated_user", return_value=AuthInfo(mid=123456, uname="tester", is_login=True)):
            with self.assertRaises(BilibiliApiError) as context:
                client.sync_classification_result(result, target_user_mid=123456, sync_mode="move")

        self.assertIn("无法安全执行“移动”", str(context.exception))


if __name__ == "__main__":
    unittest.main()
