from patchright.sync_api import sync_playwright
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
    context=browser.new_context(
        locale='zh-CN',
        timezone_id='Asia/Shanghai',
        viewport={'width': 1365, 'height': 768},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36'
    )
    for url in URLS:
        page=context.new_page()
        err=None
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=60000)
            page.wait_for_timeout(5000)
        except Exception as e:
            err=str(e)
        title=None; body=None; source=None
        try:
            data=page.locator('#js-initialData').text_content(timeout=5000)
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
                title=title or page.locator('h1.Post-Title').first.inner_text(timeout=3000)
            except Exception:
                pass
            for sel in ['.Post-RichTextContainer','.Post-RichText','article .RichText']:
                try:
                    txt=page.locator(sel).first.inner_text(timeout=3000)
                    if txt and txt.strip():
                        body=txt.strip(); source='dom'; break
                except Exception:
                    pass
        out.append({'url':url,'title':title,'body':body,'source':source,'final_url':page.url,'error':err})
        page.close()
    browser.close()
print(json.dumps(out, ensure_ascii=False))
