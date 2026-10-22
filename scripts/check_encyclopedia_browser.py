"""Optional browser check of encyclopedia navigation against the running local server."""

import os
from playwright.sync_api import sync_playwright
import json
from pathlib import Path

base = os.getenv("BAIKAL_TEST_URL", "http://127.0.0.1:8000").rstrip("/")
root = Path(__file__).resolve().parents[1]
config = json.loads((root / "config/encyclopedia.json").read_text())
new = [
    dict(t, path=t["article"])
    for section in config["sections"]
    for sub in section["subtopics"]
    for t in sub["topics"]
    if t["sprint_new"] and t.get("article")
]
artifacts = root / "data/browser-encyclopedia"
artifacts.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch(
        executable_path=os.getenv("CHROMIUM_EXECUTABLE")
        or ("/usr/bin/chromium" if Path("/usr/bin/chromium").exists() else None),
        headless=True,
        args=["--no-sandbox"],
    )
    page = b.new_page(viewport={"width": 1440, "height": 1100})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/#wiki")
    page.wait_for_selector('#wiki-content[data-path="catalog.md"]')
    assert page.locator("#wiki-list>.wiki-group").count() == len(config["sections"]) + 1
    assert page.locator(".wiki-subtopic").count() == sum(
        len(section["subtopics"]) for section in config["sections"]
    )
    for article in new:
        path = article["path"]
        page.locator(f'#wiki-list button[data-path="{path}"]').click()
        page.wait_for_selector(f'#wiki-content[data-path="{path}"]')
        assert article["title"] in page.locator("#wiki-content h2").first.inner_text()
        assert "ограничения" in page.locator("#wiki-content").inner_text().lower()
    page.locator("#wiki-query").fill("синедровый")
    page.wait_for_function(
        'document.querySelector("#wiki-search-status").textContent.includes("Найдено статей: 1")'
    )
    page.locator('#wiki-list button[data-path="syntheses/phytoplankton.md"]').click()
    page.wait_for_selector('#wiki-content[data-path="syntheses/phytoplankton.md"]')
    page.locator("#wiki-content a", has_text="Спирогира").click()
    page.wait_for_selector(
        '#wiki-content[data-path="syntheses/spirogyra-observations.md"]'
    )
    page.reload()
    page.wait_for_selector(
        '#wiki-content[data-path="syntheses/spirogyra-observations.md"]'
    )
    page.locator("#wiki-content a", has_text=":p40").first.click()
    page.wait_for_function(
        'location.hash.startsWith("#page/")&&document.querySelector("#page-text").textContent.length>100'
    )
    assert "40" in page.locator("#page-title").inner_text()
    page.go_back()
    page.wait_for_selector(
        '#wiki-content[data-path="syntheses/spirogyra-observations.md"]'
    )
    page.locator("#wiki-query").fill("несуществующеесловоxyz")
    page.wait_for_function(
        'document.querySelector("#wiki-search-status").textContent.includes("Найдено статей: 0")'
    )
    page.locator("#wiki-query").fill("")
    page.wait_for_selector(".wiki-subtopic")
    page.locator('#wiki-list button[data-path="catalog.md"]').click()
    page.wait_for_selector('#wiki-content[data-path="catalog.md"]')
    page.screenshot(path=str(artifacts / "desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    page.screenshot(path=str(artifacts / "mobile.png"), full_page=True)
    page.locator('nav a[href="#data"]').click()
    page.wait_for_selector("#chart circle")
    assert page.locator("#chart circle").count() == 7
    page.locator("#indicator").select_option("non-sulfate-sulfur-control")
    page.wait_for_function(
        'document.querySelector("#chart-note").textContent.includes("не подтверждена")'
    )
    assert page.locator("#chart circle").count() == 0
    page.locator('nav a[href="#search"]').click()
    page.locator("#query").fill("Селенга")
    page.locator("#search-form button").click()
    page.wait_for_selector("#search-results .result")
    assert not errors, errors
    print(
        "Passed: all 14 new articles, 6 sections/15 subtopics, body search, related link, reload/history, source page, empty/reset search, mobile, existing chart/comparability guard and FTS. No JS errors."
    )
    b.close()
