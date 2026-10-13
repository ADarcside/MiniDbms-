import unittest

from minidb.storage import BufferPool, Page, StorageManager
from tests.base import EngineTestCase


class PageStorageTests(EngineTestCase):
    def test_slotted_page_allocation_and_deletion(self):
        page = Page(page_id=1, size=512)
        self.assertEqual(page.record_count, 0)

        # Insert records into page
        slot0 = page.insert_record(rid=1, values={"id": 101, "name": "Alice"})
        slot1 = page.insert_record(rid=2, values={"id": 102, "name": "Bob"})
        self.assertEqual(slot0, 0)
        self.assertEqual(slot1, 1)
        self.assertEqual(page.record_count, 2)

        # Retrieve records
        rec0 = page.get_record(slot0)
        self.assertEqual(rec0, (1, {"id": 101, "name": "Alice"}))

        # Delete record
        self.assertTrue(page.delete_record(slot0))
        self.assertIsNone(page.get_record(slot0))
        self.assertEqual(page.record_count, 1)

    def test_buffer_pool_caching_and_lru_eviction(self):
        pool = BufferPool(capacity=2)
        p1 = Page(page_id=1)
        p2 = Page(page_id=2)
        p3 = Page(page_id=3)

        pool.put_page(p1)
        pool.put_page(p2)
        self.assertIsNotNone(pool.get_page(1))
        self.assertIsNotNone(pool.get_page(2))

        # Adding 3rd page should evict page 1 (since page 2 was accessed last)
        pool.get_page(2)  # access page 2 to make page 1 least recently used
        pool.put_page(p3)
        self.assertIsNone(pool.get_page(1))
        self.assertIsNotNone(pool.get_page(2))
        self.assertIsNotNone(pool.get_page(3))

    def test_storage_manager_crud_interface(self):
        self.seed()
        sm = self.engine.storage
        # Get record by rid
        rec = sm.get_record("college", "students", 1)
        self.assertEqual(rec["name"], "Asha")

        # Update record by rid
        self.assertTrue(sm.update("college", "students", 1, {"id": 1, "name": "Asha Updated", "age": 20}))
        rec_updated = sm.get_record("college", "students", 1)
        self.assertEqual(rec_updated["name"], "Asha Updated")

        # Delete record by rid
        self.assertTrue(sm.delete("college", "students", 1))
        self.assertIsNone(sm.get_record("college", "students", 1))


if __name__ == "__main__":
    unittest.main()
