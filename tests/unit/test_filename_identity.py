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

    def test_single_letter_n_number(self):
        identity = parse_media_identity("N0808.mp4")
        assert identity.canonical_number == "N-0808"
        assert identity.number_aliases == ["N-0808", "N0808"]

    def test_single_letter_k_number(self):
        identity = parse_media_identity("k1234.mp4")
        assert identity.canonical_number == "K-1234"
        assert identity.number_aliases == ["K-1234", "K1234"]

    def test_compact_red_is_distinct_from_hyphenated_red(self):
        compact = parse_media_identity("RED155.avi")
        hyphenated = parse_media_identity("RED-155.avi")

        assert compact.canonical_number == "RED155"
        assert compact.number_aliases == ["RED155"]
        assert hyphenated.canonical_number == "RED-155"
        assert hyphenated.number_aliases == ["RED-155"]

    def test_known_three_digit_prefix_pads_short_number(self):
        hyphenated = parse_media_identity("ZMIN-05 淫尻授業.mp4")
        compact = parse_media_identity("ZMIN05.mp4")

        assert hyphenated.canonical_number == "ZMIN-005"
        assert hyphenated.number_aliases == ["ZMIN-005"]
        assert compact.canonical_number == "ZMIN-005"

    def test_unconfigured_short_number_prefix_is_not_padded(self):
        identity = parse_media_identity("JS-19.mp4")
        assert identity.canonical_number == "JS-19"

    def test_mkbd_alphanumeric_suffix_number(self):
        hyphenated = parse_media_identity("MKBD-S94.mp4")
        compact = parse_media_identity("MKBDS94.mp4")
        split = parse_media_identity("MKD-S150-1.mp4")

        assert hyphenated.canonical_number == "MKBD-S94"
        assert hyphenated.number_aliases == ["MKBD-S94", "MKBDS94"]
        assert compact.canonical_number == "MKBD-S94"
        assert compact.number_aliases == ["MKBD-S94", "MKBDS94"]
        assert split.canonical_number == "MKD-S150"
        assert split.number_aliases == ["MKD-S150", "MKDS150"]
        assert split.part_index == "1"

    def test_western_title_keeps_title_stem_without_fake_number(self):
        identity = parse_media_identity(
            "Blacked.16.12.26.Lena.Paul.And.Angela.White.Lena.Gets.Her.Groove.Back.4k.mp4"
        )
        assert identity.canonical_number is None
        assert identity.raw_stem.startswith("Blacked.16.12.26")


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

    def test_compact_red_query_does_not_try_hyphenated_red(self):
        identity = parse_media_identity("RED155.avi")
        assert build_source_queries(identity, "javdb") == ["RED155"]
        assert build_source_queries(identity, "jav321") == ["RED155"]

    def test_zmin_source_query_uses_padded_canonical(self):
        identity = parse_media_identity("ZMIN-05.mp4")
        assert build_source_queries(identity, "javbus") == ["ZMIN-005"]
        assert build_source_queries(identity, "jav321") == ["ZMIN-005"]

    def test_mkbd_source_query_keeps_alphanumeric_suffix_alias(self):
        identity = parse_media_identity("MKBD-S94.mp4")
        assert build_source_queries(identity, "avsox") == ["MKBD-S94", "MKBDS94"]

        mkd = parse_media_identity("MKD-S150-1.mp4")
        assert build_source_queries(mkd, "avsox") == ["MKD-S150", "MKDS150"]

    def test_date_style_keeps_canonical_and_d2pass_uses_exact_separator(self):
        identity = parse_media_identity("102318_778.mp4")
        assert identity.canonical_number == "102318_778"
        assert identity.work_key == "102318_778"
        assert build_source_queries(identity, "d2pass") == ["102318_778"]
        assert build_source_queries(identity, "javdb") == ["102318_778", "102318-778"]

    def test_single_letter_d2pass_query_uses_compact_site_id_first(self):
        identity = parse_media_identity("n0783.mp4")
        assert identity.canonical_number == "N-0783"
        assert build_source_queries(identity, "d2pass") == ["n0783", "N0783", "N-0783"]
        assert build_source_queries(identity, "tokyohot") == ["n0783", "N0783", "N-0783"]
        assert build_source_queries(identity, "metatube:TOKYO-HOT") == ["n0783", "N0783", "N-0783"]
