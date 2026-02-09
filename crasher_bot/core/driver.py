"""Browser automation – login, navigation, and game interaction."""

import logging
import re
import time
from typing import List, Optional

try:
    import undetected_chromedriver as uc
    UC_AVAILABLE = True
except ImportError:
    UC_AVAILABLE = False

from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)

# ── JavaScript snippets ─────────────────────────────────────────────

JS_READ_MULTIPLIERS = """
var items = document.querySelectorAll('span.sc-w0koce-1.giBFzM');
var mults = [];
for (var i = 0; i < items.length; i++) {
    var t = items[i].textContent.trim();
    if (t.endsWith('x')) {
        var v = parseFloat(t.replace('x', ''));
        if (!isNaN(v)) mults.push(v);
    }
}
return mults.reverse();
"""

JS_DETECT_MULTIPLIER = """
var el = document.querySelector('span.ZmRXV');
if (!el) return null;
var text = el.textContent.trim();
var cls = el.className;
var ended = cls.includes('false');
var betBtn = document.querySelector('button[data-testid="b-btn"]');
var canBet = betBtn && betBtn.textContent.toLowerCase().includes('bet');
if (!(ended && canBet)) return null;
return text;
"""

JS_CLICK_AUTO = """
try {
    var panel = document.querySelectorAll('div[data-singlebetpart]')[0];
    var btns = panel.querySelectorAll('button');
    for (var i = 0; i < btns.length; i++) {
        var b = btns[i];
        if (b.offsetParent === null) continue;
        var t = b.textContent.trim().toLowerCase();
        if (t === 'auto') { b.click(); return true; }
        if (t === 'stop') return true;
    }
    return false;
} catch(e) { return false; }
"""

JS_TOGGLE_CASHOUT = """
try {
    var panel = document.querySelectorAll('div[data-singlebetpart]')[0];
    var tgl = panel.querySelector('input[data-testid="aut-co-tgl"]');
    if (tgl && !tgl.checked) tgl.click();
    return tgl !== null;
} catch(e) { return false; }
"""

JS_BETTOR_COUNT = """
var s = document.querySelector('span[data-testid="b-ct-spn"]');
return s ? s.textContent : null;
"""

JS_BALANCE = """
var d = document.getElementById('lblBalance');
return d ? d.textContent : null;
"""

JS_CLOSE_TUTORIAL = """
var btns = document.getElementsByClassName('Qthei');
if (btns.length > 0) { btns[0].click(); return true; }
return false;
"""

JS_VISIBLE_BUTTONS = """
var all = document.querySelectorAll('button');
var vis = [];
for (var i = 0; i < all.length; i++) {
    if (all[i].offsetParent !== null) vis.push({text: all[i].textContent.trim()});
}
return vis;
"""


