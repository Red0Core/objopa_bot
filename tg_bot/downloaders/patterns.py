"""URL matchers kept separate so heavy downloader stacks stay lazy-imported."""

import re

INSTAGRAM_REGEX = re.compile(
    r"(https?:\/\/(?:www\.)?instagram\.com\/(?:(?:p|reel|tv)\/[\w\-]+|share\/(?:p\/|reel\/|tv\/)?[\w\-]+|stories\/(?:highlights\/\d+|[\w.\-]+(?:\/\d+)?)))"
)

TWITTER_REGEX = re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/[^/]+/status/(?P<id>\d+)")
