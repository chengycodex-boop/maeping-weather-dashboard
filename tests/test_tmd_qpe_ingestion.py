import io
import unittest
import zipfile

from src.fetch_tmd_qpe import extract_values


class TmdQpeIngestionTests(unittest.TestCase):
    def test_extract_nearest_grid_value_and_nodata(self):
        header = "ncols 3\nnrows 2\nxllcorner 98.0\nyllcorner 17.0\ncellsize 0.1\nnodata_value -9999\n"
        content = (header + "1 2 3\n4 -9999 6\n").encode("ascii")
        buffer = io.BytesIO()
        filename = "Z__C_VTBB_20260711200000_SRF_GPV_Gll0p01deg_Prr60lv_ANAL.asc"
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(filename, content)
        cells = [
            {"grid_id": "north", "longitude": 98.15, "latitude": 17.15},
            {"grid_id": "south_nodata", "longitude": 98.15, "latitude": 17.05},
        ]
        _, _, values = extract_values(buffer.getvalue(), cells)
        self.assertEqual(values["north"], 2.0)
        self.assertIsNone(values["south_nodata"])


if __name__ == "__main__":
    unittest.main()
