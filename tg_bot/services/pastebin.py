import httpx

from core.config import PASTEBIN_API_KEY

PASTEBIN_API_POST_URL = "https://pastebin.com/api/api_post.php"

async def upload_to_pastebin(text: str, title: str = "GPT Response") -> str:
    type = "text"
    if text.startswith("```") and text.endswith("```"):
        type = text[3:text.find("\n")].strip().lower()
        text = text[text.find("\n") + 1 : -3].strip()
    payload = {
        "api_dev_key": PASTEBIN_API_KEY,
        "api_option": "paste",
        "api_paste_name": title,
        "api_paste_format": type,
        "api_paste_private": "1",  # 0 = public, 1 = unlisted, 2 = private
        "api_paste_expire_date": "1W",
        "api_paste_code": text
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(PASTEBIN_API_POST_URL, data=payload)
        resp.raise_for_status()

        if resp.text.startswith("Bad API request"):
            raise Exception(f"Pastebin error: {resp.text}")

        return resp.text  # Returns a URL like https://pastebin.com/abc123

if __name__ == '__main__':
    import asyncio
    from core.config import PASTEBIN_API_KEY

    async def main():
        text = "```python\nprint('Hello, World!')\n```"
        paste_url = await upload_to_pastebin(text)
        print(f"Uploaded to: {paste_url}")

    asyncio.run(main())