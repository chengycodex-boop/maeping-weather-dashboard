import unittest

from src.build_park_grid import generate_grid
from src.discover_thaiwater_stations import geometry_distance_km


SQUARE = {
    "type": "Polygon",
    "coordinates": [[
        [98.0, 17.0], [98.2, 17.0], [98.2, 17.2],
        [98.0, 17.2], [98.0, 17.0],
    ]],
}


class ParkGridTests(unittest.TestCase):
    def test_grid_is_deterministic_and_inside(self):
        first = generate_grid(SQUARE, 5.0)
        second = generate_grid(SQUARE, 5.0)
        self.assertEqual(first, second)
        self.assertGreater(len(first), 8)
        self.assertEqual(first[0]["grid_id"], "GRID5K_0001")
        for row in first:
            point = (float(row["longitude"]), float(row["latitude"]))
            self.assertTrue(geometry_distance_km(point, SQUARE)[1])


if __name__ == "__main__":
    unittest.main()
