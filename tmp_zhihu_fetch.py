from playwright.sync_api import sync_playwright
import json, re

URLS = [
    'https://zhuanlan.zhihu.com/p/2023805318547665572',
    'https://zhuanlan.zhihu.com/p/2023802595282534788',
]

def clean_html(s):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', s or '')).strip()

out=[]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    for url in URLS:
        page=browser.new_page(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36')
        page.goto(url, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_timeout(3000)
        title = None
        body = None
        source = None
        # js-initialData
        try:
            data = page.locator('#js-initialData').text_content(timeout=5000)
            if data:
                j=json.loads(data)
                arts=((j.get('initialState') or {}).get('entities') or {}).get('articles') or {}
                aid=url.rstrip('/').split('/')[-1]
                art=arts.get(aid)
                if art:
                    title=art.get('title')
                    body=clean_html(art.get('content'))
                    source='initial_data'
        except Exception:
            pass
        if not body:
            try:
                title = title or page.locator('h1.Post-Title').first.inner_text(timeout=5000)
            except Exception:
                pass
            for sel in ['.Post-RichTextContainer','.Post-RichText','article .RichText']:
                try:
                    txt=page.locator(sel).first.inner_text(timeout=5000)
                    if txt and txt.strip():
                        body=txt.strip(); source='dom'; break
                except Exception:
                    pass
        out.append({'url':url,'title':title,'body':body,'source':source,'final_url':page.url})
        page.close()
    browser.close()
print(json.dumps(out, ensure_ascii=False))