class GameDriver:
    """Wraps Selenium/undetected-chromedriver for game interaction."""

    def __init__(self):
        if not UC_AVAILABLE:
            raise RuntimeError("undetected-chromedriver is not installed")
        self.driver = None
        self.wait = None

    # ── Lifecycle ───────────────────────────────────────────────────

    def start(self) -> bool:
        try:
            opts = uc.ChromeOptions()
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument("--window-size=1920,1080")
            opts.add_argument("--enable-webgl")
            opts.add_argument("--disable-extensions")
            self.driver = uc.Chrome(options=opts, version_main=143, use_subprocess=True)
            self.driver.set_page_load_timeout(60)
            self.driver.implicitly_wait(10)
            self.driver.set_script_timeout(15)
            self.wait = WebDriverWait(self.driver, 30)
            logger.info("Chrome driver initialized")
            return True
        except Exception as e:
            logger.error("Driver init failed: %s", e)
            return False

    def quit(self):
        if self.driver:
            self.driver.quit()
            self.driver = None

    # ── Auth & navigation ───────────────────────────────────────────

    def login(self, username: str, password: str) -> bool:
        try:
            self.driver.get("https://1000bet.in")
            time.sleep(5)
            if "cloudflare" in self.driver.page_source.lower():
                logger.warning("Cloudflare detected – waiting…")
                time.sleep(10)

            btn = self.wait.until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, 'a.loginDialog[automation="home_login_button"]')
                )
            )
            btn.click()
            time.sleep(2)

            email_inp = self.wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, 'input[automation="email_input"]')
                )
            )
            pw_inp = self.driver.find_element(
                By.CSS_SELECTOR, 'input[automation="password_input"]'
            )

            self._type_slowly(email_inp, username)
            self._type_slowly(pw_inp, password)

            self.driver.find_element(
                By.CSS_SELECTOR, 'button[automation="login_button"]'
            ).click()
            time.sleep(5)
            logger.info("Login successful")
            return True
        except Exception as e:
            logger.error("Login failed: %s", e)
            return False

    def navigate_to_game(self, url: str) -> bool:
        try:
            self.driver.get(url)
            time.sleep(5)
            WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.TAG_NAME, "iframe"))
            )
            iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
            game_iframe = next(
                (f for f in iframes if (f.get_attribute("src") or "") and len(f.get_attribute("src")) > 50),
                None,
            )
            if not game_iframe:
                logger.error("Game iframe not found")
                return False

            self.driver.switch_to.frame(game_iframe)
            time.sleep(5)

            nested = self.driver.find_elements(By.TAG_NAME, "iframe")
            if nested:
                self.driver.switch_to.frame(nested[0])
                time.sleep(3)

            self._wait_for_content()
            self._close_tutorial()
            logger.info("Game loaded")
            return True
        except Exception as e:
            logger.error("Navigation failed: %s", e)
            return False

    # ── Game interactions ───────────────────────────────────────────

    def read_page_multipliers(self) -> List[float]:
        try:
            result = self.driver.execute_script(JS_READ_MULTIPLIERS)
            return result or []
        except Exception:
            return []

    def detect_round_end(self) -> Optional[float]:
        """Returns multiplier if a round just ended, else None."""
        try:
            text = self.driver.execute_script(JS_DETECT_MULTIPLIER)
            if not text:
                return None
            match = re.search(r"(\d+\.?\d*)x", text, re.IGNORECASE)
            if match:
                val = float(match.group(1))
                if 1.0 <= val <= 10000.0:
                    return val
            return None
        except Exception:
            return None

    def get_bettor_count(self) -> Optional[int]:
        try:
            txt = self.driver.execute_script(JS_BETTOR_COUNT)
            return int(txt) if txt and str(txt).strip().isdigit() else None
        except Exception:
            return None

    def get_balance(self) -> Optional[float]:
        try:
            txt = self.driver.execute_script(JS_BALANCE)
            if not txt:
                return None
            cleaned = str(txt).strip().replace("IRT", "").replace(",", "").replace(" ", "")
            return float(cleaned)
        except (ValueError, Exception):
            return None

    def click_multiplier_display(self):
        """Click the multiplier span to keep session alive."""
        try:
            el = self.driver.find_element(By.CSS_SELECTOR, "span.ZmRXV")
            el.click()
        except Exception:
            pass

    def setup_auto_cashout(self, cashout_value: float, max_retries: int = 3) -> bool:
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    time.sleep(2)

                if not self.driver.execute_script(JS_CLICK_AUTO):
                    raise RuntimeError("AUTO button not found")
                time.sleep(0.2)

                self.driver.execute_script(JS_TOGGLE_CASHOUT)
                time.sleep(0.2)

                panels = self.driver.find_elements(By.CSS_SELECTOR, "div[data-singlebetpart]")
                inp = panels[0].find_element(By.CSS_SELECTOR, 'input[data-testid="aut-co-inp"]')
                self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", inp)
                time.sleep(0.1)

                ActionChains(self.driver).move_to_element(inp).click().perform()
                time.sleep(0.1)
                for _ in range(5):
                    inp.send_keys(Keys.BACKSPACE)
                time.sleep(0.1)
                inp.send_keys(str(cashout_value))
                time.sleep(0.1)

                val = float(inp.get_attribute("value"))
                if abs(val - cashout_value) < 0.01:
                    logger.info("Auto-cashout set to %sx", val)
                    return True
            except Exception as e:
                logger.warning("Cashout setup attempt %d failed: %s", attempt + 1, e)
        return False

    def place_bet(self, amount: float) -> bool:
        try:
            panels = self.driver.find_elements(By.CSS_SELECTOR, "div[data-singlebetpart]")
            if not panels:
                return False
            inp = panels[0].find_element(By.CSS_SELECTOR, 'input[data-testid="bp-inp"]')
            inp.click()
            time.sleep(0.1)
            for _ in range(8):
                inp.send_keys(Keys.BACKSPACE)
            time.sleep(0.1)
            inp.send_keys(str(int(amount)))
            time.sleep(0.1)

            panels[0].find_element(By.CSS_SELECTOR, 'button[data-testid="b-btn"]').click()
            time.sleep(0.1)
            logger.info("Bet placed: %d", amount)
            return True
        except Exception as e:
            logger.error("Bet failed: %s", e)
            return False

    # ── Helpers ─────────────────────────────────────────────────────

    def _type_slowly(self, element, text: str, delay: float = 0.05):
        element.clear()
        for ch in text:
            element.send_keys(ch)
            time.sleep(delay)
        time.sleep(0.5)

    def _wait_for_content(self, timeout: int = 40):
        start = time.time()
        last_count, stable = 0, 0
        while time.time() - start < timeout:
            try:
                btns = self.driver.execute_script(JS_VISIBLE_BUTTONS)
                n = len(btns) if btns else 0
                if n > last_count:
                    last_count, stable = n, 0
                elif n == last_count and n > 3:
                    stable += 1
                    if stable >= 3:
                        time.sleep(2)
                        return
            except Exception:
                pass
            time.sleep(1)

    def _close_tutorial(self):
        for _ in range(30):
            try:
                if self.driver.execute_script(JS_CLOSE_TUTORIAL):
                    logger.info("Tutorial popup closed")
                    time.sleep(2)
                    return
            except Exception:
                pass
            time.sleep(1)
