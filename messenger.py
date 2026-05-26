import time
import sys
import json
import re
from playwright.sync_api import sync_playwright

# ── WINDOWS UTF-8 FIX ───────────────────────────────────
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── CONFIG ──────────────────────────────────────────────
USER_DATA_DIR = "fb_marketplace_profile"
START_URL     = "https://www.facebook.com"
RESULTS_FILE  = "marketplace_chat_numbers.json"

PHONE_REGEX = r"(?:(?:\+|00)?2)?(01[0125](?:[\s\-\.]*[0-9]){8})"

SKIP_PREFIXES = ["You:", "أنت:", "All", "Unread", "Groups", "Communities",
                 "الكل", "غير مقروء", "المجموعات", "Marketplace", "ماركت بليس", "السوق"]

def normalize_arabic_numerals(text):
    if not text:
        return ""
    arabic_to_english = str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789')
    return text.translate(arabic_to_english)

def extract_phones(text):
    if not text:
        return []
    normalized = normalize_arabic_numerals(text)
    matches = re.findall(PHONE_REGEX, normalized)
    results = []
    for m in matches:
        clean = re.sub(r'[\s\-\.]', '', m)
        if clean.startswith('201'):
            clean = clean[1:]
        if clean not in results:
            results.append(clean)
    return results

def is_within_24h(aria_label):
    if not aria_label:
        return False
    label = aria_label.lower().strip()
    if "minute" in label or "just now" in label or "second" in label:
        return True
    match = re.match(r'(\d+)\s+hour', label)
    if match and int(match.group(1)) <= 23:
        return True
    return False

def should_skip(name):
    if not name or len(name.strip()) < 5:
        return True
    for prefix in SKIP_PREFIXES:
        if name.strip().startswith(prefix):
            return True
    return False

def open_messenger_popup(page):
    for label in ["Messenger", "مراسلة"]:
        el = page.query_selector(f'[aria-label="{label}"][role="button"]')
        if el:
            el.click(force=True)
            page.wait_for_timeout(1500)
            return True
    return False

def click_marketplace_tab(page):
    for label in ["Marketplace", "ماركت بليس", "السوق"]:
        try:
            els = page.get_by_role("button", name=label, exact=True)
            for i in range(els.count()):
                el = els.nth(i)
                if el.is_visible():
                    el.click(force=True)
                    page.wait_for_timeout(1500)
                    return True
        except Exception:
            pass

    for label in ["Marketplace", "ماركت بليس", "السوق"]:
        try:
            els = page.locator(f'div[role="button"]:has-text("{label}")')
            for i in range(els.count()):
                el = els.nth(i)
                if el.is_visible():
                    el.click(force=True)
                    page.wait_for_timeout(1500)
                    return True
        except Exception:
            pass

    return False

def scroll_chat_list(page, scrolls=8):
    print(f"    Scrolling chat list ({scrolls} scrolls)...")
    try:
        scrolled = page.evaluate("""(scrolls) => {
            const abbr = document.querySelector('abbr[aria-label]');
            if (!abbr) return false;
            let node = abbr;
            for (let i = 0; i < 20; i++) {
                node = node.parentElement;
                if (!node) break;
                const style = window.getComputedStyle(node);
                if (style.overflowY === 'auto' || style.overflowY === 'scroll') {
                    for (let s = 0; s < scrolls; s++) {
                        node.scrollTop += 300;
                    }
                    return true;
                }
            }
            return false;
        }""", scrolls)
        if scrolled:
            page.wait_for_timeout(2000)
            print("    [OK] Scrolled chat list")
        else:
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(2000)
    except Exception as e:
        print(f"    [WARN] Scroll failed: {e}")

def collect_chat_data(page):
    return page.evaluate("""() => {
        const results = [];
        const seen = new Set();
        const abbrs = document.querySelectorAll('abbr[aria-label]');

        for (const abbr of abbrs) {
            const timeLabel = abbr.getAttribute('aria-label') || '';
            let node = abbr;
            let chatName = '';

            for (let i = 0; i < 25; i++) {
                node = node.parentElement;
                if (!node) break;

                const titleSpan = node.querySelector(
                    'span.x1lliihq.x6ikm8r.x10wlt62.x1n2onr6.xlyipyv.xuxw1ft:not(.x1j85h84)'
                );

                if (titleSpan) {
                    const txt = titleSpan.textContent.trim();
                    if (txt.length > 5 && !seen.has(txt)) {
                        chatName = txt;
                        seen.add(txt);
                        break;
                    }
                }
            }

            if (chatName) {
                results.push({ name: chatName, time: timeLabel });
            }
        }
        return results;
    }""")

