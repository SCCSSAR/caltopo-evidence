"""Unit tests for the pure logic. No network, no credentials."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from caltopo_evidence import model                                  # noqa: E402


def marker(fid, title, cls="Marker"):
    return {"id": fid, "properties": {"class": cls, "title": title}}


class TestResolveContainer(unittest.TestCase):
    """
    parentId is "{ClassName}:{featureId}". Getting this wrong once already
    produced a confident, wrong conclusion that the map's links were corrupt.
    """

    def setUp(self):
        self.by_id = {
            "aaa-111": marker("aaa-111", "NOTAM - 200’ AGL @0.25nm"),
            "bbb-222": marker("bbb-222", "Drone Imagery", cls="Folder"),
            "ccc-333": {"id": "ccc-333", "properties": {"class": "Assignment",
                                                        "title": "Team 3"}},
        }

    def test_no_parent_is_unattached(self):
        self.assertEqual(model.resolve_container(None, self.by_id),
                         (model.UNATTACHED, ""))
        self.assertEqual(model.resolve_container("", self.by_id),
                         (model.UNATTACHED, ""))

    def test_marker_parent_resolves_to_marker_name(self):
        self.assertEqual(
            model.resolve_container("Marker:aaa-111", self.by_id),
            ("Marker", "NOTAM - 200’ AGL @0.25nm"),
        )

    def test_folder_parent_is_not_confused_with_marker(self):
        """
        'Marker' and 'Folder' are both exactly six characters. Any fixed-width
        prefix strip passes this pair silently, which is why this test exists as
        a distinct case rather than a variation of the one above.
        """
        self.assertEqual(
            model.resolve_container("Folder:bbb-222", self.by_id),
            ("Folder", "Drone Imagery"),
        )

    def test_assignment_parent(self):
        self.assertEqual(
            model.resolve_container("Assignment:ccc-333", self.by_id),
            ("Assignment", "Team 3"),
        )

    def test_prefix_is_stripped_not_kept(self):
        """The full prefixed string must never be used as a lookup key."""
        by_id = {"Marker:aaa-111": marker("Marker:aaa-111", "WRONG")}
        cls, name = model.resolve_container("Marker:aaa-111", by_id)
        self.assertNotEqual(name, "WRONG")

    def test_missing_target_keeps_declared_class(self):
        cls, name = model.resolve_container("Marker:gone-999", self.by_id)
        self.assertEqual(cls, "Marker")
        self.assertIn("not found", name)

    def test_id_half_containing_a_colon_survives(self):
        """partition() splits on the FIRST colon; the id half is opaque."""
        by_id = {"weird:id": marker("weird:id", "Odd One")}
        self.assertEqual(model.resolve_container("Marker:weird:id", by_id),
                         ("Marker", "Odd One"))

    def test_bare_id_without_prefix(self):
        self.assertEqual(model.resolve_container("aaa-111", self.by_id),
                         ("Marker", "NOTAM - 200’ AGL @0.25nm"))

    def test_actual_class_wins_over_declared(self):
        by_id = {"x": {"id": "x", "properties": {"class": "Marker", "title": "T"}}}
        self.assertEqual(model.resolve_container("Folder:x", by_id)[0], "Marker")


class TestExtractCoordinates(unittest.TestCase):
    def test_geojson_is_lon_lat_and_we_return_lat_lng(self):
        lat, lng = model.extract_coordinates(
            {"type": "Point", "coordinates": [-121.9, 37.4]})
        self.assertAlmostEqual(lat, 37.4)
        self.assertAlmostEqual(lng, -121.9)

    def test_four_element_array_ignores_trailing_elements(self):
        lat, lng = model.extract_coordinates(
            {"coordinates": [-121.6, 37.1, 0, 0]})
        self.assertAlmostEqual(lat, 37.1)
        self.assertAlmostEqual(lng, -121.6)

    def test_missing_geometry_is_none_not_zero(self):
        """A photo with no location must not silently become (0, 0)."""
        for bad in (None, {}, {"coordinates": None}, {"coordinates": []},
                    {"coordinates": [1.0]}):
            self.assertEqual(model.extract_coordinates(bad), (None, None), bad)

    def test_non_numeric_rejected(self):
        self.assertEqual(
            model.extract_coordinates({"coordinates": ["-121.6", "37.1"]}),
            (None, None))

    def test_booleans_rejected(self):
        """bool is a subclass of int; without a guard True becomes 1.0 degrees."""
        self.assertEqual(model.extract_coordinates({"coordinates": [True, False]}),
                         (None, None))

    def test_out_of_range_rejected(self):
        self.assertEqual(model.extract_coordinates({"coordinates": [-121.6, 95.0]}),
                         (None, None))
        self.assertEqual(model.extract_coordinates({"coordinates": [-200.0, 37.1]}),
                         (None, None))

    def test_swapped_pair_is_rejected_by_the_range_check(self):
        """
        A California coordinate written lat-first has -121.6 in the latitude
        slot, which is outside +/-90 and so is caught. This is luck, not a
        general guarantee: a swap where both values are under 90 (anywhere near
        the equator and prime meridian) is undetectable by range alone.
        """
        self.assertEqual(
            model.extract_coordinates({"coordinates": [37.1, -121.6]}),
            (None, None))

    def test_low_magnitude_swap_is_NOT_detectable(self):
        """Documents the limit of the range check rather than implying safety."""
        lat, lng = model.extract_coordinates({"coordinates": [10.0, 20.0]})
        self.assertEqual((lat, lng), (20.0, 10.0))


class TestEpochMsToIso(unittest.TestCase):
    def test_milliseconds_not_seconds(self):
        """13-digit epoch ms, the shape measured on live CalTopo data."""
        self.assertEqual(model.epoch_ms_to_iso(1755100594000),
                         "2025-08-13T15:56:34Z")

    def test_thirteen_digit_value_is_not_read_as_seconds(self):
        """
        Reading a 13-digit epoch as SECONDS lands in the year 57573, which
        either overflows or produces an absurd date. Either way the result must
        not be a plausible-looking timestamp from the wrong era.
        """
        out = model.epoch_ms_to_iso(1755100594000)
        self.assertTrue(out.startswith("2025"), out)

    def test_unparseable_returns_empty_not_a_guess(self):
        for bad in (None, "", "not-a-number", 0, -1, [], {}):
            self.assertEqual(model.epoch_ms_to_iso(bad), "", bad)


class TestSafeFilename(unittest.TestCase):
    def test_extension_added_when_missing_on_the_ORDINARY_path(self):
        """
        The bug this pins: the extension fallback existed but was applied only
        on the collision branch, so ordinary downloads wrote extensionless
        files while every status field still read "ok".
        """
        self.assertEqual(model.safe_filename("IMG_20260729_090605", "fb"),
                         "IMG_20260729_090605.jpg")

    def test_existing_extension_preserved_and_lowercased(self):
        self.assertEqual(model.safe_filename("IMG_3849.JPEG", "fb"), "IMG_3849.jpeg")

    def test_format_hint_used_when_no_extension(self):
        self.assertEqual(model.safe_filename("photo", "fb", default_ext="png"),
                         "photo.png")

    def test_path_separators_stripped(self):
        out = model.safe_filename("../../etc/passwd", "fb")
        self.assertNotIn("/", out)
        self.assertNotIn("..", out.split(".")[0])

    def test_empty_falls_back(self):
        self.assertEqual(model.safe_filename("", "fallback"), "fallback.jpg")
        self.assertEqual(model.safe_filename("   ", "fallback"), "fallback.jpg")

    def test_dotted_title_keeps_all_of_its_words(self):
        """
        A descriptive title with dots must not lose its last word to extension
        parsing. Appending the real extension preserves the operator's text,
        which on an evidence artifact matters more than a tidy filename.
        """
        self.assertEqual(model.safe_filename("item.found.near.trail", "fb"),
                         "item.found.near.trail.jpg")

    def test_real_extension_is_not_duplicated(self):
        """The counterpart: a genuine extension must be recognised, not appended to."""
        self.assertEqual(model.safe_filename("IMG_3849.jpeg", "fb"), "IMG_3849.jpeg")
        self.assertEqual(model.safe_filename("shot.tiff", "fb"), "shot.tiff")


class TestDedupeFilename(unittest.TestCase):
    def test_case_insensitive_collision(self):
        """
        macOS and Windows filesystems are case-insensitive, so "Item a.jpg"
        and "Item A.jpg" -- a pair of that shape appeared on the map used for
        validation -- would overwrite each other, losing a photo while every
        status read ok.
        """
        used = set()
        a = model.dedupe_filename("Item a.jpg", used)
        b = model.dedupe_filename("Item A.jpg", used)
        self.assertNotEqual(a.lower(), b.lower())

    def test_sequential_collisions(self):
        used = set()
        names = [model.dedupe_filename("Photo.jpg", used) for _ in range(3)]
        self.assertEqual(len(set(names)), 3)
        self.assertEqual(names[0], "Photo.jpg")

    def test_extension_survives_deduping(self):
        used = set()
        model.dedupe_filename("x.png", used)
        self.assertTrue(model.dedupe_filename("x.png", used).endswith(".png"))


class TestMapTitle(unittest.TestCase):
    def test_reads_nested_title(self):
        self.assertEqual(
            model.map_title({"result": {"properties": {"title": "2026-08-19 ExportedMap"}}}),
            "2026-08-19 ExportedMap")

    def test_missing_returns_empty(self):
        for bad in ({}, None, {"result": {}}, {"result": {"properties": {}}}):
            self.assertEqual(model.map_title(bad), "", bad)


if __name__ == "__main__":
    unittest.main()


class TestExifCoordinateSource(unittest.TestCase):
    """
    Provenance of a coordinate. The distinction this pins -- camera-recorded vs
    human-placed -- is invisible in the latitude column and is exactly what an
    evidence reader needs.
    """

    def setUp(self):
        import tempfile, shutil
        from caltopo_evidence import exif
        self.exif = exif
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _jpeg(self, name, with_gps):
        """Minimal JPEG: SOI + APP1(Exif/TIFF/IFD0) + SOS."""
        import struct
        tags = [(0x010F, b"TEST")] + ([(0x8825, b"\x00\x00\x00\x1a")] if with_gps else [])
        ifd = struct.pack("<H", len(tags))
        for tag, val in tags:
            ifd += struct.pack("<HHI", tag, 2, 4) + val
        ifd += b"\x00\x00\x00\x00"
        tiff = b"II\x2a\x00" + struct.pack("<I", 8) + ifd
        payload = b"Exif\x00\x00" + tiff
        app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
        data = b"\xff\xd8" + app1 + b"\xff\xda\x00\x02\x00" + b"\x00" * 32
        p = self.tmp / name
        p.write_bytes(data)
        return p

    def test_detects_gps_when_present(self):
        self.assertTrue(self.exif.has_exif_gps(self._jpeg("gps.jpg", True)))

    def test_absent_gps_is_false_not_an_error(self):
        self.assertFalse(self.exif.has_exif_gps(self._jpeg("nogps.jpg", False)))

    def test_garbage_file_is_false_not_an_exception(self):
        p = self.tmp / "junk.jpg"
        p.write_bytes(b"\xff\xd8" + b"\x00" * 500)
        self.assertFalse(self.exif.has_exif_gps(p))

    def test_missing_file_is_false(self):
        self.assertFalse(self.exif.has_exif_gps(self.tmp / "absent.jpg"))

    def test_map_coordinate_without_exif_is_labelled_map_only(self):
        p = self._jpeg("placed.jpg", False)
        self.assertEqual(
            self.exif.classify_coordinate_source(True, True, p),
            self.exif.SOURCE_MAP_ONLY)

    def test_camera_fix_is_labelled_camera(self):
        p = self._jpeg("shot.jpg", True)
        self.assertEqual(
            self.exif.classify_coordinate_source(True, True, p),
            self.exif.SOURCE_CAMERA)

    def test_no_coordinate_is_none(self):
        self.assertEqual(
            self.exif.classify_coordinate_source(False, True, self._jpeg("x.jpg", True)),
            self.exif.SOURCE_NONE)

    def test_undownloaded_says_unchecked_not_camera(self):
        """
        Without the bytes we cannot know. Defaulting to the flattering answer
        would assert camera provenance the bundle cannot support.
        """
        self.assertEqual(
            self.exif.classify_coordinate_source(True, False, None),
            self.exif.SOURCE_UNCHECKED)


class TestContainerCoordinates(unittest.TestCase):
    """
    Inheriting a coordinate from the parent marker. All 4 uncoordinated photos
    in the validation run hung off markers that DO have a position.
    """

    def setUp(self):
        self.by_id = {
            "m1": {"id": "m1", "properties": {"class": "Marker", "title": "Marker A"},
                   "geometry": {"type": "Point", "coordinates": [-121.91, 37.41]}},
            "m2": {"id": "m2", "properties": {"class": "Marker", "title": "No geom"}},
        }

    def test_inherits_from_marker(self):
        lat, lng = model.container_coordinates("Marker:m1", self.by_id)
        self.assertAlmostEqual(lat, 37.41)
        self.assertAlmostEqual(lng, -121.91)

    def test_marker_without_geometry_yields_nothing(self):
        self.assertEqual(model.container_coordinates("Marker:m2", self.by_id), (None, None))

    def test_unknown_parent_yields_nothing(self):
        self.assertEqual(model.container_coordinates("Marker:gone", self.by_id), (None, None))

    def test_no_parent_yields_nothing(self):
        self.assertEqual(model.container_coordinates(None, self.by_id), (None, None))
        self.assertEqual(model.container_coordinates("", self.by_id), (None, None))

    def test_uses_the_same_prefix_rule_as_resolve_container(self):
        """Must not regress to using the whole prefixed string as a key."""
        self.assertEqual(model.container_coordinates("m1", self.by_id)[0], 37.41)


class TestCoordinateSourceLabelsAreDistinct(unittest.TestCase):
    """
    The four provenance labels must never collapse into one another.

    Mutation testing caught this: changing SOURCE_INHERITED to equal
    SOURCE_CAMERA passed every behavioral test, because each test asserted
    against the constant rather than against the DISTINCTION between constants.
    A label that silently equals another is worse than a missing label -- it
    reports a marker's position as a camera fix.
    """

    def test_all_four_labels_differ(self):
        from caltopo_evidence import exif
        labels = [exif.SOURCE_CAMERA, exif.SOURCE_MAP_ONLY,
                  exif.SOURCE_NONE, exif.SOURCE_UNCHECKED]
        self.assertEqual(len(labels), len(set(labels)), labels)

    def test_map_only_is_not_described_as_a_camera_fix(self):
        from caltopo_evidence import exif
        self.assertNotIn("camera", exif.SOURCE_MAP_ONLY.lower())


class TestMarkerCoordinateColumns(unittest.TestCase):
    """
    Photo position and marker position are reported SEPARATELY and never merged.
    A responder can drop a marker and walk before shooting, so one value must
    never stand in for the other.
    """

    def test_unattached_photo_reports_NA_for_the_marker_pair(self):
        r = model.PhotoRecord(container_type=model.UNATTACHED,
                              latitude=37.1, longitude=-121.6, has_coordinates=True)
        row = r.as_csv_row()
        self.assertEqual(row["marker_latitude"], model.NOT_APPLICABLE)
        self.assertEqual(row["marker_longitude"], model.NOT_APPLICABLE)
        self.assertEqual(row["latitude"], "37.10000000")

    def test_attached_photo_reports_both_pairs_independently(self):
        r = model.PhotoRecord(container_type="Marker", container_name="Marker A",
                              latitude=37.1, longitude=-121.6, has_coordinates=True,
                              marker_latitude=37.2, marker_longitude=-121.7)
        row = r.as_csv_row()
        self.assertEqual(row["latitude"], "37.10000000")
        self.assertEqual(row["marker_latitude"], "37.20000000")
        self.assertNotEqual(row["latitude"], row["marker_latitude"])

    def test_photo_without_its_own_coordinate_keeps_that_cell_EMPTY(self):
        """The marker position must not be quietly promoted into the photo column."""
        r = model.PhotoRecord(container_type="Marker", has_coordinates=False,
                              marker_latitude=37.2, marker_longitude=-121.7)
        row = r.as_csv_row()
        self.assertEqual(row["latitude"], "")
        self.assertEqual(row["has_coordinates"], "no")
        self.assertEqual(row["marker_latitude"], "37.20000000")

    def test_attached_but_uncoordinated_container_is_NA(self):
        r = model.PhotoRecord(container_type="Marker", marker_latitude=None)
        self.assertEqual(r.as_csv_row()["marker_latitude"], model.NOT_APPLICABLE)


class TestValidateMapId(unittest.TestCase):
    """
    The map id becomes a directory name as well as an API path segment, and
    `Path("bundles") / "/tmp/x"` is `/tmp/x`. An absolute or dotted id would
    silently escape --out.
    """

    def test_bare_handles_pass(self):
        for good in ("ABC123", "a1b2c3d", "a", "A1b2C3", "with-dash", "with_underscore"):
            with self.subTest(map_id=good):
                self.assertEqual(model.validate_map_id(good), good)

    def test_surrounding_whitespace_is_stripped(self):
        self.assertEqual(model.validate_map_id("  ABC123\n"), "ABC123")

    def test_absolute_path_is_refused(self):
        """This is the pathlib trap: joining an absolute path discards the base."""
        with self.assertRaises(model.InvalidMapId):
            model.validate_map_id("/tmp/absolute")

    def test_dot_segments_are_refused(self):
        for bad in ("../../escape", "a/../../b", "..", "."):
            with self.subTest(map_id=bad):
                with self.assertRaises(model.InvalidMapId):
                    model.validate_map_id(bad)

    def test_separators_are_refused(self):
        for bad in ("a/b", "a\\b", "a b", "a;b", "a\x00b"):
            with self.subTest(map_id=bad):
                with self.assertRaises(model.InvalidMapId):
                    model.validate_map_id(bad)

    def test_empty_is_refused(self):
        for bad in ("", "   ", None):
            with self.subTest(map_id=bad):
                with self.assertRaises(model.InvalidMapId):
                    model.validate_map_id(bad)

    def test_pasted_url_is_named_in_the_error(self):
        """The realistic input is a paste, not an attack. Say so."""
        with self.assertRaises(model.InvalidMapId) as ctx:
            model.validate_map_id("https://caltopo.com/m/ABC123")
        self.assertIn("ABC123", str(ctx.exception))
        self.assertIn("Did you mean", str(ctx.exception))

    def test_deprecated_sartopo_host_is_named(self):
        """sartopo.com still resolves but is deprecated. Remind, do not support."""
        with self.assertRaises(model.InvalidMapId) as ctx:
            model.validate_map_id("https://sartopo.com/m/ABC123")
        msg = str(ctx.exception)
        self.assertIn("ABC123", msg)
        self.assertIn("sartopo.com is deprecated", msg)
        self.assertIn("caltopo.com", msg)

    def test_canonical_host_paste_gets_no_deprecation_noise(self):
        with self.assertRaises(model.InvalidMapId) as ctx:
            model.validate_map_id("https://caltopo.com/m/ABC123")
        self.assertNotIn("deprecated", str(ctx.exception))

    def test_unrecoverable_input_gets_no_misleading_hint(self):
        with self.assertRaises(model.InvalidMapId) as ctx:
            model.validate_map_id("/tmp/../etc/")
        self.assertNotIn("Did you mean", str(ctx.exception))


class TestValidateMapIdIsWiredIntoExtract(unittest.TestCase):
    def test_extract_validates_before_touching_the_filesystem(self):
        """
        Order is the whole point. Validating after prepare_output_dir would
        create the escaped directory and then complain about it.
        """
        import inspect
        from caltopo_evidence import extract as extract_mod
        src = inspect.getsource(extract_mod.extract)
        stripped = "\n".join(l.split("#")[0] for l in src.splitlines())
        self.assertIn("model.validate_map_id(map_id)", stripped)
        self.assertLess(stripped.index("model.validate_map_id(map_id)"),
                        stripped.index("prepare_output_dir("),
                        "validation must run before the output directory is created")


class TestNonPointContainerGeometries(unittest.TestCase):
    """
    A photo can be attached to a Line & Polygon (API class `Shape`) or to an
    Assignment. Both were exercised against a real map: the container resolves
    by name, and the marker coordinate columns correctly read N/A.

    Those containers carry a real geometry, just not a Point. Declining is the
    right answer, but today it happens because a LineString's first element is
    a list and fails the numeric type guard, not because anything checks the
    geometry type. That is correct and worth pinning: a later "be lenient about
    numeric input" change could start reporting a polygon's FIRST VERTEX as the
    container position, which would look entirely plausible while pointing at
    one corner of a search area.
    """

    def test_linestring_yields_no_coordinate(self):
        self.assertEqual(
            model.extract_coordinates(
                {"type": "LineString",
                 "coordinates": [[-121.9, 37.4], [-121.91, 37.41]]}),
            (None, None))

    def test_polygon_yields_no_coordinate(self):
        self.assertEqual(
            model.extract_coordinates(
                {"type": "Polygon",
                 "coordinates": [[[-121.9, 37.4], [-121.91, 37.41], [-121.9, 37.4]]]}),
            (None, None))

    def test_container_coordinates_are_NA_for_a_shape(self):
        by_id = {"s1": {"id": "s1", "properties": {"class": "Shape", "title": "Segment A"},
                        "geometry": {"type": "LineString",
                                     "coordinates": [[-121.9, 37.4], [-121.91, 37.41]]}}}
        self.assertEqual(model.container_coordinates("Shape:s1", by_id), (None, None))

    def test_container_coordinates_are_NA_for_an_assignment(self):
        by_id = {"a1": {"id": "a1", "properties": {"class": "Assignment", "title": "Team 1"},
                        "geometry": {"type": "Polygon",
                                     "coordinates": [[[-121.9, 37.4], [-121.91, 37.41],
                                                      [-121.9, 37.4]]]}}}
        self.assertEqual(model.container_coordinates("Assignment:a1", by_id), (None, None))

    def test_shape_and_assignment_containers_resolve_by_name(self):
        """Container resolution is class-agnostic; these two are now proven so."""
        by_id = {
            "s1": {"id": "s1", "properties": {"class": "Shape", "title": "Segment A"}},
            "a1": {"id": "a1", "properties": {"class": "Assignment", "title": "Team 1"}},
        }
        self.assertEqual(model.resolve_container("Shape:s1", by_id), ("Shape", "Segment A"))
        self.assertEqual(model.resolve_container("Assignment:a1", by_id),
                         ("Assignment", "Team 1"))
