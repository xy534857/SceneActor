"""TokenRouter video-generation adapter.

Wraps the internal TokenRouter gateway (Tencent-VOD vendor route: Kling, Vidu,
Hailuo, VS/seedance, ...) behind a plain-Python client, so SceneActor can turn
compiled ``ShotSpec`` prompts into rendered MP4 files without the Godot frontend.

Provider metadata (model catalogue, versions, defaults, upload bucket) is NOT
hardcoded here: it is loaded at runtime from the vendored GodotAvatarVideoGen
repository (``vendor/GodotAvatarVideoGen/config/*.json``). Pulling that
submodule picks up new models/versions with zero code changes.

Interface contract mirrors ``vendor/.../scripts/providers/tencent_kling_client.gd``
and ``docs/videoGen_输入输出接口说明.md``:

    POST {base_url}/v1/video-tasks/tencent-vod
    headers: Authorization: Bearer <owtr_token>, X-TC-Action: <Action>
    actions: CreateAigcVideoTask -> DescribeTaskDetail (poll until FINISH)

Credentials resolve from (in order): explicit argument, ``SCENEACTOR_OWTR_TOKEN``
env var, or the vendored repo's ``credentials/credential.json``.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .seedance import ProviderCapabilities

ROUTER_PATH = "/v1/video-tasks/tencent-vod"
_VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".ogv")

# Reference roles the tencent-vod route accepts, mapped to FileInfos Usage.
_ROLE_TO_USAGE = {
    "first_frame": "FirstFrame",
    "identity_anchor": "Reference",
    "face_anchor": "Reference",
    "scene_style": "Reference",
    "prop": "Reference",
    "reference": "Reference",
}


def default_vendor_root() -> Path:
    """Locate the vendored GodotAvatarVideoGen checkout.

    Prefers ``SCENEACTOR_VIDEOGEN_VENDOR`` env var, then the repo-relative
    ``vendor/GodotAvatarVideoGen`` submodule.
    """
    env = os.environ.get("SCENEACTOR_VIDEOGEN_VENDOR", "").strip()
    if env:
        return Path(env).expanduser()
    # src/sceneactor/adapters/tokenrouter.py -> repo root is four levels up.
    return Path(__file__).resolve().parents[3] / "vendor" / "GodotAvatarVideoGen"


@dataclass(frozen=True)
class TokenRouterProviderConfig:
    """Provider metadata loaded from the vendored repo's config files."""

    base_url: str
    model_options: tuple[str, ...]
    model_versions: Mapping[str, tuple[str, ...]]
    model_aliases: Mapping[str, str]
    default_model: str
    default_resolution: str
    default_duration: int
    default_aspect_ratio: str
    default_audio_generation: str
    poll_interval_seconds: float
    request_timeout_seconds: int
    oci_par_base_url: str
    vendor_root: Path

    @classmethod
    def load(cls, vendor_root: Path | None = None) -> "TokenRouterProviderConfig":
        root = vendor_root or default_vendor_root()
        config_path = root / "config" / "tencent_kling_config.json"
        if not config_path.is_file():
            raise FileNotFoundError(
                f"vendored config not found: {config_path}. "
                "Run `git submodule update --init` or set SCENEACTOR_VIDEOGEN_VENDOR."
            )
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        versions = {
            str(model): tuple(str(v) for v in vers)
            for model, vers in dict(raw.get("model_versions", {})).items()
        }
        return cls(
            base_url=_normalize_base_url(str(raw.get("base_url", ""))),
            model_options=tuple(str(m) for m in raw.get("model_options", [])),
            model_versions=versions,
            model_aliases={str(k): str(v) for k, v in dict(raw.get("model_aliases", {})).items()},
            default_model=str(raw.get("model_name", "Kling")),
            default_resolution=str(raw.get("resolution", "720P")),
            default_duration=int(raw.get("duration", 5)),
            default_aspect_ratio=str(raw.get("aspect_ratio", "16:9")),
            default_audio_generation=str(raw.get("audio_generation", "Enabled")),
            poll_interval_seconds=float(raw.get("poll_interval_seconds", 5.0)),
            request_timeout_seconds=int(raw.get("request_timeout_seconds", 60)),
            oci_par_base_url=str(raw.get("oci_par_base_url", "")),
            vendor_root=root,
        )

    def capabilities(self, model: str, version: str = "") -> ProviderCapabilities:
        """Project one catalogue entry into SceneActor's ProviderCapabilities."""
        if model not in self.model_options:
            raise ValueError(f"unknown model {model!r}; catalogue: {self.model_options}")
        model_id = f"{model}-{version}" if version else model
        # Per vendor docs, only SV/VS-family models consume reference media.
        multimodal = model in ("VS", "SV")
        return ProviderCapabilities(
            provider="tokenrouter/tencent-vod",
            model_id=model_id,
            supported_reference_roles=tuple(_ROLE_TO_USAGE) if multimodal else (),
            max_images=None if multimodal else 0,
            max_videos=None if multimodal else 0,
            max_audios=None if multimodal else 0,
            min_duration=1,
            max_duration=15,
            resolutions=("480P", "720P", "1080P", "4K"),
            supports_audio=True,
        )

    def resolve_token(self, token: str = "") -> str:
        if token.strip():
            return token.strip()
        env = os.environ.get("SCENEACTOR_OWTR_TOKEN", "").strip()
        if env:
            return env
        cred_path = self.vendor_root / "credentials" / "credential.json"
        if cred_path.is_file():
            stored = str(json.loads(cred_path.read_text(encoding="utf-8")).get("token", "")).strip()
            if stored:
                return stored
        raise ValueError(
            "no TokenRouter token: pass one explicitly, set SCENEACTOR_OWTR_TOKEN, "
            f"or fill {cred_path}"
        )


