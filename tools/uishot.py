"""Streamlit ऐप का tablet-चौड़ाई पर screenshot (⚙️ इनपुट खोलकर)।"""
import os
from playwright.sync_api import sync_playwright
os.makedirs("shots", exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch()
    for w, h, nm in ((1000, 1600, "tablet_portrait"), (1280, 800, "tablet_landscape")):
        pg = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=2, color_scheme="dark")
        pg.goto("http://localhost:8501", wait_until="networkidle")
        pg.wait_for_timeout(8000)
        pg.screenshot(path=f"shots/{nm}_top.png", full_page=False)
        try:
            q = pg.locator('div[data-testid="stExpander"]').nth(1)
            q.screenshot(path=f"shots/{nm}_qbt.png")
        except Exception as e:  # noqa: BLE001
            open(f"shots/{nm}_qbt_err.txt", "w").write(repr(e))
        try:
            pg.get_by_text("⚙️ इनपुट").first.click()
            pg.wait_for_timeout(2500)
            exp = pg.locator('div[data-testid="stExpander"]').first
            exp.screenshot(path=f"shots/{nm}_inputs.png")
            for i, tab in enumerate(pg.locator('div[data-testid="stExpander"] button[role="tab"]').all()[:6]):
                tab.click(); pg.wait_for_timeout(800)
                exp.screenshot(path=f"shots/{nm}_tab{i}.png")
        except Exception as e:  # noqa: BLE001
            open(f"shots/{nm}_err.txt", "w").write(repr(e))
        pg.close()
    b.close()
