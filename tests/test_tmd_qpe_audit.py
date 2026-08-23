import io
import unittest
import zipfile

from src.audit_tmd_qpe import parse_header, timestamp_from_filename


class TmdQpeAuditTests(unittest.TestCase):
    def test_timestamp_from_filename_is_utc(self):
        value = timestamp_from_filename("Z__C_VTBB_20260711200000_SRF_GPV_Gll0p01deg_Prr60lv_ANAL.asc")
        self.assertEqual(value.isoformat(), "2026-07-11T20:00:00+00:00")

    def test_parse_header(self):
        content = b"ncols 1300\nnrows 1850\nxllcorner 95.005\nyllcorner 3.995\ncellsize 0.01\nnodata_value -9999\n"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("Z__C_VTBB_20260711200000_SRF_GPV_Gll0p01deg_Prr60lv_ANAL.asc", content)
        filename, header = parse_header(buffer.getvalue())
        self.assertTrue(filename.endswith(".asc"))
        self.assertEqual(header["cellsize"], 0.01)
        self.assertEqual(header["ncols"], 1300)


if __name__ == "__main__":
    unittest.main()
