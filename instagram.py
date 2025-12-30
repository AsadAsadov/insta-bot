import customtkinter as ctk
import threading
import queue
import time
import re
import atexit
import os
import json
import random
from datetime import datetime
from tkinter import filedialog

from playwright.sync_api import sync_playwright, TimeoutError

# ================== PATHS / FILES ==================
DATA_DIR = "data"
SENT_FILE = os.path.join(DATA_DIR, "sent_users.txt")
BLACKLIST_FILE = os.path.join(DATA_DIR, "blacklist.txt")
SAVED_FILE = os.path.join(DATA_DIR, "users_saved.txt")
PROGRESS_FILE = os.path.join(DATA_DIR, "dm_progress.json")
COMMENTED_POSTS_FILE = os.path.join(DATA_DIR, "commented_posts.txt")

os.makedirs(DATA_DIR, exist_ok=True)

# ================== QUEUES & STATE ==================
task_queue = queue.Queue()
result_queue = queue.Queue()

playwright = None
context = None
page = None
browser_ready = False

dm_pause_flag = threading.Event()
dm_stop_flag = threading.Event()

saved_users = []  # list[str], order preserved
sent_users = set()

USERNAME_RE = re.compile(r"^/([A-Za-z0-9._]{1,30})/?$")


# ================== FILE HELPERS ==================
def load_set(path):
    if not os.path.exists(path):
        return set()
    with open(path, "r", encoding="utf-8") as f:
        return set(line.strip().lstrip("@").lower() for line in f if line.strip())


def append_line(path, line):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def save_users_txt(users):
    with open(SAVED_FILE, "w", encoding="utf-8") as f:
        for u in users:
            f.write(u + "\n")


def load_progress():
    if not os.path.exists(PROGRESS_FILE):
        return {"index": 0, "last_updated": None}
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"index": 0, "last_updated": None}


def save_progress(index):
    payload = {
        "index": index,
        "last_updated": datetime.now().isoformat(timespec="seconds"),
    }
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_sent_users(filepath=SENT_FILE):
    global sent_users
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                u = line.strip().lstrip("@").lower()
                if u:
                    sent_users.add(u)
        return True, len(sent_users)
    except FileNotFoundError:
        return False, 0


# ================== PLAYWRIGHT WORKER ==================
def ensure_browser():
    global playwright, context, page, browser_ready

    try:
        if browser_ready and context and page:
            # səhifə hələ yaşayır?
            page.title()
            return
    except:
        # context ölüdür → sıfırla
        browser_ready = False

    playwright = sync_playwright().start()
    context = playwright.chromium.launch_persistent_context(
        user_data_dir="C:/IG_Profile",
        headless=False,
        viewport=None,
    )
    page = context.new_page()
    browser_ready = True


def playwright_worker():
    while True:
        task = task_queue.get()
        try:
            if task["type"] == "shutdown":
                break

            if task["type"] == "preview":
                url = task["url"]
                ensure_browser()
                users = extract_usernames_internal(page, url)
                result_queue.put(("USERS", users))

            elif task["type"] == "dm_start":
                ensure_browser()
                params = task["params"]
                dm_send_loop(page, params)

            elif task["type"] == "feed_comment":
                ensure_browser()
                params = task["params"]
                feed_comment_loop(page, params)

            elif task["type"] == "hashtag_comment":
                ensure_browser()
                params = task["params"]
                hashtag_comment_loop(page, params)

        except Exception as e:
            result_queue.put(("ERROR", str(e)))
        finally:
            task_queue.task_done()


# ================== CORE: EXTRACT USERNAMES ==================
def _find_scrollable_box(page, root_locator, tries=25, pause_ms=400):
    """
    Verilən root (dialog və ya main) içində scroll olunan container tapır.
    Tapmasa None qaytarır.
    """
    for _ in range(tries):
        try:
            handle = root_locator.evaluate_handle(
                """
                (root) => {
                  if (!root) return null;
                  const all = root.querySelectorAll('*');
                  for (const el of all) {
                    const st = getComputedStyle(el);
                    const oy = st.overflowY;
                    if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 50) {
                      return el;
                    }
                  }
                  return null;
                }
                """
            )
            el = handle.as_element() if handle else None
            if el:
                return el
        except:
            pass
        page.wait_for_timeout(pause_ms)
    return None


def _open_comments_if_needed(page):
    """
    Reels-də comment dialogunu açmağa çalışır.
    Postlarda comment hissə çox vaxt açıq olur, ona toxunmur.
    """
    # artıq dialog açıqdırsa, çıx
    try:
        if page.locator("div[role='dialog']").count() > 0:
            return
    except:
        pass

    # Postlarda çox vaxt "Yorum ekle." textarea görünür — deməli comment hissə açıqdır
    try:
        if (
            page.locator(
                "textarea[aria-label*='Yorum' i], textarea[placeholder*='Yorum' i]"
            ).count()
            > 0
        ):
            return
    except:
        pass

    # Reels / bəzi hallarda comment ikonuna klik lazımdır
    try:
        comment_btn = page.locator(
            "svg[aria-label*='comment' i], "
            "svg[aria-label*='yorum' i], "
            "svg[aria-label*='şərh' i], "
            "div[role='button']:has(svg[aria-label*='comment' i]), "
            "div[role='button']:has(svg[aria-label*='yorum' i])"
        ).first

        if comment_btn.count() > 0:
            comment_btn.click(force=True)
            page.wait_for_timeout(1500)
    except:
        pass


