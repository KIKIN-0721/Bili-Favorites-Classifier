from __future__ import annotations

from difflib import SequenceMatcher

from .models import ClassificationResult, ClassificationRule, ClassifiedGroup, VideoItem


SAMPLE_CUSTOM_RULES = [
    ClassificationRule("科技数码", ["数码", "软件应用", "计算机技术", "开箱", "科技"]),
    ClassificationRule("学习知识", ["教程", "知识", "科普", "历史", "校园学习"]),
    ClassificationRule("生活兴趣", ["日常", "生活", "VLOG", "手工", "绘画"]),
    ClassificationRule("时事观察", ["热点", "社会", "资讯", "财经", "商业"]),
]


def classify_videos(
    videos: list[VideoItem],
    mode: str = "default",
    custom_rules: list[ClassificationRule] | None = None,
) -> ClassificationResult:
    if mode == "custom":
        rules = custom_rules or []
        return _classify_with_custom_rules(videos, rules)
    return _classify_with_default_rules(videos)


def _classify_with_default_rules(videos: list[VideoItem]) -> ClassificationResult:
    groups: dict[str, list[VideoItem]] = {}
    for video in videos:
        group_name = video.partition_name or video.partition_parent_name or "未知分类"
        groups.setdefault(group_name, []).append(video)

    result_groups = [
        ClassifiedGroup(name=name, videos=sorted(items, key=lambda item: item.title), reason="按 Bilibili 视频分区直接分类")
        for name, items in groups.items()
    ]
    result_groups.sort(key=lambda group: (-len(group.videos), group.name))
    return ClassificationResult(
        mode="default",
        groups=result_groups,
        total_videos=len(videos),
        unclassified_count=_count_unclassified_videos(result_groups),
    )


def _classify_with_custom_rules(
    videos: list[VideoItem],
    rules: list[ClassificationRule],
) -> ClassificationResult:
    groups: dict[str, list[VideoItem]] = {rule.name: [] for rule in rules if rule.name.strip()}
    unmatched: list[VideoItem] = []

    for video in videos:
        matched_any = False
        for rule in rules:
            if not rule.name.strip():
                continue
            if _matching_rule_tags(video, rule):
                groups.setdefault(rule.name, []).append(video)
                matched_any = True
        if not matched_any:
            unmatched.append(video)

    result_groups = [
        ClassifiedGroup(name=name, videos=sorted(items, key=lambda item: item.title), reason="根据自定义 tag 规则匹配")
        for name, items in groups.items()
    ]

    result_groups.append(
        ClassifiedGroup(
            name="未分类",
            videos=sorted(unmatched, key=lambda item: item.title),
            reason="未匹配任何自定义 tag 关键词",
        )
    )

    result_groups = _ordered_groups(result_groups)
    return ClassificationResult(
        mode="custom",
        groups=result_groups,
        total_videos=len(videos),
        unclassified_count=len(unmatched),
    )


def move_video_to_group(
    result: ClassificationResult,
    video: VideoItem,
    source_group_name: str,
    target_group_name: str,
) -> bool:
    if source_group_name == target_group_name:
        return False

    source_group = _find_group(result.groups, source_group_name)
    target_group = _find_group(result.groups, target_group_name)
    if source_group is None:
        return False
    if target_group is None:
        target_group = ClassifiedGroup(name=target_group_name, videos=[], reason="手动调整分类")
        insert_index = len(result.groups)
        for index, group in enumerate(result.groups):
            if group.name == "未分类":
                insert_index = index
                break
        result.groups.insert(insert_index, target_group)

    original_count = len(source_group.videos)
    source_group.videos = [item for item in source_group.videos if item.bvid != video.bvid]
    if len(source_group.videos) == original_count:
        return False
    if all(item.bvid != video.bvid for item in target_group.videos):
        target_group.videos.append(video)
        target_group.videos.sort(key=lambda item: item.title)

    result.unclassified_count = _count_unclassified_videos(result.groups)
    return True


def _matching_rule_tags(video: VideoItem, rule: ClassificationRule) -> list[str]:
    matched_tags: list[str] = []
    normalized_keywords = rule.normalized_keywords()
    normalized_tags = [tag.strip() for tag in video.tags if tag.strip()]

    for tag in normalized_tags:
        normalized_tag = tag.lower()
        for keyword in normalized_keywords:
            if _tag_matches_keyword(normalized_tag, keyword):
                matched_tags.append(tag)
                break
    return matched_tags


def _tag_matches_keyword(normalized_tag: str, normalized_keyword: str) -> bool:
    if normalized_tag == normalized_keyword:
        return True
    if normalized_keyword in normalized_tag or normalized_tag in normalized_keyword:
        return True
    return SequenceMatcher(None, normalized_tag, normalized_keyword).ratio() >= 0.72


def _find_group(groups: list[ClassifiedGroup], group_name: str) -> ClassifiedGroup | None:
    for group in groups:
        if group.name == group_name:
            return group
    return None


def _ordered_groups(groups: list[ClassifiedGroup]) -> list[ClassifiedGroup]:
    regular_groups = [group for group in groups if group.name != "未分类"]
    unclassified_groups = [group for group in groups if group.name == "未分类"]
    regular_groups.sort(key=lambda group: (-len(group.videos), group.name))
    return regular_groups + unclassified_groups


def _count_unclassified_videos(groups: list[ClassifiedGroup]) -> int:
    return len({video.bvid for group in groups if group.name == "未分类" for video in group.videos})
