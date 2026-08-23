import unittest

from src.discover_thaiwater_stations import (
    geometry_distance_km,
    point_in_ring,
)


SQUARE = {
    "type": "Polygon",
    "coordinates": [
        [
            [98.0, 17.0],
            [99.0, 17.0],
            [99.0, 18.0],
            [98.0, 18.0],
            [98.0, 17.0],
        ]
    ],
}


class StationDiscoveryTests(unittest.TestCase):
    def test_point_in_ring(self):
        self.assertTrue(point_in_ring((98.5, 17.5), SQUARE["coordinates"][0]))
        self.assertFalse(point_in_ring((99.5, 17.5), SQUARE["coordinates"][0]))

    def test_inside_polygon_has_zero_distance(self):
        distance, inside = geometry_distance_km((98.5, 17.5), SQUARE)
        self.assertTrue(inside)
        self.assertEqual(distance, 0.0)

    def test_outside_polygon_has_positive_distance(self):
        distance, inside = geometry_distance_km((99.1, 17.5), SQUARE)
        self.assertFalse(inside)
        self.assertGreater(distance, 10)
        self.assertLess(distance, 12)


if __name__ == "__main__":
    unittest.main()