@dataclass(frozen=True)
class ReferenceMedia:
    """One reference item for FileInfos. ``url`` must be publicly fetchable."""

    url: str
    category: str  # Image | Video | Audio
    role: str = "reference"  # key of _ROLE_TO_USAGE

    def to_file_info(self) -> dict[str, str]:
        usage = _ROLE_TO_USAGE.get(self.role)
        if usage is None:
            raise ValueError(f"unsupported reference role: {self.role!r}")
        return {
            "Type": "Url",
            "Category": self.category.capitalize(),
            "Url": self.url,
            "Usage": usage,
        }


@dataclass(frozen=True)
class VideoTaskResult:
    task_id: str
    status: str
    video_url: str
    video_path: Path | None
    elapsed_seconds: float
    provider_response: Mapping[str, Any] = field(default_factory=dict)


class TokenRouterVideoClient:
    """Blocking create -> poll -> download client for the tencent-vod route."""

    def __init__(
        self,
        config: TokenRouterProviderConfig | None = None,
        *,
        token: str = "",
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config or TokenRouterProviderConfig.load()
        self.token = self.config.resolve_token(token)
        self._opener = opener or urllib.request.urlopen

    # -- request construction (pure; unit-testable) --------------------------

    def build_create_payload(
        self,
        prompt: str,
        *,
        model: str = "",
        version: str = "",
        resolution: str = "",
        duration: int = 0,
        aspect_ratio: str = "",
        audio: bool | None = None,
        references: Sequence[ReferenceMedia] = (),
    ) -> dict[str, Any]:
        cfg = self.config
        chosen_model = model or cfg.default_model
        if chosen_model not in cfg.model_options:
            raise ValueError(f"unknown model {chosen_model!r}; catalogue: {cfg.model_options}")
        if version and version not in cfg.model_versions.get(chosen_model, ()):
            raise ValueError(
                f"unknown version {version!r} for {chosen_model}; "
                f"catalogue: {cfg.model_versions.get(chosen_model, ())}"
            )
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        audio_setting = cfg.default_audio_generation if audio is None else ("Enabled" if audio else "Disabled")
        payload: dict[str, Any] = {
            "ModelName": chosen_model,
            "Prompt": prompt,
            "OutputConfig": {
                "Resolution": resolution or cfg.default_resolution,
                "Duration": duration or cfg.default_duration,
                "AspectRatio": aspect_ratio or cfg.default_aspect_ratio,
                "AudioGeneration": audio_setting,
            },
        }
        if version:
            payload["ModelVersion"] = version
        if references:
            payload["FileInfos"] = [item.to_file_info() for item in references]
        return payload

    def _request(self, action: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            self.config.base_url + ROUTER_PATH,
            data=json.dumps(dict(payload)).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
                "X-TC-Action": action,
            },
            method="POST",
        )
        try:
            with self._opener(req, timeout=self.config.request_timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1200]
            raise TokenRouterError(_friendly_error(exc.code, detail)) from exc
        inner = body.get("Response", {}) if isinstance(body, dict) else {}
        error = inner.get("Error") if isinstance(inner, dict) else None
        if isinstance(error, dict) and error:
            raise TokenRouterError(f"{action} failed: {error.get('Code')}: {error.get('Message')}")
        return body

    # -- lifecycle ------------------------------------------------------------

    def create_task(self, payload: Mapping[str, Any]) -> str:
        body = self._request("CreateAigcVideoTask", payload)
        task_id = str(body.get("Response", {}).get("TaskId", ""))
        if not task_id:
            raise TokenRouterError(f"create response missing TaskId: {json.dumps(body)[:500]}")
        return task_id

    def describe_task(self, task_id: str) -> dict[str, Any]:
        return self._request("DescribeTaskDetail", {"TaskId": task_id})

    def wait_for_video(
        self,
        task_id: str,
        *,
        timeout_seconds: float = 900.0,
        on_status: Callable[[str, Mapping[str, Any]], None] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Poll until the inner AigcVideoTask finishes; return (output_url, raw_response).

        The outer ``Response.Status`` reports FINISH even when the generation
        itself FAILED, and ``Response.Input`` echoes back the caller's reference
        URLs — so completion, errors, and the output URL are all read strictly
        from ``Response.AigcVideoTask`` (never scanned from the whole response,
        which previously returned the uploaded reference video as the "result").
        """
        started = time.monotonic()
        while True:
            body = self.describe_task(task_id)
            response = body.get("Response", {}) if isinstance(body, dict) else {}
            node = response.get("AigcVideoTask") or {}
            status = str(node.get("Status", response.get("Status", "")))
            if on_status is not None:
                on_status(status, response)
            if status.upper() == "FINISH":
                err_code = int(node.get("ErrCode") or 0)
                if err_code:
                    raise TokenRouterError(
                        _friendly_task_error(task_id, err_code, str(node.get("Message", "")))
                    )
                url = _output_video_url(node)
                if not url:
                    raise TokenRouterError(f"task {task_id} finished without an output video URL")
                return url, body
            if "FAIL" in status.upper() or "ERROR" in status.upper():
                raise TokenRouterError(
                    _friendly_task_error(task_id, int(node.get("ErrCode") or 0), str(node.get("Message", status)))
                )
            if time.monotonic() - started > timeout_seconds:
                raise TokenRouterError(f"task {task_id} timed out after {timeout_seconds:.0f}s (status={status})")
            time.sleep(self.config.poll_interval_seconds)

    def download(self, video_url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(video_url)
        with self._opener(req, timeout=max(self.config.request_timeout_seconds, 300)) as response:
            dest.write_bytes(response.read())
        return dest

    def upload_reference(self, path: Path) -> str:
        """Upload a local file to the OCI PAR bucket; return its public URL.

        Mirrors the vendor client: local reference media must be publicly
        fetchable before Tencent can consume it via FileInfos, so we PUT to
        ``{oci_par_base_url}{object}`` and reuse the same URL as the public
        reference. Content-addressed naming makes repeat uploads idempotent.
        """
        par_base = self.config.oci_par_base_url.strip()
        if not par_base:
            raise TokenRouterError(
                "oci_par_base_url is not configured in the vendored "
                "tencent_kling_config.json; local reference uploads are disabled"
            )
        if not par_base.endswith("/"):
            par_base += "/"
        source = Path(path).expanduser()
        if not source.is_file():
            raise TokenRouterError(f"reference file not found: {source}")
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        suffix = source.suffix.lower() or ".bin"
        url = f"{par_base}sceneactor/{digest}{suffix}"
        content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": content_type},
            method="PUT",
        )
        try:
            with self._opener(req, timeout=max(self.config.request_timeout_seconds, 300)):
                pass
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise TokenRouterError(f"reference upload failed (HTTP {exc.code}): {detail}") from exc
        return url

    def register_material(
        self,
        image_url: str,
        *,
        name: str = "sceneactor-material",
        group: str = "sceneactor",
        real_person: bool = False,
        timeout_seconds: float = 300.0,
    ) -> str:
        """Register an image as an AIGC material; return its ``asset://`` URL.

        Registered materials carry identity far more reliably than raw image
        URLs when combined with other references (e.g. a dance video for motion
        transfer). Requirement: image aspect ratio must be within 0.4-2.5, or
        Tencent rejects the asset.
        """
        body = self._request(
            "CreateAigcMaterial",
            {
                "FileInfo": {"Type": "Url", "Url": image_url},
                "AssetType": "Image",
                "IsRealPerson": "True" if real_person else "False",
                "GroupId": group,
                "GroupName": group,
                "AssetName": name,
            },
        )
        task_id = str(body.get("Response", {}).get("TaskId", ""))
        if not task_id:
            raise TokenRouterError(f"material create response missing TaskId: {json.dumps(body)[:400]}")
        started = time.monotonic()
        while True:
            response = self.describe_task(task_id).get("Response", {})
            node = response.get("CreateAigcMaterialTask") or {}
            status = str(node.get("Status", response.get("Status", "")))
            if status.upper() == "FINISH":
                err_code = int(node.get("ErrCode") or 0)
                asset_id = str((node.get("Output") or {}).get("AssetId", "") or "")
                if err_code or not asset_id:
                    raise TokenRouterError(
                        f"material registration failed (ErrCode={err_code}): {node.get('Message', '')}"
                    )
                return f"asset://{asset_id}"
            if time.monotonic() - started > timeout_seconds:
                raise TokenRouterError(f"material task {task_id} timed out (status={status})")
            time.sleep(self.config.poll_interval_seconds)

    def generate(
        self,
        prompt: str,
        *,
        model: str = "",
        version: str = "",
        resolution: str = "",
        duration: int = 0,
        aspect_ratio: str = "",
        audio: bool | None = None,
        references: Sequence[ReferenceMedia] = (),
        output_dir: Path | None = None,
        timeout_seconds: float = 900.0,
        on_status: Callable[[str, Mapping[str, Any]], None] | None = None,
    ) -> VideoTaskResult:
        """End-to-end: create task, poll, download. Blocking."""
        payload = self.build_create_payload(
            prompt,
            model=model,
            version=version,
            resolution=resolution,
            duration=duration,
            aspect_ratio=aspect_ratio,
            audio=audio,
            references=references,
        )
        started = time.monotonic()
        task_id = self.create_task(payload)
        video_url, raw = self.wait_for_video(task_id, timeout_seconds=timeout_seconds, on_status=on_status)
        video_path: Path | None = None
        if output_dir is not None:
            slug = f"{payload['ModelName']}{'-' + version if version else ''}"
            stamp = time.strftime("%Y%m%d_%H%M%S")
            ext = _url_extension(video_url)
            video_path = self.download(video_url, output_dir / f"{slug}_{stamp}_{task_id}{ext}")
        return VideoTaskResult(
            task_id=task_id,
            status="succeeded",
            video_url=video_url,
            video_path=video_path,
            elapsed_seconds=time.monotonic() - started,
            provider_response=raw,
        )


class TokenRouterError(RuntimeError):
    """Gateway or task failure, with server-side hints preserved."""


def _normalize_base_url(raw: str) -> str:
    url = raw.strip()
    while url.endswith("/"):
        url = url[:-1]
    if url.endswith(ROUTER_PATH):
        url = url[: -len(ROUTER_PATH)]
    return url


def _url_extension(url: str) -> str:
    base = url.split("?")[0].lower()
    for ext in _VIDEO_EXTENSIONS:
        if base.endswith(ext):
            return ext
    return ".mp4"


def _output_video_url(task_node: Mapping[str, Any]) -> str:
    """Output URL strictly from AigcVideoTask.Output.FileInfos.

    NEVER scan the whole response: Input.FileInfos echoes back the caller's
    reference URLs, which a naive URL scan mistakes for the generated video.
    """
    output = task_node.get("Output") or {}
    for info in output.get("FileInfos") or []:
        if not isinstance(info, Mapping):
            continue
        url = str(info.get("FileUrl", "") or info.get("Url", "")).strip()
        if url.startswith("http"):
            return url
    return ""


def _friendly_task_error(task_id: str, err_code: int, message: str) -> str:
    if "OutputAudioSensitiveContentDetected" in message:
        return (
            f"task {task_id} failed: generated audio was rejected by the content "
            "filter (OutputAudioSensitiveContentDetected). This is frequently a "
            "false positive — retry with OutputConfig.AudioGeneration=Disabled "
            f"(generate(audio=False)). Raw: {message}"
        )
    return f"task {task_id} failed (ErrCode={err_code}): {message}"


def _friendly_error(status: int, body: str) -> str:
    """Translate known server-side failures (mirrors tokenrouter_gateway.gd)."""
    if status == 500 and "missing video secret" in body:
        return (
            "TokenRouter server is missing the tencent-vod credential; this is a "
            "server-side configuration problem the client cannot fix. Ask the "
            f"gateway operator to configure vendor tencent-vod. Raw: {body}"
        )
    return f"HTTP {status}: {body}"