def extract_usernames_internal(page, url):
    already_sent = load_set(SENT_FILE)
    blacklist = load_set(BLACKLIST_FILE)

    users = set()

    page.goto(url, timeout=90000, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)

    # 1) Reels-disə comment dialogunu aç, postdursa açıq ola bilər
    _open_comments_if_needed(page)
    page.wait_for_timeout(2000)

    # 2) Root seç: əvvəl dialog varsa dialog, yoxsa main (post üçün)
    dialog = None
    try:
        dlg = page.locator("div[role='dialog']").first
        if dlg.count() > 0:
            dialog = dlg
    except:
        dialog = None

    root = dialog if dialog else page.locator("main").first
    try:
        root.wait_for(timeout=15000)
    except:
        pass

    # 3) Scroll box tap (dialog içi və ya səhifə içi)
    scroll_box = _find_scrollable_box(page, root, tries=25, pause_ms=400)

    # Əgər scroll box tapılmadısa: postlarda bəzən scroll "window" olur.
    # Onda window scroll ilə davam edəcəyik.
    use_window_scroll = False
    if not scroll_box:
        use_window_scroll = True

    stable_rounds = 0
    last_user_count = 0
    last_height = 0

    # 4) Scroll loop
    for _ in range(600):
        if dm_stop_flag.is_set():
            break

        # "Daha çox şərh" / "View more" varsa kliklə (dialog və ya page)
        try:
            more = root.locator(
                "text=/Daha çox|View more|Load more|tümünü gör|more comments/i"
            )
            if more.count() > 0:
                more.first.click(timeout=1500)
                page.wait_for_timeout(1200)
        except:
            pass

        # Username-ləri yığ
        try:
            anchors = root.locator("a[href^='/']").all()
        except:
            anchors = []

        for a in anchors:
            try:
                href = (a.get_attribute("href") or "").strip()
            except:
                continue

            if not href or href.count("/") < 2:
                continue

            seg = href.split("/")[1].lower()

            # IG internal route-ları at
            if seg in {"p", "reel", "reels", "explore", "accounts", "direct"}:
                continue

            # comment permalink /p/.../c/... görsən də username linkləri yenə /user/ formatındadır
            u = seg

            if u in already_sent or u in blacklist:
                continue

            users.add(u)

        # Scroll et (ya container, ya window)
        if use_window_scroll:
            try:
                prev_h = page.evaluate("() => document.body.scrollHeight")
                page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1500)
                new_h = page.evaluate("() => document.body.scrollHeight")
            except:
                prev_h, new_h = last_height, last_height
        else:
            try:
                prev_h = scroll_box.evaluate("el => el.scrollHeight")
                scroll_box.evaluate("el => el.scrollTop = el.scrollHeight")
                page.wait_for_timeout(1500)
                new_h = scroll_box.evaluate("el => el.scrollHeight")
            except:
                prev_h, new_h = last_height, last_height

        # Stop şərti (user sayı + scroll height sabitlənirsə)
        if len(users) == last_user_count and new_h == last_height:
            stable_rounds += 1
        else:
            stable_rounds = 0
            last_user_count = len(users)
            last_height = new_h

        if stable_rounds >= 6:
            break

    return sorted(users)


def extract_post_shortcode(href):
    if not href:
        return None
    m = re.search(r"/(p|reel)/([A-Za-z0-9_-]+)/", href)
    if m:
        return m.group(2)
    return None


def collect_visible_feed_posts(page, commented_ids, extra_skip=None):
    posts = []
    skip_set = commented_ids | (extra_skip or set())
    try:
        articles = page.locator("article").all()
    except Exception:
        articles = []

    for art in articles:
        try:
            if art.locator("text=/Sponsorlu|Sponsored/i").count() > 0:
                continue
        except Exception:
            pass

        try:
            anchors = art.locator("a[href*='/p/'], a[href*='/reel/']").all()
        except Exception:
            anchors = []

        for a in anchors:
            try:
                href = a.get_attribute("href") or ""
            except Exception:
                continue

            shortcode = extract_post_shortcode(href)
            if not shortcode or shortcode in skip_set:
                continue

            posts.append((shortcode, href, a))
            break

    return posts


def collect_search_results_posts(page, commented_ids, extra_skip=None):
    posts = []
    skip_set = commented_ids | (extra_skip or set())

    try:
        anchors = page.locator("a[href*='/p/'], a[href*='/reel/']").all()
    except Exception:
        anchors = []

    for a in anchors:
        try:
            href = a.get_attribute("href") or ""
        except Exception:
            continue

        shortcode = extract_post_shortcode(href)
        if not shortcode or shortcode in skip_set:
            continue

        posts.append((shortcode, href, a))

    return posts


def post_comment_overlay(page):
    dlg = page.locator("div[role='dialog']").first
    if dlg.count() > 0:
        return dlg
    return page


def close_post_overlay(page):
    try:
        close_btn = page.locator(
            "[aria-label='Kapat'], button[aria-label='Kapat'], div[role='button'][aria-label='Kapat']"
        ).first
        if close_btn.count() > 0:
            close_btn.click(force=True)
            page.wait_for_timeout(800)
    except Exception:
        pass