# FIX: Broader selector + fallback text search so chats are found after popup re-opens
def find_and_click_chat(page, chat_name):
    clicked = page.evaluate("""(targetName) => {
        // Strategy 1: exact match on known span class
        const spans = document.querySelectorAll(
            'span.x1lliihq.x6ikm8r.x10wlt62.x1n2onr6.xlyipyv.xuxw1ft:not(.x1j85h84)'
        );
        for (const span of spans) {
            if (span.textContent.trim() === targetName) {
                let node = span;
                for (let i = 0; i < 10; i++) {
                    node = node.parentElement;
                    if (!node) break;
                    if (node.getAttribute('role') === 'link' ||
                        node.getAttribute('role') === 'button' ||
                        node.tagName === 'A') {
                        node.click();
                        return true;
                    }
                }
                span.click();
                return true;
            }
        }

        // Strategy 2: any span containing the name (partial match for truncated titles)
        const allSpans = document.querySelectorAll('span');
        for (const span of allSpans) {
            const txt = span.textContent.trim();
            if (txt === targetName || (targetName.length > 30 && targetName.startsWith(txt) && txt.length > 20)) {
                let node = span;
                for (let i = 0; i < 15; i++) {
                    node = node.parentElement;
                    if (!node) break;
                    if (node.getAttribute('role') === 'link' ||
                        node.getAttribute('role') === 'button' ||
                        node.tagName === 'A') {
                        node.click();
                        return true;
                    }
                }
            }
        }

        return false;
    }""", chat_name)
    return clicked

def go_back_to_chat_list(page):
    """
    FIX: Instead of navigating away (which loses the popup state),
    click the back arrow inside the Messenger popup to return to the list.
    Falls back to re-opening the popup from scratch only if needed.
    """
    # Try the back arrow inside the Messenger panel
    try:
        back = page.query_selector('[aria-label="Back"][role="button"]')
        if not back:
            back = page.query_selector('[aria-label="رجوع"][role="button"]')
        if back and back.is_visible():
            back.click(force=True)
            page.wait_for_timeout(1500)
            return True
    except Exception:
        pass

    # Fallback: re-open the popup from current page (don't navigate away)
    try:
        open_messenger_popup(page)
        click_marketplace_tab(page)
        page.wait_for_timeout(1500)
        return True
    except Exception:
        pass

    return False

def wait_for_chat_load(page, chat_name, timeout=8):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            body = page.locator("body").text_content() or ""
            if chat_name[:20] in body:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False

def scan_chat_for_phones(page, already_seen):
    """
    Extract phone numbers from the active chat's message bubbles only.
    Excludes numbers already seen in previous chats to prevent DOM bleed.
    Never uses document.body (too broad — includes sidebar previews).
    """
    candidates = page.evaluate("""() => {
        const results = [];

        // Strategy 1: named message containers (most precise)
        const labeled = [
            document.querySelector('[role="main"]'),
            document.querySelector('[data-pagelet="MWChat"]'),
            document.querySelector('[aria-label="Messages"]'),
            document.querySelector('[aria-label="رسائل"]'),
        ];
        for (const c of labeled) {
            if (c) {
                const txt = Array.from(c.querySelectorAll('[dir="auto"]'))
                    .map(s => s.textContent).join(' ');
                if (txt.trim().length > 50) results.push(txt);
            }
        }

        // Strategy 2: scrollable panels that are NOT the chat list sidebar
        // The chat list is narrow (< 400px wide); the message panel is wider
        const allScrollable = Array.from(document.querySelectorAll('*')).filter(el => {
            const s = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return (s.overflowY === 'auto' || s.overflowY === 'scroll') &&
                   el.scrollHeight > 200 &&
                   rect.width > 400;   // exclude narrow sidebar
        });
        for (const el of allScrollable) {
            const txt = Array.from(el.querySelectorAll('[dir="auto"]'))
                .map(s => s.textContent).join(' ');
            if (txt.trim().length > 50) results.push(txt);
        }

        return results;
    }""")

    if not candidates:
        print(f"    [DBG] No candidates found")
        return []

    # Pick the candidate with the most text
    best_text = max(candidates, key=len)
    print(f"    [DBG] Best candidate: {len(best_text)} chars from {len(candidates)} sources")

    all_phones = extract_phones(best_text)
    # Only return numbers NOT seen in any previous chat
    new_phones = [p for p in all_phones if p not in already_seen]
    print(f"    [DBG] All phones: {all_phones} → new only: {new_phones}")
    return new_phones

