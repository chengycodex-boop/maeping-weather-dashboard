import unittest
from datetime import datetime, timezone

from src.fetch_grid_forecast import chunks, normalize


class GridForecastTests(unittest.TestCase):
    def test_chunks_preserve_rows(self):
        rows = [{"grid_id": str(index)} for index in range(7)]
        batches = list(chunks(rows, 3))
        self.assertEqual([len(batch) for batch in batches], [3, 3, 1])
        self.assertEqual([row for batch in batches for row in batch], rows)

    def test_normalize_maps_api_variables(self):
        fetched_at = datetime(2026, 8, 23, 0, 30, tzinfo=timezone.utc)
        cells = [{"grid_id": "GRID5K_0001"}]
        payloads = [{
            "hourly": {
                "time": ["2026-08-23T08:00", "2026-08-23T09:00"],
                "precipitation": [1.2, 0.0],
                "precipitation_probability": [60, 10],
                "temperature_2m": [27.5, 28.0],
            },
            "hourly_units": {
                "precipitation": "mm",
                "precipitation_probability": "%",
                "temperature_2m": "°C",
            },
        }]
        rows = normalize(cells, payloads, fetched_at)
        self.assertEqual(len(rows), 6)
        self.assertEqual({row[6] for row in rows}, {
            "precipitation", "precipitation_probability", "temperature"
        })


if __name__ == "__main__":
    unittest.main()