def _containers_near_textarea(textarea):
    containers = []
    try:
        form = textarea.locator("xpath=ancestor::form[1]").first
        if form.count() > 0:
            containers.append(form)
    except Exception:
        pass

    try:
        parent = textarea.locator("xpath=parent::*").first
        if parent.count() > 0:
            containers.append(parent)
    except Exception:
        pass

    containers.append(textarea)

    unique = []
    for c in containers:
        try:
            if c and c.count() > 0 and c not in unique:
                unique.append(c)
        except Exception:
            continue
    return unique


def submit_comment_from_textarea(page, root, textarea):
    containers = _containers_near_textarea(textarea)

    try:
        textarea.press("Enter")
        page.wait_for_timeout(1200)
    except Exception:
        pass
    else:
        try:
            cleared = textarea.input_value().strip() == ""
        except Exception:
            cleared = False
        if cleared:
            return True

    for container in containers:
        try:
            share_btn = container.locator(
                "xpath=.//form//button[role='button'][normalize-space(text())='Paylaş'] | "
                "xpath=.//form//div[@role='button' and normalize-space(text())='Paylaş']"
            ).first
            if share_btn.count() > 0 and share_btn.is_visible():
                try:
                    share_btn.click(timeout=5000, force=True)
                except Exception:
                    page.evaluate("(el) => el.click()", share_btn)
                page.wait_for_timeout(1200)
                return True
        except Exception:
            continue

    return False


def detect_login_wall(page):
    try:
        login_inputs = page.locator("input[name='username'], input[name='password']")
        login_btns = page.locator("text=/Log in|Giriş yap|Kaydol/i")
        if login_inputs.count() > 0 or login_btns.count() > 0:
            return True
    except Exception:
        pass
    return False


def normalize_hashtags(raw_list):
    seen = set()
    cleaned = []
    for ln in raw_list:
        tag = ln.strip().lstrip("#").strip().lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        cleaned.append(tag)
    return cleaned


def extract_all_comment_usernames(page, dialog):
    users = set()

    scroll_box = dialog.locator("div[role='dialog'] div").nth(0)

    stable_rounds = 0
    last_user_count = 0

    for i in range(500):  # təhlükəsiz limit
        # 1️⃣ "Daha çox şərh" varsa kliklə
        try:
            more_btn = dialog.locator("text=/Daha çox|View more|Load more|tümünü gör/i")
            if more_btn.count() > 0:
                more_btn.first.click(timeout=1500)
                page.wait_for_timeout(2500)
        except:
            pass

        # 2️⃣ BÜTÜN username-ləri oxu
        anchors = dialog.locator("a[href^='/']").all()
        for a in anchors:
            href = (a.get_attribute("href") or "").strip()
            if not href or href.count("/") < 2:
                continue
            username = href.split("/")[1].lower()
            if username in {"reel", "reels", "p", "explore", "accounts", "direct"}:
                continue
            users.add(username)

        # 3️⃣ Scroll ET
        prev_height = scroll_box.evaluate("el => el.scrollHeight")
        scroll_box.evaluate("el => el.scrollTop = el.scrollHeight")
        page.wait_for_timeout(2500)
        new_height = scroll_box.evaluate("el => el.scrollHeight")

        # 4️⃣ REAL STOP ŞƏRTİ
        if len(users) == last_user_count and new_height == prev_height:
            stable_rounds += 1
        else:
            stable_rounds = 0
            last_user_count = len(users)

        # 5️⃣ YALNIZ 5 DƏFƏ ARD-ARDA STABİLDİRSƏ DAYAN
        if stable_rounds >= 5:
            break

    return sorted(users)


# ================== CORE: SMART DM LOOP ==================
def safe_wait(seconds):
    start = time.time()
    while time.time() - start < seconds:
        if dm_stop_flag.is_set():
            return False
        while dm_pause_flag.is_set():
            time.sleep(0.2)
            if dm_stop_flag.is_set():
                return False
        time.sleep(0.2)
    return True


# ================== SEND BUTTON HELPER ==================
def click_send_button(page):
    send_btns = page.locator(
        "div[role='button']:has-text('Gönder'), "
        "div[role='button']:has-text('Send'), "
        "button:has-text('Gönder'), "
        "button:has-text('Send'), "
        "[role='button'][aria-label='Gönder'], "
        "[role='button'][aria-label='Send'], "
        "div[role='button']:has(svg[aria-label='Send'])"
    )

    send_btns.wait_for(timeout=10000)
    btn = send_btns.last

    try:
        btn.scroll_into_view_if_needed()
    except:
        pass

    page.wait_for_timeout(200)

    try:
        btn.click(force=True)
    except:
        page.evaluate("(el) => el.click()", btn)

    page.wait_for_timeout(800)


