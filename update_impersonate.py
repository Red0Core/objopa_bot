with open("tg_bot/downloaders/ig_reel_downloader.py", "r") as f:
    text = f.read()

text = text.replace('impersonate="chrome110"', 'impersonate="chrome124"')

with open("tg_bot/downloaders/ig_reel_downloader.py", "w") as f:
    f.write(text)
