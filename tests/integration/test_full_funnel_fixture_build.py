"""The generated full-funnel fixture loads, and its stages are draw-compatible."""

import pytest

pytestmark = pytest.mark.integration


def test_fixture_builds_with_matching_stages():
    from google_meridian_mcp_server.meridian.loader import load_meridian_model
    from scripts.validation.fixtures import full_funnel_paths

    stage2_path, mediator_paths = full_funnel_paths("geo-full-funnel")
    stage2 = load_meridian_model(stage2_path)
    assert list(stage2.input_data.organic_media_channel.values) == ["M1", "M2"]
    post2 = stage2.inference_data.posterior
    for name, path in mediator_paths.items():
        stage1 = load_meridian_model(path)
        post1 = stage1.inference_data.posterior
        assert (post1.chain.size, post1.draw.size) == (
            post2.chain.size,
            post2.draw.size,
        )
        assert list(stage1.input_data.get_all_paid_channels()) == (
            ["A"] if name == "M1" else ["A", "B"]
        )
