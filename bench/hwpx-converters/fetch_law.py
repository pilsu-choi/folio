"""Download law body as HWPX and PDF from law.go.kr via the page's own save dialog.

usage: python fetch_law.py name=lsiSeq ...  (needs playwright + chromium; writes samples/, ref/)
"""
import asyncio, sys
from playwright.async_api import async_playwright
LAWS = dict(a.split("=") for a in sys.argv[1:])  # name=lsiSeq
async def save(pg, seq, ext, dst):
    await pg.goto(f"https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq={seq}", wait_until="domcontentloaded"); await pg.wait_for_timeout(4000)
    await pg.click("#bdySaveBtn"); await pg.wait_for_timeout(1500)
    await pg.check(f"input[name=fileType][value={ext}]")
    pg.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
    async with pg.expect_download(timeout=120000) as dl:
        await pg.click("a[onclick*=beforeSavePrint]:visible")
    d = await dl.value; await d.save_as(dst); print(seq, ext, d.suggested_filename, flush=True)
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(); pg = await b.new_page(accept_downloads=True)
        for name, seq in LAWS.items():
            for ext, dst in (("hwpx", f"samples/{name}.hwpx"), ("pdf", f"ref/{name}.pdf")):
                try: await save(pg, seq, ext, dst)
                except Exception as e: print("FAIL", name, ext, e, flush=True)
                await pg.wait_for_timeout(3000)  # be gentle with the server
        await b.close()
asyncio.run(main())
