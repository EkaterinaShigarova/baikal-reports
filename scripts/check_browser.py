"""Optional browser smoke test against the running server and local evidence collection."""

import os
from pathlib import Path
from playwright.sync_api import sync_playwright

artifacts = Path("data/browser-check")
artifacts.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch(
        executable_path=os.getenv("CHROMIUM_EXECUTABLE")
        or ("/usr/bin/chromium" if Path("/usr/bin/chromium").exists() else None),
        headless=True,
        args=["--no-sandbox"],
    )
    page = b.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    base = os.getenv("BAIKAL_TEST_URL", "http://127.0.0.1:8000").rstrip("/")
    page.goto(base)
    page.wait_for_selector("#stats .card")
    assert page.locator("#stats").inner_text().replace("\u00a0", " ").find("168") != -1
    page.locator('nav a[href="#data"]').click()
    page.wait_for_selector("#chart circle")
    assert page.locator("#chart circle").count() == 7
    page.locator("#year-from").select_option("2015")
    page.locator("#year-to").select_option("2017")
    page.wait_for_function('document.querySelectorAll("#chart circle").length===3')
    assert page.locator("#data-rows tr").count() == 3
    page.locator("#year-from").select_option("")
    page.locator("#year-to").select_option("")
    page.locator("#indicator").select_option("2024-report-table-1.3.1.5-total")
    page.wait_for_function(
        'document.querySelector("#chart-note").textContent.includes("2024")'
    )
    assert page.locator("#chart circle").count() == 3
    assert (
        "13,281" in page.locator("#data-rows").inner_text()
        or "244,681" in page.locator("#data-rows").inner_text()
    )
    page.locator("#indicator").select_option("rosgidromet2025-table4.7-1-0")
    page.wait_for_function('document.querySelectorAll("#chart circle").length===2')
    assert "61,8" in page.locator("#data-rows").inner_text()
    page.locator("#indicator").select_option("seal-population-estimates")
    page.wait_for_function(
        'document.querySelector("#chart-note").textContent.includes("не подтверждена")'
    )
    assert page.locator("#chart circle").count() == 0
    page.locator('nav a[href="#wiki"]').click()
    page.locator("#wiki-list button", has_text="Байкальская нерпа").first.click()
    page.wait_for_selector("#wiki-content table")
    assert "163,2" in page.locator("#wiki-content").inner_text()
    page.locator('nav a[href="#search"]').click()
    page.locator("#query").fill("Селенга")
    page.locator("#search-form button").click()
    page.wait_for_selector("#search-results .result")
    page.locator("#search-results .result a").first.click()
    page.wait_for_function('document.querySelector("#page-text").textContent.length>0')
    assert "Селенг" in page.locator("#page-text").inner_text()
    page.goto(base + "/#page/drive-1QKhC57MB-qveIXhYWKGfSOkTmzdFw_mJ/30")
    page.wait_for_function(
        'document.querySelector("#page-title").textContent.includes("30")'
    )
    assert page.locator("#page-tables table").count() == 2
    assert "сентябрь" in page.locator("#page-tables").inner_text()
    assert (
        page.locator("#page-tables table")
        .first.locator("tr")
        .first.locator("td")
        .count()
        == 4
    )
    page.locator('nav a[href="#overview"]').click()
    page.screenshot(path=str(artifacts / "dashboard.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator('nav a[href="#data"]').click()
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), (
        "Mobile overflow"
    )
    page.screenshot(path=str(artifacts / "mobile.png"), full_page=True)
    assert not errors, errors
    print(
        "Browser passed: 168 observations, year filters, new series, comparison guard, Wiki tables, search, PDF table September column, mobile; no JS errors."
    )
    b.close()
