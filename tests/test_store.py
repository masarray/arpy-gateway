import csv
import tempfile
import unittest
from pathlib import Path
from contextlib import closing

from arpy_gateway.studio import store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "work.sqlite"
        store.initialize(self.path)
        store.seed_demo(self.path, 1000)

    def test_seed_and_pages(self):
        self.assertEqual(store.count_rows(self.path, store.View()), 1000)
        first = store.load_page(self.path, store.View(), 0, 256)
        last = store.load_page(self.path, store.View(), 3, 256)
        self.assertEqual(len(first), 256)
        self.assertEqual(len(last), 232)
        self.assertEqual(first[0][1], "Voltage_000001")
        self.assertEqual(last[-1][1], "Status_001000")

    def test_filtered_sorted_edit_targets_original_ids(self):
        view = store.View(search="Voltage", sort_field="name", descending=True)
        self.assertEqual(store.count_rows(self.path, view), 125)
        row = store.load_page(self.path, view, 0, 1)[0]
        self.assertEqual(store.apply_grid(self.path, view, 0, 7, [["9876"]]), 1)
        with closing(store.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT target_address FROM signals WHERE id=?", (row[0],)).fetchone()[0], "9876")

    def test_rectangular_paste_undo_redo(self):
        edits = [["Renamed_1", "Modbus RTU"], ["Renamed_2", "Modbus TCP"]]
        self.assertEqual(store.apply_grid(self.path, store.View(), 0, 0, edits), 3)
        rows = store.load_page(self.path, store.View(), 0, 2)
        self.assertEqual(rows[0][1], "Renamed_1")
        count, label = store.history_step(self.path, True)
        self.assertEqual(count, 3)
        self.assertEqual(store.load_page(self.path, store.View(), 0, 1)[0][1], "Voltage_000001")
        self.assertEqual(store.history_step(self.path, False)[0], 3)
        self.assertEqual(store.load_page(self.path, store.View(), 0, 1)[0][1], "Renamed_1")

    def test_invalid_paste_is_atomic(self):
        with self.assertRaisesRegex(ValueError, "expected a number"):
            store.apply_grid(self.path, store.View(), 0, 8, [["2.5"], ["not-a-number"]])
        self.assertEqual(store.load_page(self.path, store.View(), 0, 2)[0][9], 1.0)
        self.assertEqual(store.history_step(self.path, True)[0], 0)

    def test_undo_redo_branch(self):
        view = store.View()
        store.apply_grid(self.path, view, 0, 10, [["kW"]])
        store.apply_grid(self.path, view, 0, 10, [["Hz"]])
        store.history_step(self.path, True)
        store.apply_grid(self.path, view, 0, 10, [["A"]])
        self.assertEqual(store.history_step(self.path, False)[0], 0)
        self.assertEqual(store.load_page(self.path, view, 0, 1)[0][11], "A")

    def test_fill_and_auto_number(self):
        view = store.View()
        self.assertEqual(store.auto_number(self.path, view, 0, 4, "target_address", 9000, 3), 5)
        self.assertEqual([r[8] for r in store.load_page(self.path, view, 0, 5)],
                         ["9000", "9003", "9006", "9009", "9012"])
        self.assertEqual(store.fill_cells(self.path, view, 0, 4, 10, 10, "down"), 4)
        self.assertEqual(store.load_page(self.path, view, 0, 5)[4][11], "V")
        with self.assertRaisesRegex(ValueError, "Maximum IEC-104 IOA"):
            store.auto_number(self.path, view, 0, 20, "target_address", 16_777_215, 1)

    def test_literal_search_escapes_wildcards(self):
        self.assertEqual(store.count_rows(self.path, store.View(search="%")), 0)
        self.assertEqual(store.count_rows(self.path, store.View(search="_")), 1000)

    def test_csv_roundtrip(self):
        export = Path(self.temp.name) / "export.csv"
        self.assertEqual(store.export_csv(self.path, export, store.View(search="Voltage")), 125)
        with export.open(encoding="utf-8-sig", newline="") as file:
            self.assertEqual(len(list(csv.DictReader(file))), 125)
        store.seed_demo(self.path, 3)
        self.assertEqual(store.import_csv(self.path, export), 125)
        self.assertEqual(store.count_rows(self.path, store.View()), 128)

    def test_limits_and_addresses(self):
        with self.assertRaises(ValueError):
            store.apply_grid(self.path, store.View(), 0, 12, [["x", "extra"]])
        with self.assertRaises(ValueError):
            store.apply_grid(self.path, store.View(), 0, 0, [[""]])
        self.assertEqual(store.copy_tsv(self.path, store.View(), 0, 0, 0, 1),
                         "Voltage_000001\tModbus TCP\r\n")


if __name__ == "__main__":
    unittest.main()
