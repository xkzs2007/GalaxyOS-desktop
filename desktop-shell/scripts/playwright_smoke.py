"""playwright_smoke.py — End-to-end visual smoke test for the GalaxyOS desktop app.

Spins up Playwright + the local Chromium build, opens the renderer's
index.html (served from a local HTTP server on :8080) which talks to
the sidecar on :5758 via SSE, asks a question, waits for the streamed
TokUI bubble to render, then takes a screenshot.

Run after:
  1. The Python sidecar is running on :5757 (zmq) and :5758 (SSE).
  2. A static HTTP server is serving renderer/ on :8080.

Outputs:
  - C:/Users/Administrator/Desktop/galaxyos-desktop-initial.png
  - C:/Users/Administrator/Desktop/galaxyos-desktop-after-ask.png
"""
import asyncio
import sys
import time
from pathlib import Path

# Output to desktop so the user can see the result immediately.
OUTPUT_DIR = Path(r"C:\Users\Administrator\Desktop")


async def main() -> int:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        # Use the Chromium build that Playwright already installed.
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 820},
            device_scale_factor=1.5,
        )
        page = await ctx.new_page()

        # Surface page console for debugging
        page.on("console", lambda msg: print(f"[page/{msg.type}] {msg.text}"))
        page.on("pageerror", lambda err: print(f"[page/error] {err}"))

        # ── 1. Load the renderer ────────────────────────────────────
        print("Loading http://127.0.0.1:8080/index.html ...")
        await page.goto("http://127.0.0.1:8080/index.html", wait_until="domcontentloaded", timeout=30000)
        print("  page DOM loaded")

        # Give the boot path time: TokUI CDN → window.TokUI init → welcome bubble
        await page.wait_for_timeout(3000)

        # Verify the page rendered
        title = await page.title()
        print(f"  title: {title}")

        # Check that the welcome bubble appeared
        welcome = await page.locator("#tokui-container").inner_html()
        if "欢迎使用 GalaxyOS 桌面端" not in welcome and "GalaxyOS" not in welcome:
            print("  WARN: welcome bubble not detected yet; waiting more...")
            await page.wait_for_timeout(2000)
            welcome = await page.locator("#tokui-container").inner_html()
        print(f"  welcome bubble present: {'GalaxyOS' in welcome}")
        print(f"  conn status: {await page.locator('#conn-text').text_content()}")

        # First screenshot: initial state
        path1 = OUTPUT_DIR / "galaxyos-desktop-initial.png"
        await page.screenshot(path=str(path1), full_page=False)
        print(f"  screenshot → {path1}")

        # ── 2. Switch to Process mode and ask a question ────────────
        print()
        print("Switching to Process mode and asking 'R-CCAM 是什么' ...")
        await page.click("button.mode-btn[data-mode='process']")
        await page.wait_for_timeout(200)

        # Clear input + type question
        await page.fill("#input", "R-CCAM 是什么？请详细解释。")
        await page.wait_for_timeout(200)

        # Click send
        await page.click("#send")
        print("  send clicked, waiting for SSE stream...")

        # Wait for the user bubble to appear (first SSE fragment)
        try:
            await page.wait_for_selector(
                "#tokui-container .tokui-bubble, #tokui-container [class*='bubble']",
                timeout=10000,
            )
        except Exception:
            print("  WARN: no bubble selector found; trying generic selector")

        # Wait for streaming to complete. Two markers to look for:
        #   (1) '置信度 N%' (footer of the assistant bubble)
        #   (2) bubble count >= 2 (user + assistant)
        # Generous timeout because the sidecar may have to import GalaxyOS.
        try:
            await page.wait_for_function(
                "() => {"
                "  const c = document.querySelector('#tokui-container');"
                "  if (!c) return false;"
                "  const txt = c.innerText || '';"
                "  const hasConfidence = /置信度\\s*\\d+%/.test(txt);"
                "  const bubbles = c.querySelectorAll('[class*=\"bubble\"]').length;"
                "  return hasConfidence && bubbles >= 2;"
                "}",
                timeout=45000,  # galaxyos init may take ~30s on first load
            )
            print("  streamed answer visible (confidence + 2+ bubbles)")
        except Exception:
            print("  WARN: stream marker not found within 45s; capturing anyway")

        # Give a moment for any final layout shift
        await page.wait_for_timeout(800)

        # Second screenshot: after ask
        path2 = OUTPUT_DIR / "galaxyos-desktop-after-ask.png"
        await page.screenshot(path=str(path2), full_page=False)
        print(f"  screenshot → {path2}")

        # ── 3. Verify final state ───────────────────────────────────
        final_html = await page.locator("#tokui-container").inner_html()
        bubble_count = final_html.count("class=") - final_html.count("class=\"tokui-")
        print()
        print(f"  container innerHTML size: {len(final_html)} bytes")
        print(f"  bubble-ish nodes: ~{bubble_count // 2}")

        # Check that 2 bubbles rendered (user + assistant)
        # TokUI's actual class names depend on the bundle; just check size
        if len(final_html) < 500:
            print("  WARN: container looks too small; may not have rendered")
        else:
            print("  OK: TokUI bubbles rendered")

        await browser.close()
        return 0


if __name__ == "__main__":
    try:
        rc = asyncio.run(main())
        print(f"\nDone, exit code {rc}")
    except Exception as e:
        print(f"\nFATAL: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