def main():
    results = []

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
            no_viewport=True,
            slow_mo=50,
        )

        page = context.new_page()

        # ── STEP 1: Open Facebook ────────────────────────────
        print("[1] Opening Facebook...")
        page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)

        # ── STEP 2: Open Messenger popup ─────────────────────
        print("[2] Opening Messenger popup...")
        if not open_messenger_popup(page):
            print("    [ERR] Could not find Messenger button")
            input("Press ENTER to close...")
            context.close()
            return
        print("    [OK] Messenger popup opened")

        # ── STEP 3: Click Marketplace tab ───────────────────
        print("[3] Clicking Marketplace tab...")
        if not click_marketplace_tab(page):
            print("    [ERR] Could not find Marketplace tab")
            input("Press ENTER to close...")
            context.close()
            return
        print("    [OK] Marketplace tab opened")
        page.wait_for_timeout(2000)

        # ── STEP 4: Scroll to load ALL chats ────────────────
        print("[4] Scrolling to load all chats...")
        scroll_chat_list(page, scrolls=10)

        # ── STEP 5: Extract chat list via JS ─────────────────
        print("[5] Scanning chat list...")
        chat_data = collect_chat_data(page)
        print(f"    Found {len(chat_data)} entries from JS scan")

        seen_names = set()
        recent_chats = []
        skipped_old = 0

        for chat in chat_data:
            name = chat["name"].strip()
            time_label = chat["time"]

            if should_skip(name):
                continue
            if name in seen_names:
                continue
            if not is_within_24h(time_label):
                skipped_old += 1
                continue

            seen_names.add(name)
            recent_chats.append(chat)
            print(f"    [+] ({time_label}): {name[:70]}")

        print(f"    Skipped {skipped_old} chats older than 24h")
        print(f"\n[6] Found {len(recent_chats)} valid chats within 24h")

        # ── STEP 6: Open each chat and scan ──────────────────
        all_seen_phones = set()   # global across all chats to prevent bleed

        for i, chat in enumerate(recent_chats):
            chat_name = chat["name"].strip()
            time_label = chat["time"]

            print(f"\n{'='*60}")
            print(f"[Chat {i+1}/{len(recent_chats)}] ({time_label})")
            print(f"    Name: {chat_name[:80]}")

            try:
                # FIX: Go back to chat list WITHOUT navigating away from Facebook
                go_back_to_chat_list(page)

                # Scroll so the target chat is visible
                scroll_chat_list(page, scrolls=3)
                page.wait_for_timeout(1000)

                # FIX: Retry clicking up to 3 times with extra scroll between attempts
                clicked = False
                for attempt in range(3):
                    clicked = find_and_click_chat(page, chat_name)
                    if clicked:
                        break
                    print(f"    [RETRY {attempt+1}] Chat not found, scrolling more...")
                    scroll_chat_list(page, scrolls=3)
                    page.wait_for_timeout(1000)

                if not clicked:
                    print(f"    [ERR] Could not find chat after retries — skipping")
                    results.append({"chat_name": chat_name, "time": time_label,
                                    "phones": [], "note": "NOT_FOUND"})
                    continue

                print("    [OK] Clicked chat")

                wait_for_chat_load(page, chat_name, timeout=8)
                page.wait_for_timeout(2000)

                current_url = page.url
                if "photo" in current_url or "/posts/" in current_url:
                    print(f"    [WARN] Wrong page detected")
                    go_back_to_chat_list(page)
                    results.append({"chat_name": chat_name, "time": time_label,
                                    "phones": [], "note": "WRONG_PAGE"})
                    continue

                phones = scan_chat_for_phones(page, already_seen=all_seen_phones)

                if phones:
                    print(f"    [OK] Numbers found: {phones}")
                    all_seen_phones.update(phones)
                else:
                    print(f"    [--] No phone number found")

                results.append({
                    "chat_name": chat_name,
                    "time": time_label,
                    "phones": phones
                })

                with open(RESULTS_FILE, "w", encoding="utf-8") as f:
                    json.dump(results, f, ensure_ascii=False, indent=4)

            except Exception as e:
                print(f"    [ERR] {str(e)[:100]}")
                try:
                    go_back_to_chat_list(page)
                except Exception:
                    pass
                results.append({"chat_name": chat_name, "time": time_label,
                                "phones": [], "note": f"CRASH: {str(e)[:60]}"})

        # ── SUMMARY ──────────────────────────────────────────
        print(f"\n{'='*60}")
        print(f"[DONE] Processed {len(recent_chats)} chats")
        print(f"Results saved to: {RESULTS_FILE}")
        found = [r for r in results if r["phones"]]
        print(f"\nNumbers found in {len(found)}/{len(results)} chats:")
        for r in found:
            print(f"  {r['phones']} — {r['chat_name'][:60]}")

        input("\nPress ENTER to close browser...")
        context.close()

if __name__ == "__main__":
    main()