# ================== SEND DM ==================
def send_dm_to_user(page, username, message_text):
    try:
        page.goto(
            f"https://www.instagram.com/{username}/",
            timeout=90000,
            wait_until="domcontentloaded",
        )
        page.wait_for_timeout(3500)

        # 1️⃣ Ana profildə "Mesaj Gönder"
        main_msg_btn = page.locator(
            "div[role='button']:has-text('Mesaj Gönder'), "
            "button:has-text('Mesaj Gönder'), "
            "div[role='button']:has-text('Message'), "
            "button:has-text('Message')"
        )

        if main_msg_btn.count() > 0:
            main_msg_btn.first.click(force=True)
            page.wait_for_timeout(3000)
        else:
            # 2️⃣ 3 nöqtə → Mesaj Gönder
            options_svg = page.locator(
                "svg[aria-label*='Seçenekler' i], "
                "svg[aria-label*='Options' i], "
                "svg[aria-label*='More' i]"
            ).first

            options_svg.wait_for(timeout=15000)
            options_svg.click(force=True)
            page.wait_for_timeout(800)

            dlg = page.locator("div[role='dialog']").first
            dlg.wait_for(timeout=10000)

            modal_btn = dlg.locator(
                "button:has-text('Mesaj Gönder'), "
                "button:has-text('Message'), "
                "div[role='button']:has-text('Mesaj Gönder'), "
                "div[role='button']:has-text('Message')"
            ).first

            modal_btn.wait_for(timeout=15000)
            modal_btn.click(force=True)
            page.wait_for_timeout(3000)

        # 3️⃣ Mesaj yaz
        textbox = page.locator("div[contenteditable='true'][role='textbox']").first
        textbox.wait_for(timeout=20000)
        textbox.click()
        page.wait_for_timeout(200)

        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        page.keyboard.insert_text(message_text)
        page.wait_for_timeout(300)

        # 4️⃣ Göndər
        try:
            click_send_button(page)
        except:
            page.keyboard.press("Enter")
            page.wait_for_timeout(800)

        return True, "Mesaj göndərildi"

    except Exception as e:
        return False, str(e)


# ================== SEND DM ==================
def send_dm_to_user(page, username, message_text):
    try:
        page.goto(
            f"https://www.instagram.com/{username}/",
            timeout=90000,
            wait_until="domcontentloaded",
        )
        page.wait_for_timeout(3500)

        # 1️⃣ Ana profildə "Mesaj Gönder"
        main_msg_btn = page.locator(
            "div[role='button']:has-text('Mesaj Gönder'), "
            "button:has-text('Mesaj Gönder'), "
            "div[role='button']:has-text('Message'), "
            "button:has-text('Message')"
        )

        if main_msg_btn.count() > 0:
            main_msg_btn.first.click(force=True)
            page.wait_for_timeout(3000)
        else:
            # 2️⃣ 3 nöqtə → Mesaj Gönder
            options_svg = page.locator(
                "svg[aria-label*='Seçenekler' i], "
                "svg[aria-label*='Options' i], "
                "svg[aria-label*='More' i]"
            ).first

            options_svg.wait_for(timeout=15000)
            options_svg.click(force=True)
            page.wait_for_timeout(800)

            dlg = page.locator("div[role='dialog']").first
            dlg.wait_for(timeout=10000)

            modal_btn = dlg.locator(
                "button:has-text('Mesaj Gönder'), "
                "button:has-text('Message'), "
                "div[role='button']:has-text('Mesaj Gönder'), "
                "div[role='button']:has-text('Message')"
            ).first

            modal_btn.wait_for(timeout=15000)
            modal_btn.click(force=True)
            page.wait_for_timeout(3000)

        # 3️⃣ Mesaj yaz
        textbox = page.locator("div[contenteditable='true'][role='textbox']").first
        textbox.wait_for(timeout=20000)
        textbox.click()
        page.wait_for_timeout(200)

        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        page.keyboard.insert_text(message_text)
        page.wait_for_timeout(300)

        # 4️⃣ Göndər
        try:
            click_send_button(page)
        except:
            page.keyboard.press("Enter")
            page.wait_for_timeout(800)

        return True, "Mesaj göndərildi"

    except Exception as e:
        return False, str(e)


