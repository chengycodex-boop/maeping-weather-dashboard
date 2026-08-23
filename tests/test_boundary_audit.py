import unittest

from src.audit_park_boundary import choose_component, polygon_area_km2, simplify_ring


SQUARE = {"type": "Polygon", "coordinates": [[[98, 17], [98.2, 17], [98.2, 17.2], [98, 17.2], [98, 17]]]}


class BoundaryAuditTests(unittest.TestCase):
    def test_area_is_positive(self):
        self.assertGreater(polygon_area_km2(SQUARE), 400)

    def test_simplify_preserves_closed_ring(self):
        ring = [[98, 17], [98.1, 17], [98.2, 17], [98.2, 17.2], [98, 17.2], [98, 17]]
        simplified = simplify_ring(ring)
        self.assertEqual(simplified[0], simplified[-1])
        self.assertLess(len(simplified), len(ring))

    def test_choose_overlapping_component(self):
        far = [[[100, 19], [100.2, 19], [100.2, 19.2], [100, 19.2], [100, 19]]]
        national = {"type": "MultiPolygon", "coordinates": [far, SQUARE["coordinates"]]}
        index, geometry = choose_component(national, SQUARE)
        self.assertEqual(index, 1)
        self.assertEqual(geometry["type"], "Polygon")


if __name__ == "__main__":
    unittest.main()
