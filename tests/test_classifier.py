import unittest
from unittest import mock
import urllib.error

from bili_favorites_classifier.api import BilibiliApiClient, BilibiliApiError
from bili_favorites_classifier.classifier import classify_videos, move_video_to_group
from bili_favorites_classifier.models import ClassificationRule, VideoItem
from bili_favorites_classifier.partition_map import resolve_partition_info


def make_video(
    title: str,
    bvid: str,
    tags: list[str],
    partition_name: str = "知识",
) -> VideoItem:
    return VideoItem(
        bvid=bvid,
        title=title,
        url=f"https://www.bilibili.com/video/{bvid}",
        tags=tags,
        source_folders=["默认收藏夹"],
        partition_name=partition_name,
    )


class ClassifierTests(unittest.TestCase):
    def test_default_mode_groups_by_partition_name(self) -> None:
        videos = [
            make_video("Python 编程入门教程", "BV1test1111", ["教程", "编程"], partition_name="知识"),
            make_video("新手机开箱测评", "BV1test2222", ["数码", "开箱"], partition_name="数码"),
        ]

        result = classify_videos(videos, mode="default")
        grouped = {group.name: [video.bvid for video in group.videos] for group in result.groups}

        self.assertEqual(grouped["知识"], ["BV1test1111"])
        self.assertEqual(grouped["数码"], ["BV1test2222"])
        self.assertEqual(result.unclassified_count, 0)

    def test_custom_mode_supports_multi_match_and_unclassified(self) -> None:
        videos = [
            make_video("家常菜做法", "BV1test3333", ["美食", "做饭"], partition_name="美食"),
            make_video("效率软件分享", "BV1test4444", ["软件应用", "效率"], partition_name="数码"),
            make_video("冷门纪录片推荐", "BV1test5555", ["纪录片"], partition_name="纪录片"),
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
        self.assertEqual(set(grouped["混合兴趣"]), {"BV1test3333", "BV1test4444"})
        self.assertEqual(grouped["未分类"], ["BV1test5555"])
        self.assertEqual(result.unclassified_count, 1)

    def test_custom_mode_matches_similar_tags(self) -> None:
        videos = [make_video("AI 入门", "BV1test6666", ["人工智能"], partition_name="知识")]
        rules = [ClassificationRule(name="AI", keywords=["人工智能技术"])]

        result = classify_videos(videos, mode="custom", custom_rules=rules)
        grouped = {group.name: [video.bvid for video in group.videos] for group in result.groups}

        self.assertEqual(grouped["AI"], ["BV1test6666"])
        self.assertEqual(grouped["未分类"], [])

    def test_move_video_between_groups_updates_unclassified_count(self) -> None:
        video = make_video("纪录片推荐", "BV1test7777", ["纪录片"], partition_name="纪录片")
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


if __name__ == "__main__":
    unittest.main()
