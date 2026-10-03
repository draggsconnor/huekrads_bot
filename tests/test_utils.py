import unittest
from handlers.utils import is_admin
from config import ADMIN_IDS

class TestAdminFunction(unittest.TestCase):
    def test_is_admin(self):
        # Assuming there is at least one admin ID in ADMIN_IDS
        self.assertTrue(is_admin(list(ADMIN_IDS)[0]), "Test admin ID should return True")
        self.assertFalse(is_admin(-1), "Non-admin ID should return False")

if __name__ == '__main__':
    unittest.main()
