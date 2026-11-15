"""The member's colours arrive as a stylesheet linked from <head>.

A fetch at the end of the script can only repaint after the first frame, so a cold load
showed the stock theme and then changed. The link is parsed with the rest of the head,
which is the difference between "the theme is right" and "the theme becomes right".
"""
from pathlib import Path
from unittest import mock

import preferences
from test_listening import ListeningHTTP

PAGE = Path(__file__).resolve().parent.parent / "web" / "index.html"


class ThemeCSS(ListeningHTTP):
    def css(self, cookie=None):
        return self.request("/theme.css", headers={"Cookie": cookie} if cookie else {})

    def test_a_members_saved_colours_are_the_sheet(self):
        preferences.set_theme(self.store, "u1",
                              {"base": "#123456", "accent": "#abcdef", "trim": "#fedcba"})
        code, headers, raw = self.css(self.token("u1"))
        self.assertEqual(code, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/css"))
        # Only the inputs: tokens.css mixes panel/panel2/line from these itself.
        self.assertEqual(
            raw.decode().strip(),
            ":root:root{--user-base:#123456;--user-accent:#abcdef;--user-trim:#fedcba}")
        self.assertIn("no-store", headers["Cache-Control"])

    def test_one_sheet_per_member_not_per_browser_tab(self):
        preferences.set_theme(self.store, "u1",
                              {"base": "#111111", "accent": "#222222", "trim": "#333333"})
        self.assertIn(b"--user-base:#111111", self.css(self.token("u1"))[2])
        # Another member's own colours, and the stock three for a stranger.
        self.assertIn(b"--user-base:#101014", self.css(self.token("u2"))[2])

    def test_anonymous_and_stale_sessions_get_the_defaults_without_failing(self):
        for cookie in (None, "", "__Host-hl_session=not-a-session"):
            code, headers, raw = self.css(cookie)
            self.assertEqual(code, 200)
            self.assertEqual(headers["Content-Type"], "text/css; charset=utf-8")
            self.assertEqual(
                raw.decode().strip(),
                ":root:root{--user-base:#101014;--user-accent:#58d3a8;--user-trim:#343641}")

    def test_it_never_spends_the_sign_in_budget(self):
        # `/theme.css` is fetched by the browser itself on every cold load. Counting that
        # against the verification budget would let a refresh loop lock the login out.
        with mock.patch.object(self.sec, "limit",
                               side_effect=AssertionError("verification budget spent")):
            self.assertEqual(self.css("__Host-hl_session=not-a-session")[0], 200)
            self.assertEqual(self.css(self.token("u1"))[0], 200)

    def test_the_page_links_it_before_any_script(self):
        head = PAGE.read_text().split("</head>")[0]
        self.assertIn('href="/theme.css"', head)
        self.assertLess(head.index('href="/theme.css"'), head.index("<script"))