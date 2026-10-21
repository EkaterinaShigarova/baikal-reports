import unittest
from baikal.acquire import confirmation_url


class Confirmation(unittest.TestCase):
    def test_same_file_only(self):
        form = '<form id="download-form" action="https://drive.usercontent.google.com/download"><input name="id" value="abc"><input name="confirm" value="t"></form>'
        self.assertIn("id=abc", confirmation_url(form, "abc"))
        with self.assertRaises(ValueError):
            confirmation_url(form, "another-file")
        with self.assertRaises(ValueError):
            confirmation_url(
                form.replace("drive.usercontent.google.com", "example.org"), "abc"
            )
        with self.assertRaises(ValueError):
            confirmation_url("<html>Sign in</html>", "abc")
