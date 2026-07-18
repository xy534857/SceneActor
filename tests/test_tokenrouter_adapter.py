"""Offline tests for the TokenRouter video adapter (no network)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from sceneactor.adapters.tokenrouter import (
    ReferenceMedia,
    TokenRouterError,
    TokenRouterProviderConfig,
    TokenRouterVideoClient,
)

VENDOR_ROOT = Path(__file__).resolve().parents[1] / "vendor" / "GodotAvatarVideoGen"

pytestmark = pytest.mark.skipif(
    not (VENDOR_ROOT / "config" / "tencent_kling_config.json").is_file(),
    reason="vendor submodule not initialized",
)


@pytest.fixture()
def config() -> TokenRouterProviderConfig:
    return TokenRouterProviderConfig.load(VENDOR_ROOT)


@pytest.fixture()
def client(config: TokenRouterProviderConfig) -> TokenRouterVideoClient:
    return TokenRouterVideoClient(config, token="owtr_test")


class TestConfigLoading:
    def test_catalogue_from_vendor_json(self, config: TokenRouterProviderConfig) -> None:
        assert "Kling" in config.model_options
        assert "3.0" in config.model_versions["Kling"]
        assert config.base_url.startswith("http") and not config.base_url.endswith("/")

    def test_capabilities_projection(self, config: TokenRouterProviderConfig) -> None:
        vs = config.capabilities("VS", "2.0-fast")
        assert vs.provider == "tokenrouter/tencent-vod"
        assert vs.model_id == "VS-2.0-fast"
        assert "first_frame" in vs.supported_reference_roles
        kling = config.capabilities("Kling", "3.0")
        assert kling.supported_reference_roles == ()
        assert kling.max_images == 0

    def test_capabilities_rejects_unknown_model(self, config: TokenRouterProviderConfig) -> None:
        with pytest.raises(ValueError, match="unknown model"):
            config.capabilities("NotAModel")

    def test_token_resolution_order(self, config: TokenRouterProviderConfig, monkeypatch) -> None:
        monkeypatch.setenv("SCENEACTOR_OWTR_TOKEN", "owtr_env")
        assert config.resolve_token("owtr_explicit") == "owtr_explicit"
        assert config.resolve_token() == "owtr_env"


class TestPayloadConstruction:
    def test_minimal_payload_uses_vendor_defaults(self, client: TokenRouterVideoClient) -> None:
        payload = client.build_create_payload("a cat", model="Kling", version="3.0")
        assert payload["ModelName"] == "Kling"
        assert payload["ModelVersion"] == "3.0"
        assert payload["OutputConfig"]["Resolution"] == client.config.default_resolution
        assert "FileInfos" not in payload

    def test_references_become_file_infos(self, client: TokenRouterVideoClient) -> None:
        refs = [
            ReferenceMedia(url="https://x/1.png", category="image", role="first_frame"),
            ReferenceMedia(url="https://x/2.png", category="image", role="identity_anchor"),
        ]
        payload = client.build_create_payload("a cat", model="VS", version="2.0", references=refs)
        infos = payload["FileInfos"]
        assert infos[0] == {"Type": "Url", "Category": "Image", "Url": "https://x/1.png", "Usage": "FirstFrame"}
        assert infos[1]["Usage"] == "Reference"

    def test_rejects_bad_inputs(self, client: TokenRouterVideoClient) -> None:
        with pytest.raises(ValueError, match="unknown model"):
            client.build_create_payload("x", model="Sora")
        with pytest.raises(ValueError, match="unknown version"):
            client.build_create_payload("x", model="Kling", version="99.9")
        with pytest.raises(ValueError, match="prompt"):
            client.build_create_payload("   ", model="Kling")
        with pytest.raises(ValueError, match="reference role"):
            ReferenceMedia(url="https://x", category="image", role="bogus").to_file_info()


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class TestLifecycle:
    """Response fixtures mirror REAL DescribeTaskDetail bodies observed live:
    the outer Response.Status is FINISH even for failed generations, Input
    echoes back caller reference URLs, and the output lives ONLY at
    Response.AigcVideoTask.Output.FileInfos[].FileUrl."""

    def _client_with_responses(self, config: TokenRouterProviderConfig, bodies: list[dict]) -> TokenRouterVideoClient:
        queue = [json.dumps(b).encode() for b in bodies]

        def fake_opener(req, timeout=0):
            return _FakeResponse(queue.pop(0))

        return TokenRouterVideoClient(config, token="owtr_test", opener=fake_opener)

    @staticmethod
    def _finish_body(*, err_code: int = 0, message: str = "", output_url: str = "") -> dict:
        output_infos = [{"StorageMode": "Temporary", "FileUrl": output_url}] if output_url else []
        return {
            "Response": {
                "Status": "FINISH",  # outer status lies: FINISH even on failure
                "AigcVideoTask": {
                    "TaskId": "t-x",
                    "Status": "FINISH",
                    "ErrCode": err_code,
                    "Message": message,
                    "Progress": 100,
                    "Input": {
                        "FileInfos": [
                            # echo of the caller's reference video — must NOT be
                            # mistaken for the generated output
                            {"Type": "Url", "Category": "Video", "Url": "https://bucket/reference_dance.mp4"}
                        ]
                    },
                    "Output": {"FileInfos": output_infos},
                },
            }
        }

    def test_output_taken_from_output_not_input_echo(self, config: TokenRouterProviderConfig) -> None:
        client = self._client_with_responses(
            config,
            [
                {"Response": {"TaskId": "t-1"}},
                self._finish_body(output_url="https://vod/generated.mp4"),
            ],
        )
        task_id = client.create_task(client.build_create_payload("a cat", model="Kling"))
        assert task_id == "t-1"
        url, _raw = client.wait_for_video(task_id, timeout_seconds=5)
        assert url == "https://vod/generated.mp4"
        assert "reference_dance" not in url

    def test_inner_failure_raises_despite_outer_finish(self, config: TokenRouterProviderConfig) -> None:
        client = self._client_with_responses(
            config,
            [self._finish_body(err_code=70000, message="task failed: some internal error")],
        )
        with pytest.raises(TokenRouterError, match="ErrCode=70000"):
            client.wait_for_video("t-2", timeout_seconds=5)

    def test_audio_filter_failure_gets_actionable_hint(self, config: TokenRouterProviderConfig) -> None:
        client = self._client_with_responses(
            config,
            [self._finish_body(err_code=70000, message="code:OutputAudioSensitiveContentDetected, message:...")],
        )
        with pytest.raises(TokenRouterError, match="AudioGeneration=Disabled"):
            client.wait_for_video("t-3", timeout_seconds=5)

    def test_finish_without_output_url_raises(self, config: TokenRouterProviderConfig) -> None:
        client = self._client_with_responses(config, [self._finish_body()])
        with pytest.raises(TokenRouterError, match="without an output video URL"):
            client.wait_for_video("t-4", timeout_seconds=5)

    def test_gateway_error_surfaces(self, config: TokenRouterProviderConfig) -> None:
        client = self._client_with_responses(
            config,
            [{"Response": {"Error": {"Code": "AuthFailure", "Message": "bad token"}}}],
        )
        with pytest.raises(TokenRouterError, match="AuthFailure"):
            client.create_task(client.build_create_payload("a cat", model="Kling"))

    def test_material_registration_returns_asset_url(self, config: TokenRouterProviderConfig) -> None:
        client = self._client_with_responses(
            config,
            [
                {"Response": {"TaskId": "m-1"}},
                {
                    "Response": {
                        "Status": "FINISH",
                        "CreateAigcMaterialTask": {
                            "Status": "FINISH",
                            "ErrCode": 0,
                            "Output": {"AssetId": "asset-20260719-abc"},
                        },
                    }
                },
            ],
        )
        assert client.register_material("https://bucket/face.png") == "asset://asset-20260719-abc"

    def test_material_aspect_ratio_failure(self, config: TokenRouterProviderConfig) -> None:
        client = self._client_with_responses(
            config,
            [
                {"Response": {"TaskId": "m-2"}},
                {
                    "Response": {
                        "Status": "FINISH",
                        "CreateAigcMaterialTask": {
                            "Status": "FINISH",
                            "ErrCode": 70000,
                            "Message": "Create asset failed: Aspect ratio must be between 0.4 and 2.5.",
                        },
                    }
                },
            ],
        )
        with pytest.raises(TokenRouterError, match="Aspect ratio"):
            client.register_material("https://bucket/turnaround.png")
