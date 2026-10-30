"""Streamlit ऐप का tablet-चौड़ाई पर screenshot (⚙️ popover खोलकर)।"""
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
            pg.get_by_role("button", name="⚙️").first.click()
            pg.wait_for_timeout(2500)
            pg.screenshot(path=f"shots/{nm}_inputs.png", full_page=False)
        except Exception as e:  # noqa: BLE001
            open(f"shots/{nm}_err.txt", "w").write(repr(e))
        pg.close()
    b.close()
