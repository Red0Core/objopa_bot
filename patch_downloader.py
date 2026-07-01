with open("tg_bot/downloaders/ig_reel_downloader.py", "r") as f:
    text = f.read()

# Replace extraction variables logic
text = text.replace('video_url, caption = _extract_from_html(resp.text)', 'video_urls, caption = _extract_from_html(resp.text)')
text = text.replace('embed_video_url, embed_caption = _extract_from_html(resp_embed.text)\n                if embed_video_url:\n                    video_url = embed_video_url', 'embed_video_urls, embed_caption = _extract_from_html(resp_embed.text)\n                if embed_video_urls:\n                    video_urls = embed_video_urls')
text = text.replace('if not video_url:', 'if not video_urls:')
text = text.replace('video_url = None', 'video_urls = []')

old_dl = '''    if not video_urls:
        return DownloadResult(
            success=False,
            files=[],
            error="Failed to extract video URL",
            downloader_used=DownloaderType.CUSTOM,
        )

    # Implement Download
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    video_path = DOWNLOADS_DIR / f"{shortcode}.mp4"

    logger.info(f"Downloading video to {video_path}")
    download_success = await _download_file(video_url, video_path)

    if not download_success:
        return DownloadResult(
            success=False,
            files=[],
            error="Failed to download video file from extracted URL",
            downloader_used=DownloaderType.CUSTOM,
        )

    files_to_return = [video_path]'''

new_dl = '''    if not video_urls:
        return DownloadResult(
            success=False,
            files=[],
            error="Failed to extract video URL",
            downloader_used=DownloaderType.CUSTOM,
        )

    # Implement Download with size limits (max 50 MB)
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    files_to_return = []

    session = Session(preset="chrome-149")
    try:
        # Download all unique extracted videos (useful for carousels)
        for i, vid_url in enumerate(video_urls):
            video_path = DOWNLOADS_DIR / f"{shortcode}_{i}.mp4" if len(video_urls) > 1 else DOWNLOADS_DIR / f"{shortcode}.mp4"

            # Check size via HEAD
            try:
                head_resp = await session.head_async(vid_url)
                size = int(head_resp.headers.get("content-length", 0))
                # 50 MB in bytes limit
                if size > 50 * 1024 * 1024:
                    logger.info(f"Video size {size} exceeds 50MB limit, trying next quality or skipping this video.")
                    continue
            except Exception as e:
                logger.warning(f"Failed to get video size via HEAD: {e}")

            logger.info(f"Downloading video to {video_path}")
            if await _download_file(vid_url, video_path):
                files_to_return.append(video_path)

    finally:
        session.close()

    if not files_to_return:
        return DownloadResult(
            success=False,
            files=[],
            error="Failed to download video files or all files exceeded 50MB limit",
            downloader_used=DownloaderType.CUSTOM,
        )'''

text = text.replace(old_dl, new_dl)

with open("tg_bot/downloaders/ig_reel_downloader.py", "w") as f:
    f.write(text)
