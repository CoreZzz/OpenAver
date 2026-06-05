"""Tests for unified media filename identity parsing."""

import json
from pathlib import Path

from core.filename_identity import build_source_queries, parse_media_identity


MATRIX_PATH = Path(__file__).parents[1] / "fixtures" / "filename_variants" / "matrix.json"


def test_phase0_filename_matrix_contract():
    cases = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    assert cases, "filename variant fixture matrix must not be empty"

    for case in cases:
        identity = parse_media_identity(case["filename"])
        assert identity.canonical_number == case["canonical_number"], case["filename"]
        assert identity.search_number == case["canonical_number"], case["filename"]
        assert identity.work_key == case["canonical_number"], case["filename"]
        assert identity.variant_flags.subtitle_cn is case["subtitle_cn"], case["filename"]
        assert identity.variant_flags.cracked is case["cracked"], case["filename"]
        assert identity.part_index == case["part_index"], case["filename"]


class TestCanonicalNumber:
    def test_separator_equivalence(self):
        cases = ["SONE-103.mp4", "sone_103.mp4", "sone103.mp4"]
        assert [parse_media_identity(c).canonical_number for c in cases] == [
            "SONE-103",
            "SONE-103",
            "SONE-103",
        ]

    def test_fc2_aliases_share_canonical(self):
        cases = [
            "FC2PPV-1234567.mp4",
            "FC2PPV1234567.mp4",
            "FC2-1234567.mp4",
            "FC2-PPV-1234567.mp4",
            "fc2_ppv_1234567.mp4",
        ]
        assert {parse_media_identity(c).canonical_number for c in cases} == {
            "FC2-PPV-1234567"
        }

    def test_invalid_noise_prefix(self):
        assert parse_media_identity("random_movie_2024.mp4").canonical_number is None


class TestVariantFlags:
    def test_c_is_chinese_subtitle(self):
        identity = parse_media_identity("SONE-103-C.mp4")
        assert identity.canonical_number == "SONE-103"
        assert identity.variant_flags.subtitle_cn is True
        assert identity.variant_flags.cracked is False
        assert identity.part_index is None

    def test_u_is_cracked_not_uncensored(self):
        identity = parse_media_identity("SONE-103-U.mp4")
        assert identity.canonical_number == "SONE-103"
        assert identity.variant_flags.cracked is True
        assert identity.variant_flags.subtitle_cn is False
        assert "破解" in identity.variant_label

    def test_uc_is_cracked_and_subtitle(self):
        identity = parse_media_identity("SONE_103_uc.mp4")
        assert identity.canonical_number == "SONE-103"
        assert identity.variant_flags.cracked is True
        assert identity.variant_flags.subtitle_cn is True
        assert identity.variant_label == "破解+中文字幕"

    def test_part_suffix_numeric(self):
        identity = parse_media_identity("SONE-103-1.mp4")
        assert identity.canonical_number == "SONE-103"
        assert identity.part_index == "1"
        assert identity.work_key == "SONE-103"

    def test_part_suffix_letter(self):
        identity = parse_media_identity("SONE-103-A.mp4")
        assert identity.canonical_number == "SONE-103"
        assert identity.part_index == "A"


class TestSourceQueries:
    def test_fc2_source_query_order(self):
        identity = parse_media_identity("FC2-1234567.mp4")
        assert build_source_queries(identity, "fc2") == [
            "FC2-PPV-1234567",
            "FC2PPV-1234567",
            "1234567",
        ]

    def test_javdb_fc2_query_order(self):
        identity = parse_media_identity("FC2PPV-1234567.mp4")
        assert build_source_queries(identity, "javdb") == [
            "FC2-PPV-1234567",
            "FC2-1234567",
            "FC2PPV-1234567",
        ]

    def test_non_fc2_uses_canonical(self):
        identity = parse_media_identity("sone_103_uc.mp4")
        assert build_source_queries(identity, "javbus") == ["SONE-103"]
