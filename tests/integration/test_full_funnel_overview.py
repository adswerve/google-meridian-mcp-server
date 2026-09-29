import pytest

pytestmark = pytest.mark.integration


def test_full_funnel_overview_block(ff_facade):
    overview = ff_facade.get_model_overview()
    assert overview["funnel"] == "full_funnel"
    assert overview["full_funnel"] == {
        "mediators": [
            {
                "name": "M1",
                "driven_by": ["A"],
                "brand_equity_label": "M1 (brand equity, rest)",
            },
            {
                "name": "M2",
                "driven_by": ["A", "B"],
                "brand_equity_label": "M2 (brand equity, rest)",
            },
        ]
    }
    assert overview["organic_media"] == ["M1", "M2"]  # still described as input data


def test_single_model_overview_says_single(plain_facade):
    overview = plain_facade.get_model_overview()
    assert overview["funnel"] == "single"
    assert "full_funnel" not in overview