def comment_single_post(page, href, anchor, comment_text):
    if dm_stop_flag.is_set():
        return False, "stop"

    try:
        try:
            anchor.scroll_into_view_if_needed()
        except Exception:
            pass
        try:
            anchor.click(force=True)
        except Exception:
            page.goto(href, timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

        page.wait_for_timeout(1200)

        root = post_comment_overlay(page)

        try:
            comment_icon = root.locator(
                "svg[aria-label='Yorum Yap'], "
                "svg[aria-label*='yorum' i], "
                "svg[aria-label*='comment' i], "
                "div[role='button']:has(svg[aria-label*='yorum' i])"
            ).first
            if comment_icon.count() > 0:
                comment_icon.click(timeout=8000, force=True)
                page.wait_for_timeout(600)
        except Exception:
            pass

        textarea = root.locator(
            "textarea[aria-label*='Yorum ekle' i], textarea[placeholder*='Yorum ekle' i]"
        ).first
        textarea.wait_for(timeout=15000)
        textarea.click()
        page.wait_for_timeout(150)

        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        page.keyboard.insert_text(comment_text)
        page.wait_for_timeout(200)

        submitted = submit_comment_from_textarea(page, root, textarea)
        if not submitted:
            return False, "submit_failed"

        close_post_overlay(page)
        if re.search(r"/(p|reel)/", page.url):
            page.goto("https://www.instagram.com/", timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
        return True, "ok"
    except Exception as e:
        close_post_overlay(page)
        if re.search(r"/(p|reel)/", page.url):
            try:
                page.goto(
                    "https://www.instagram.com/", timeout=60000, wait_until="domcontentloaded"
                )
                page.wait_for_timeout(1200)
            except Exception:
                pass
        return False, str(e)


def dm_send_loop(page, params):
    msg = params["msg"]
    min_delay = params["min_delay"]
    max_delay = params["max_delay"]
    break_every = params["break_every"]
    break_minutes = params["break_minutes"]
    get_users = params["get_users"]

    sent_count = 0

    result_queue.put(("LOG", "📨 DM başladı\n"))

    while True:
        users = get_users()
        if not users:
            result_queue.put(
                ("LOG", "🎉 DM prosesi bitdi (bütün userlər göndərildi)\n")
            )
            return

        username = users[0]

        if dm_stop_flag.is_set():
            result_queue.put(("LOG", "🛑 DM dayandırıldı\n"))
            return

        ok, info = send_dm_to_user(page, username, msg)

        if ok:
            append_line(SENT_FILE, username)
            result_queue.put(("SENT_OK", username))
            sent_count += 1
        else:
            result_queue.put(("LOG", f"⚠️ @{username} → {info}\n"))
            result_queue.put(("SENT_OK", username))  # ilişməsin, siyahıdan çıxsın

        delay = random.randint(min_delay, max_delay)
        result_queue.put(("LOG", f"⏳ Növbəti user üçün gözləmə: {delay}s\n"))
        if not safe_wait(delay):
            return

        if break_every > 0 and sent_count % break_every == 0:
            result_queue.put(("LOG", f"🧠 Anti-spam fasilə: {break_minutes} dəq\n"))
            if not safe_wait(break_minutes * 60):
                return

        # ===============================
        # SEND BUTTON CLICK (ROBUST)
        # ===============================
        send_btn = page.locator(
            "div[role='button'][aria-label='Gönder'], "
            "div[role='button'][aria-label='Send'], "
            "div[role='button']:has(svg[aria-label='Send'])"
        )

        if send_btn.count() > 0:
            try:
                send_btn.first.click(force=True)
            except:
                # JS fallback
                page.evaluate("(el) => el.click()", send_btn.first)
        else:
            # son çarə
            page.keyboard.press("Enter")


def hashtag_comment_loop(page, params):
    hashtags = normalize_hashtags(params["hashtags"])
    comment_variants = params["comments"]
    min_delay = params["min_delay"]
    max_delay = params["max_delay"]
    break_every = params["break_every"]
    break_minutes = params["break_minutes"]

    commented_ids = load_set(COMMENTED_POSTS_FILE)
    commented_count = len(commented_ids)
    made_comments = 0

    if not hashtags:
        result_queue.put(("ERROR", "Hashtag siyahısı boşdur"))
        return

    result_queue.put(("LOG", "🗨 Hashtag komment prosesi başladı\n"))

    for tag in hashtags:
        if dm_stop_flag.is_set():
            break

        result_queue.put(("LOG", f"🏷 Hashtag: #{tag}\n"))
        try:
            page.goto(
                f"https://www.instagram.com/explore/search/keyword/?q=%23{tag}",
                timeout=90000,
                wait_until="domcontentloaded",
            )
            page.wait_for_timeout(2500)
        except Exception as e:
            result_queue.put(("ERROR", f"{tag}: naviqasiya alınmadı → {e}"))
            continue

        if detect_login_wall(page):
            result_queue.put(("ERROR", "🔒 Login tələb olunur, proses dayandırıldı"))
            break

        seen_shortcodes = set()
        idle_rounds = 0

        while not dm_stop_flag.is_set():
            while dm_pause_flag.is_set() and not dm_stop_flag.is_set():
                time.sleep(0.2)

            posts = collect_search_results_posts(page, commented_ids, seen_shortcodes)

            if not posts:
                idle_rounds += 1
                try:
                    page.mouse.wheel(0, 1600)
                except Exception:
                    page.evaluate("() => window.scrollBy(0, window.innerHeight * 0.9)")
                page.wait_for_timeout(1400)
                if idle_rounds > 18:
                    break
                continue

            idle_rounds = 0

            for shortcode, href, anchor in posts:
                seen_shortcodes.add(shortcode)

                comment_text = random.choice(comment_variants)
                ok, info = comment_single_post(page, href, anchor, comment_text)

                if ok:
                    commented_ids.add(shortcode)
                    append_line(COMMENTED_POSTS_FILE, shortcode)
                    made_comments += 1
                    commented_count += 1
                    result_queue.put(("LOG", f"✅ {shortcode} → yorum göndərildi\n"))
                    result_queue.put(("COMMENT_PROGRESS", commented_count))
                else:
                    result_queue.put(("LOG", f"⚠️ {shortcode} keçildi: {info}\n"))

                delay = random.randint(min_delay, max_delay)
                result_queue.put(("LOG", f"⏳ Növbəti post üçün gözləmə: {delay}s\n"))
                if not safe_wait(delay):
                    return

                if break_every > 0 and made_comments > 0 and made_comments % break_every == 0:
                    result_queue.put(("LOG", f"🧠 Anti-spam fasilə: {break_minutes} dəq\n"))
                    if not safe_wait(break_minutes * 60):
                        return

            try:
                page.mouse.wheel(0, 1600)
            except Exception:
                page.evaluate("() => window.scrollBy(0, window.innerHeight * 0.9)")
            page.wait_for_timeout(1400)

        if dm_stop_flag.is_set():
            break

        extra_break = random.randint(120, 300)
        result_queue.put(("LOG", f"⏳ Hashtag fasiləsi: {extra_break}s\n"))
        if not safe_wait(extra_break):
            return

    result_queue.put(("LOG", "🛑 Hashtag komment prosesi bitdi\n"))


def feed_comment_loop(page, params):
    comment_variants = params["comments"]
    min_delay = params["min_delay"]
    max_delay = params["max_delay"]
    break_every = params["break_every"]
    break_minutes = params["break_minutes"]

    commented_ids = load_set(COMMENTED_POSTS_FILE)
    commented_count = len(commented_ids)
    made_comments = 0

    result_queue.put(("LOG", "🗨 Feed komment proses başladı\n"))

    page.goto("https://www.instagram.com/", timeout=90000, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)

    while not dm_stop_flag.is_set():
        while dm_pause_flag.is_set() and not dm_stop_flag.is_set():
            time.sleep(0.2)

        posts = collect_visible_feed_posts(page, commented_ids)

        if not posts:
            try:
                page.mouse.wheel(0, 1400)
            except Exception:
                page.evaluate("() => window.scrollBy(0, window.innerHeight * 0.9)")
            page.wait_for_timeout(1600)
            continue

        for shortcode, href, anchor in posts:
            if dm_stop_flag.is_set():
                break

            while dm_pause_flag.is_set() and not dm_stop_flag.is_set():
                time.sleep(0.2)

            comment_text = random.choice(comment_variants)
            ok, info = comment_single_post(page, href, anchor, comment_text)

            if ok:
                commented_ids.add(shortcode)
                append_line(COMMENTED_POSTS_FILE, shortcode)
                made_comments += 1
                commented_count += 1
                result_queue.put(("LOG", f"✅ {shortcode} → yorum göndərildi\n"))
                result_queue.put(("COMMENT_PROGRESS", commented_count))
            else:
                result_queue.put(("LOG", f"⚠️ {shortcode} keçildi: {info}\n"))

            delay = random.randint(min_delay, max_delay)
            result_queue.put(("LOG", f"⏳ Növbəti post üçün gözləmə: {delay}s\n"))
            if not safe_wait(delay):
                return

            if break_every > 0 and made_comments > 0 and made_comments % break_every == 0:
                result_queue.put(("LOG", f"🧠 Anti-spam fasilə: {break_minutes} dəq\n"))
                if not safe_wait(break_minutes * 60):
                    return

        try:
            page.mouse.wheel(0, 1600)
        except Exception:
            page.evaluate("() => window.scrollBy(0, window.innerHeight * 0.9)")
        page.wait_for_timeout(1400)

    result_queue.put(("LOG", "🛑 Feed komment prosesi dayandırıldı\n"))


# ================== SHUTDOWN ==================
def shutdown():
    global playwright, context
    try:
        if context:
            context.close()
        if playwright:
            playwright.stop()
    except:
        pass


atexit.register(shutdown)

# ================== GUI ==================
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

app = ctk.CTk()
app.title("Instagram Comment → Username Tool — BestHome")
app.geometry("500x700")

left = ctk.CTkFrame(app, width=320)
left.pack(side="left", fill="y", padx=10, pady=10)

ctk.CTkLabel(left, text="Post / Reel Linki").pack(anchor="w")
post_entry = ctk.CTkEntry(left)
post_entry.pack(fill="x", pady=5)

btn_preview = ctk.CTkButton(left, text="🔍 Comment yazanları çıxar")
btn_preview.pack(fill="x", pady=6)

ctk.CTkButton(
    left, text="💾 Username-ləri TXT saxla", command=lambda: on_save_users()
).pack(fill="x", pady=6)

ctk.CTkLabel(left, text="DM Mesajı (1 mətn)").pack(anchor="w", pady=(10, 0))
msg_box = ctk.CTkTextbox(left, height=100)
msg_box.pack(fill="x", pady=5)

ctk.CTkLabel(left, text="Feed komment mətni (sətir-sətir)").pack(anchor="w")
comment_box = ctk.CTkTextbox(left, height=120)
comment_box.pack(fill="x", pady=5)

ctk.CTkLabel(left, text="Hashtaglər (1 sətir = 1 hashtag)").pack(anchor="w")
hashtag_box = ctk.CTkTextbox(left, height=100)
hashtag_box.pack(fill="x", pady=5)

ctk.CTkLabel(left, text="User arası interval (saniyə)").pack(anchor="w")
dm_min = ctk.CTkEntry(left)
dm_min.insert(0, "15")
dm_min.pack(fill="x", pady=2)

dm_max = ctk.CTkEntry(left)
dm_max.insert(0, "45")
dm_max.pack(fill="x", pady=2)

ctk.CTkLabel(left, text="Böyük fasilə").pack(anchor="w", pady=(10, 0))
break_every = ctk.CTkEntry(left)
break_every.insert(0, "30")
break_every.pack(fill="x", pady=2)

break_minutes = ctk.CTkEntry(left)
break_minutes.insert(0, "30")
break_minutes.pack(fill="x", pady=2)

actions_row = ctk.CTkFrame(left)
actions_row.pack(fill="x", pady=(12, 6))

btn_dm_start = ctk.CTkButton(actions_row, text="📨 DM Başlat")
btn_dm_start.pack(side="left", expand=True, fill="x", padx=(0, 4))

btn_feed_comment = ctk.CTkButton(actions_row, text="🗨 Feed Comment Başlat")
btn_feed_comment.pack(side="left", expand=True, fill="x", padx=(4, 0))

hashtag_row = ctk.CTkFrame(left)
hashtag_row.pack(fill="x", pady=(4, 6))

btn_hashtag_comment = ctk.CTkButton(hashtag_row, text="🗨 Hashtag Comment Başlat")
btn_hashtag_comment.pack(expand=True, fill="x")

btn_pause = ctk.CTkButton(left, text="⏸ Pause", fg_color="#444444")
btn_pause.pack(fill="x", pady=4)

btn_resume = ctk.CTkButton(left, text="▶️ Resume", fg_color="#2b6a2b")
btn_resume.pack(fill="x", pady=4)

btn_stop = ctk.CTkButton(left, text="🛑 Stop", fg_color="#7a2b2b")
btn_stop.pack(fill="x", pady=4)

right = ctk.CTkFrame(app)
right.pack(side="right", expand=True, fill="both", padx=10, pady=10)

users_count_lbl = ctk.CTkLabel(right, text="Tapılan username-lər (0 qaldı)")
users_count_lbl.pack(anchor="w", pady=(0, 4))

user_list = ctk.CTkTextbox(right, font=("Consolas", 12))
user_list.pack(expand=True, fill="both", pady=5)

log = ctk.CTkTextbox(right, height=160)
log.pack(fill="x")

progress_lbl = ctk.CTkLabel(right, text="DM Progress: 0/0")
progress_lbl.pack(anchor="w", pady=(6, 0))


def gui_log(text):
    log.insert("end", text)
    log.see("end")


def refresh_users_box(users):
    user_list.delete("1.0", "end")
    for u in users:
        user_list.insert("end", f"@{u}\n")
    users_count_lbl.configure(text=f"Tapılan username-lər ({len(users)} qaldı)")


def current_users_from_gui():
    lines = user_list.get("1.0", "end").strip().splitlines()
    out = []
    for ln in lines:
        ln = ln.strip().lstrip("@").strip()
        if ln:
            out.append(ln.lower())
    return out


def on_save_users():
    users = current_users_from_gui()
    save_users_txt(users)
    gui_log(f"💾 users_saved.txt saxlanıldı: {len(users)} user\n")


def manual_import():
    path = filedialog.askopenfilename(
        title="TXT fayl seç", filetypes=[("Text files", "*.txt")]
    )
    if not path:
        return

    sent = load_set(SENT_FILE)
    blacklist = load_set(BLACKLIST_FILE)

    imported = []
    skipped = 0

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            u = line.strip().lstrip("@").lower()
            if not u:
                continue
            if u in sent or u in blacklist:
                skipped += 1
                continue
            imported.append(u)

    unique_users = list(dict.fromkeys(imported))

    global saved_users
    saved_users = unique_users

    refresh_users_box(saved_users)
    gui_log(f"📥 TXT import edildi: {len(saved_users)} user | skip: {skipped}\n")
    progress_lbl.configure(text=f"DM Progress: 0/{len(saved_users)}")


ctk.CTkButton(left, text="📥 txt import et", command=manual_import).pack(
    fill="x", pady=5
)

loaded, count = load_sent_users(SENT_FILE)
if loaded:
    gui_log(f"📥 sent_users.txt yükləndi ({count} istifadəçi)\n")
else:
    gui_log("ℹ️ sent_users.txt tapılmadı (yeni başlayırıq)\n")

# ================== GUI ACTIONS ==================
polling_active = False


def preview_users():
    global polling_active, saved_users

    user_list.delete("1.0", "end")
    log.delete("1.0", "end")

    url = post_entry.get().strip()
    if not url:
        gui_log("❌ Link boşdur\n")
        return

    gui_log("🚀 Proses başladı (comment yüklənir)...\n")
    task_queue.put({"type": "preview", "url": url})

    if not polling_active:
        polling_active = True
        app.after(300, poll_results)


def poll_results():
    global polling_active, saved_users

    while not result_queue.empty():
        kind, *payload = result_queue.get_nowait()

        if kind == "USERS":
            users = payload[0]
            saved_users = users[:]
            refresh_users_box(saved_users)
            gui_log(f"✅ {len(users)} istifadəçi tapıldı (filtrdən sonra)\n")
            progress_lbl.configure(text=f"DM Progress: 0/{len(saved_users)}")
            polling_active = False

        elif kind == "ERROR":
            gui_log(f"❌ Xəta: {payload[0]}\n")

        elif kind == "LOG":
            gui_log(payload[0])

        elif kind == "PROGRESS":
            idx, total = payload
            progress_lbl.configure(text=f"DM Progress: {idx}/{total}")

        elif kind == "COMMENT_PROGRESS":
            count = payload[0]
            progress_lbl.configure(text=f"Comment Progress: {count}")

        elif kind == "SENT_OK":
            username = payload[0]

            if username in saved_users:
                saved_users.remove(username)
                refresh_users_box(saved_users)

            sent_count = len(load_set(SENT_FILE))
            total = sent_count + len(saved_users)

            gui_log(f"✅ @{username} → mesaj göndərildi ({sent_count}/{total})\n")
            progress_lbl.configure(text=f"DM Progress: {sent_count}/{total}")

    app.after(300, poll_results)


def dm_start():
    users = current_users_from_gui()
    if not users:
        gui_log("❌ DM üçün siyahı boşdur (əvvəlcə user çıxart / import et)\n")
        return

    msg = msg_box.get("1.0", "end").strip()
    if not msg:
        gui_log("❌ DM mesajı boşdur\n")
        return

    try:
        min_d = int(dm_min.get().strip())
        max_d = int(dm_max.get().strip())
        be = int(break_every.get().strip())
        bm = int(break_minutes.get().strip())
    except:
        gui_log("❌ Interval / fasilə dəyərləri səhvdir\n")
        return

    if min_d < 5 or max_d < min_d:
        gui_log("❌ Interval düzgün deyil (min>=5, max>=min)\n")
        return

    dm_stop_flag.clear()
    dm_pause_flag.clear()

    prog = load_progress()
    start_index = int(prog.get("index", 0))
    if start_index < 0 or start_index > len(users):
        start_index = 0

    gui_log(f"📌 DM başlayır. Qaldığı yer: index={start_index}\n")
    progress_lbl.configure(text=f"DM Progress: {start_index}/{len(users)}")

    params = {
        "msg": msg,
        "min_delay": min_d,
        "max_delay": max_d,
        "break_every": be,
        "break_minutes": bm,
        "get_users": lambda: current_users_from_gui(),
    }
    task_queue.put({"type": "dm_start", "params": params})


def feed_comment_start():
    comments = [ln.strip() for ln in comment_box.get("1.0", "end").splitlines() if ln.strip()]
    if not comments:
        gui_log("❌ Comment mətni boşdur\n")
        return

    try:
        min_d = int(dm_min.get().strip())
        max_d = int(dm_max.get().strip())
        be = int(break_every.get().strip())
        bm = int(break_minutes.get().strip())
    except Exception:
        gui_log("❌ Interval / fasilə dəyərləri səhvdir\n")
        return

    if min_d < 5 or max_d < min_d:
        gui_log("❌ Interval düzgün deyil (min>=5, max>=min)\n")
        return

    dm_stop_flag.clear()
    dm_pause_flag.clear()

    gui_log("📌 Feed komment başlayır\n")
    progress_lbl.configure(text="Comment Progress: ...")

    params = {
        "comments": comments,
        "min_delay": min_d,
        "max_delay": max_d,
        "break_every": be,
        "break_minutes": bm,
    }
    task_queue.put({"type": "feed_comment", "params": params})


def hashtag_comment_start():
    comments = [ln.strip() for ln in comment_box.get("1.0", "end").splitlines() if ln.strip()]
    hashtags = [ln for ln in hashtag_box.get("1.0", "end").splitlines()]

    if not comments:
        gui_log("❌ Comment mətni boşdur\n")
        return

    hashtags = normalize_hashtags(hashtags)
    if not hashtags:
        gui_log("❌ Hashtag siyahısı boşdur\n")
        return

    try:
        min_d = int(dm_min.get().strip())
        max_d = int(dm_max.get().strip())
        be = int(break_every.get().strip())
        bm = int(break_minutes.get().strip())
    except Exception:
        gui_log("❌ Interval / fasilə dəyərləri səhvdir\n")
        return

    if min_d < 5 or max_d < min_d:
        gui_log("❌ Interval düzgün deyil (min>=5, max>=min)\n")
        return

    dm_stop_flag.clear()
    dm_pause_flag.clear()

    gui_log("📌 Hashtag komment başlayır\n")
    progress_lbl.configure(text="Comment Progress: ...")

    params = {
        "hashtags": hashtags,
        "comments": comments,
        "min_delay": min_d,
        "max_delay": max_d,
        "break_every": be,
        "break_minutes": bm,
    }
    task_queue.put({"type": "hashtag_comment", "params": params})


def dm_pause():
    dm_pause_flag.set()
    gui_log("⏸ Pause edildi\n")


def dm_resume():
    dm_pause_flag.clear()
    gui_log("▶️ Resume edildi\n")


def dm_stop():
    dm_stop_flag.set()
    dm_pause_flag.clear()
    gui_log("🛑 Stop siqnalı göndərildi\n")


btn_preview.configure(command=preview_users)
btn_dm_start.configure(command=dm_start)
btn_feed_comment.configure(command=feed_comment_start)
btn_hashtag_comment.configure(command=hashtag_comment_start)
btn_pause.configure(command=dm_pause)
btn_resume.configure(command=dm_resume)
btn_stop.configure(command=dm_stop)

# ================== START WORKER ==================
threading.Thread(target=playwright_worker, daemon=True).start()
app.after(300, poll_results)

app.mainloop()
