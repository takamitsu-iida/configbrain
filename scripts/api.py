"""Asynchronous HTTP API for grounded configuration generation."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import secrets
import time
from typing import (
    Annotated,
    AsyncIterator,
    Awaitable,
    Callable,
    Literal,
    Mapping,
    Sequence,
)
from uuid import UUID, uuid4

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    Response,
    Security,
    status,
)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from dotenv import dotenv_values
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from .config_generation_jobs import (
    ConfigurationJob,
    ConfigurationJobError,
    ConfigurationJobManager,
    ConfigurationJobStore,
    IdempotencyConflictError,
    JobCapacityError,
    JobStatus,
)
from .config_generation_service import (
    ConfigurationGenerationRequest,
    ConfigurationGenerationService,
)
from .pilot_ui import (
    DuplicateFeedbackError,
    FeedbackNotFoundError,
    PilotUIError,
    append_feedback,
    load_requirement_templates,
    update_feedback,
)
from .rag import (
    DEFAULT_MODEL,
    E5Embedder,
    expand_search_plan,
    generate_configuration,
    search_expanded,
)
from .transform_markdown import load_config


LOGGER = logging.getLogger(__name__)
DEFAULT_MAX_REQUEST_BODY_BYTES = 64 * 1024
DEFAULT_MAX_UNFINISHED_JOBS = 20
DEFAULT_JOB_TIMEOUT_SECONDS = 300
REQUEST_ID_PATTERN = re.compile(r"\A[A-Za-z0-9._:-]{1,128}\Z")
WEB_UI_DIRECTORY = Path(__file__).resolve().parent.parent / "web"
NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
KnowledgePolicy = Literal["disallow", "limited", "allow"]
FeedbackVerdict = Literal[
    "ready",
    "minor_changes",
    "major_changes",
    "unusable",
]
FeedbackRating = Literal["yes", "no", "unknown"]
IssueCategory = Literal[
    "api",
    "retrieval",
    "generation",
    "requirement",
    "corpus",
    "pipeline",
]


class ConfigurationGenerationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: NonEmptyString
    product: NonEmptyString
    release: NonEmptyString | None = None
    knowledge_policy: KnowledgePolicy = "disallow"
    top_k: int = Field(default=6, ge=1, le=20)


class ConfigurationTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: str
    releases: list[str]


class ConfigurationTargetsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targets: list[ConfigurationTarget]


class RequirementTemplateItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    requirement: str


class RequirementTemplatesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    templates: list[RequirementTemplateItem]


class PilotFeedbackPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback_id: UUID
    reviewer: NonEmptyString
    requirement_anonymized: bool
    verdict: FeedbackVerdict
    expected_configuration: str = ""
    corrected_configuration: str = ""
    citations_useful: FeedbackRating
    requirement_classification_appropriate: FeedbackRating
    dangerous_or_destructive_error: bool
    issue_categories: list[IssueCategory] = Field(
        default_factory=list,
        max_length=6,
    )
    comments: str = ""

    @model_validator(mode="after")
    def validate_review_details(self) -> "PilotFeedbackPayload":
        if (
            self.verdict in {"major_changes", "unusable"}
            and not self.issue_categories
        ):
            raise ValueError(
                "major_changes and unusable require an issue category"
            )
        if (
            self.dangerous_or_destructive_error
            and not self.comments.strip()
        ):
            raise ValueError(
                "Dangerous or destructive errors require comments"
            )
        return self


class PilotFeedbackResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback_id: str
    job_id: str
    reviewed_at: str


class RetrievalEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citation_id: str
    score: float
    document_id: str
    product: str
    release: str
    heading_path: list[str]
    source_url: str = ""


class ConfigurationGenerationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: str
    knowledge_policy: KnowledgePolicy
    configuration: str
    explanation: str
    citations: list[str]
    warnings: list[str]
    inferred_commands: list[str]
    unsupported_requirements: list[str]
    missing_required_values: list[str]
    retrieval: list[RetrievalEvidence]
    search_queries: list[str]
    cli_terms: list[str]


class ConfigurationJobAcceptedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    request_id: str
    status: JobStatus
    status_url: str
    created_at: str


class ConfigurationJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    request_id: str
    status: JobStatus
    request: ConfigurationGenerationPayload
    result: ConfigurationGenerationResponse | None
    error_code: str | None
    error: str | None
    duration_ms: int | None
    created_at: str
    updated_at: str
    started_at: str | None
    completed_at: str | None


def build_runtime_service(
    qdrant_path: Path,
    model: str,
    env_file: Path,
) -> ConfigurationGenerationService:
    return ConfigurationGenerationService(
        qdrant_path=qdrant_path,
        embedder=E5Embedder(model),
        llm_config=load_config(env_file),
        search_planner=expand_search_plan,
        evidence_searcher=search_expanded,
        configuration_generator=generate_configuration,
    )


def load_configuration_targets(
    qdrant_path: Path,
    catalog_directory: Path,
) -> dict[str, tuple[str, ...]]:
    manifest_path = qdrant_path / "index-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"Could not read RAG index manifest: {manifest_path}"
        ) from error
    collections = manifest.get("collections")
    source_sha256 = manifest.get("source_sha256")
    if not isinstance(collections, dict) or not isinstance(
        source_sha256,
        dict,
    ):
        raise RuntimeError(
            f"RAG index manifest is missing target metadata: {manifest_path}"
        )
    indexed_products = {
        product
        for product in collections
        if isinstance(product, str) and product
    }
    indexed_documents = {
        document_id
        for document_id in source_sha256
        if isinstance(document_id, str) and document_id
    }
    targets: dict[str, set[str]] = {}
    try:
        catalog_paths = sorted(catalog_directory.glob("*.json"))
    except OSError as error:
        raise RuntimeError(
            f"Could not list catalog directory: {catalog_directory}"
        ) from error
    for catalog_path in catalog_paths:
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(
                f"Could not read manual catalog: {catalog_path}"
            ) from error
        if not isinstance(catalog, dict):
            raise RuntimeError(
                f"Manual catalog must be a JSON object: {catalog_path}"
            )
        product = catalog.get("product")
        release = catalog.get("release")
        documents = catalog.get("documents")
        if (
            not isinstance(product, str)
            or product not in indexed_products
            or not isinstance(release, str)
            or not release.strip()
            or not isinstance(documents, list)
        ):
            continue
        catalog_document_ids = {
            document.get("document_id")
            for document in documents
            if isinstance(document, dict)
            and isinstance(document.get("document_id"), str)
        }
        if catalog_document_ids & indexed_documents:
            targets.setdefault(product, set()).add(release.strip())
    if not targets:
        raise RuntimeError(
            "No indexed product and release combinations were found"
        )
    return {
        product: tuple(sorted(releases))
        for product, releases in sorted(targets.items())
    }


def _normalize_configuration_targets(
    targets: Mapping[str, Sequence[str]],
) -> dict[str, tuple[str, ...]]:
    normalized: dict[str, tuple[str, ...]] = {}
    for product, releases in targets.items():
        if not isinstance(product, str) or isinstance(releases, str):
            raise ValueError(
                "Configuration targets require a product and releases"
            )
        normalized_product = product.strip()
        normalized_releases = tuple(
            sorted(
                {
                    release.strip()
                    for release in releases
                    if isinstance(release, str) and release.strip()
                }
            )
        )
        if not normalized_product or not normalized_releases:
            raise ValueError(
                "Configuration targets require a product and releases"
            )
        normalized[normalized_product] = normalized_releases
    if not normalized:
        raise ValueError("At least one configuration target is required")
    return dict(sorted(normalized.items()))


def create_app(
    service: ConfigurationGenerationService | None = None,
    *,
    qdrant_path: Path = Path("data/qdrant"),
    model: str = DEFAULT_MODEL,
    job_database_path: Path = Path("data/api/jobs.sqlite3"),
    api_key: str | None = None,
    max_request_body_bytes: int = DEFAULT_MAX_REQUEST_BODY_BYTES,
    max_unfinished_jobs: int = DEFAULT_MAX_UNFINISHED_JOBS,
    job_timeout_seconds: float = DEFAULT_JOB_TIMEOUT_SECONDS,
    env_file: Path = Path(".env"),
    catalog_directory: Path = Path("data/catalog"),
    configuration_targets: Mapping[str, Sequence[str]] | None = None,
    requirement_templates_path: Path = Path(
        "templates/requirement_templates.tsv"
    ),
    pilot_feedback_path: Path = Path(
        "data/evaluation/pilot/feedback.csv"
    ),
) -> FastAPI:
    if max_request_body_bytes < 1:
        raise ValueError("max_request_body_bytes must be at least 1")
    api_key_header = APIKeyHeader(
        name="X-API-Key",
        auto_error=False,
    )
    configured_targets = (
        _normalize_configuration_targets(configuration_targets)
        if configuration_targets is not None
        else None
    )

    async def require_api_key(
        request: Request,
        provided_api_key: str | None = Security(api_key_header),
    ) -> None:
        expected_api_key: str = request.app.state.api_key
        if (
            provided_api_key is None
            or not secrets.compare_digest(provided_api_key, expected_api_key)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key",
            )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        env_values = dotenv_values(env_file) if env_file.is_file() else {}
        configured_api_key = (
            api_key
            or os.environ.get("CONFIGBRAIN_API_KEY")
            or env_values.get("CONFIGBRAIN_API_KEY")
        )
        if not configured_api_key or not configured_api_key.strip():
            raise RuntimeError("CONFIGBRAIN_API_KEY must be configured")
        app.state.api_key = configured_api_key.strip()
        app.state.configuration_targets = (
            configured_targets
            or load_configuration_targets(
                qdrant_path,
                catalog_directory,
            )
        )
        runtime_service = service or build_runtime_service(
            qdrant_path,
            model,
            env_file,
        )
        manager = ConfigurationJobManager(
            ConfigurationJobStore(job_database_path),
            runtime_service,
            max_unfinished_jobs=max_unfinished_jobs,
            execution_timeout_seconds=job_timeout_seconds,
        )
        manager.start()
        app.state.configuration_job_manager = manager
        try:
            yield
        finally:
            manager.shutdown()

    application = FastAPI(
        title="configbrain2 Configuration Generation API",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.mount(
        "/ui/assets",
        StaticFiles(directory=WEB_UI_DIRECTORY / "assets"),
        name="web-ui-assets",
    )

    @application.middleware("http")
    async def protect_request(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started = time.monotonic()

        def finish(response: Response, request_id: str) -> Response:
            response.headers["X-Request-ID"] = request_id
            if request.url.path.startswith("/ui/assets/"):
                response.headers["Cache-Control"] = "no-store"
            duration_ms = max(
                0,
                round((time.monotonic() - started) * 1000),
            )
            LOGGER.info(
                "api_request request_id=%s method=%s path=%s status=%s "
                "duration_ms=%s",
                request_id,
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
            )
            return response

        supplied_request_id = request.headers.get("X-Request-ID")
        if supplied_request_id and not REQUEST_ID_PATTERN.fullmatch(
            supplied_request_id
        ):
            response = JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "X-Request-ID has an invalid format"},
            )
            return finish(response, str(uuid4()))
        request_id = supplied_request_id or str(uuid4())
        request.state.request_id = request_id
        content_length = request.headers.get("Content-Length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                response = JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"detail": "Content-Length must be an integer"},
                )
                return finish(response, request_id)
            if declared_size > max_request_body_bytes:
                response = JSONResponse(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    content={"detail": "Request body is too large"},
                )
                return finish(response, request_id)
        body = await request.body()
        if len(body) > max_request_body_bytes:
            response = JSONResponse(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                content={"detail": "Request body is too large"},
            )
            return finish(response, request_id)
        response = await call_next(request)
        return finish(response, request_id)

    @application.get(
        "/",
        include_in_schema=False,
        response_class=FileResponse,
    )
    def get_web_ui() -> FileResponse:
        return FileResponse(
            WEB_UI_DIRECTORY / "index.html",
            media_type="text/html",
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": (
                    "default-src 'self'; "
                    "base-uri 'none'; "
                    "frame-ancestors 'none'; "
                    "form-action 'self'"
                ),
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @application.get(
        "/v1/configuration-targets",
        response_model=ConfigurationTargetsResponse,
    )
    def get_configuration_targets(
        request: Request,
    ) -> dict[str, object]:
        targets: dict[str, tuple[str, ...]] = (
            request.app.state.configuration_targets
        )
        return {
            "targets": [
                {
                    "product": product,
                    "releases": list(releases),
                }
                for product, releases in targets.items()
            ]
        }

    @application.get(
        "/v1/requirement-templates",
        response_model=RequirementTemplatesResponse,
        dependencies=[Depends(require_api_key)],
    )
    def get_requirement_templates() -> dict[str, object]:
        try:
            templates = load_requirement_templates(
                requirement_templates_path
            )
        except PilotUIError as error:
            LOGGER.error(
                "Requirement template error: error_type=%s",
                type(error).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Requirement templates are unavailable",
            ) from error
        return {
            "templates": [
                {
                    "label": template.label,
                    "requirement": template.requirement,
                }
                for template in templates
            ]
        }

    @application.post(
        "/v1/config-generations",
        response_model=ConfigurationJobAcceptedResponse,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_api_key)],
    )
    def create_configuration_job(
        payload: ConfigurationGenerationPayload,
        request: Request,
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key", max_length=128),
        ] = None,
    ) -> dict[str, object]:
        if idempotency_key is not None:
            idempotency_key = idempotency_key.strip()
            if not idempotency_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Idempotency-Key must not be empty",
                )
        targets: dict[str, tuple[str, ...]] = (
            request.app.state.configuration_targets
        )
        supported_releases = targets.get(payload.product)
        if supported_releases is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported product: {payload.product}",
            )
        if (
            payload.release is not None
            and payload.release not in supported_releases
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Unsupported release for {payload.product}: "
                    f"{payload.release}"
                ),
            )
        manager: ConfigurationJobManager = (
            request.app.state.configuration_job_manager
        )
        try:
            job, _ = manager.submit(
                ConfigurationGenerationRequest(
                    requirement=payload.requirement,
                    product=payload.product,
                    release=payload.release,
                    knowledge_policy=payload.knowledge_policy,
                    top_k=payload.top_k,
                ),
                idempotency_key,
                request.state.request_id,
            )
        except IdempotencyConflictError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(error),
            ) from error
        except JobCapacityError as error:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=str(error),
                headers={"Retry-After": "5"},
            ) from error
        except ConfigurationJobError as error:
            LOGGER.error(
                "Job service error: request_id=%s error_type=%s",
                request.state.request_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal job service error",
            ) from error
        return {
            "job_id": job.job_id,
            "request_id": job.request_id,
            "status": job.status,
            "status_url": str(
                request.url_for(
                    "get_configuration_job",
                    job_id=job.job_id,
                )
            ),
            "created_at": job.created_at,
        }

    @application.get(
        "/v1/config-generations/{job_id}",
        response_model=ConfigurationJobResponse,
        name="get_configuration_job",
        dependencies=[Depends(require_api_key)],
    )
    def get_configuration_job(
        job_id: UUID,
        request: Request,
    ) -> dict[str, object]:
        manager: ConfigurationJobManager = (
            request.app.state.configuration_job_manager
        )
        try:
            job = manager.get(str(job_id))
        except ConfigurationJobError as error:
            LOGGER.error(
                "Job service error: request_id=%s error_type=%s",
                request.state.request_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal job service error",
            ) from error
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Configuration job not found",
            )
        return _job_response(job)

    @application.post(
        "/v1/config-generations/{job_id}/feedback",
        response_model=PilotFeedbackResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_api_key)],
    )
    def create_pilot_feedback(
        job_id: UUID,
        payload: PilotFeedbackPayload,
        request: Request,
    ) -> dict[str, str]:
        manager: ConfigurationJobManager = (
            request.app.state.configuration_job_manager
        )
        try:
            job = manager.get(str(job_id))
        except ConfigurationJobError as error:
            LOGGER.error(
                "Job service error: request_id=%s error_type=%s",
                request.state.request_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal job service error",
            ) from error
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Configuration job not found",
            )
        if job.status != "completed":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Feedback requires a completed job",
            )
        reviewed_at = datetime.now(timezone.utc).isoformat()
        feedback_id = str(payload.feedback_id)
        try:
            append_feedback(
                pilot_feedback_path,
                _pilot_feedback_record(
                    payload,
                    job,
                    reviewed_at,
                ),
            )
        except DuplicateFeedbackError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(error),
            ) from error
        except PilotUIError as error:
            LOGGER.error(
                "Pilot feedback storage error: request_id=%s "
                "job_id=%s error_type=%s",
                request.state.request_id,
                job.job_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Could not store pilot feedback",
            ) from error
        return {
            "feedback_id": feedback_id,
            "job_id": job.job_id,
            "reviewed_at": reviewed_at,
        }

    @application.put(
        "/v1/config-generations/{job_id}/feedback/{feedback_id}",
        response_model=PilotFeedbackResponse,
        dependencies=[Depends(require_api_key)],
    )
    def update_pilot_feedback(
        job_id: UUID,
        feedback_id: UUID,
        payload: PilotFeedbackPayload,
        request: Request,
    ) -> dict[str, str]:
        if payload.feedback_id != feedback_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Feedback ID does not match the request path",
            )
        manager: ConfigurationJobManager = (
            request.app.state.configuration_job_manager
        )
        try:
            job = manager.get(str(job_id))
        except ConfigurationJobError as error:
            LOGGER.error(
                "Job service error: request_id=%s error_type=%s",
                request.state.request_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal job service error",
            ) from error
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Configuration job not found",
            )
        reviewed_at = datetime.now(timezone.utc).isoformat()
        try:
            update_feedback(
                pilot_feedback_path,
                _pilot_feedback_record(
                    payload,
                    job,
                    reviewed_at,
                ),
            )
        except FeedbackNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(error),
            ) from error
        except PilotUIError as error:
            LOGGER.error(
                "Pilot feedback update error: request_id=%s "
                "job_id=%s error_type=%s",
                request.state.request_id,
                job.job_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Could not update pilot feedback",
            ) from error
        return {
            "feedback_id": str(feedback_id),
            "job_id": job.job_id,
            "reviewed_at": reviewed_at,
        }

    return application


def _pilot_feedback_record(
    payload: PilotFeedbackPayload,
    job: ConfigurationJob,
    reviewed_at: str,
) -> dict[str, str]:
    return {
        "feedback_id": str(payload.feedback_id),
        "job_id": job.job_id,
        "reviewer": payload.reviewer,
        "reviewed_at": reviewed_at,
        "requirement_anonymized": (
            "yes" if payload.requirement_anonymized else "no"
        ),
        "verdict": payload.verdict,
        "expected_configuration": payload.expected_configuration,
        "corrected_configuration": payload.corrected_configuration,
        "citations_useful": payload.citations_useful,
        "requirement_classification_appropriate": (
            payload.requirement_classification_appropriate
        ),
        "dangerous_or_destructive_error": (
            "yes" if payload.dangerous_or_destructive_error else "no"
        ),
        "issue_categories": ";".join(
            dict.fromkeys(payload.issue_categories)
        ),
        "comments": payload.comments,
    }


def _job_response(job: ConfigurationJob) -> dict[str, object]:
    return {
        "job_id": job.job_id,
        "request_id": job.request_id,
        "status": job.status,
        "request": {
            "requirement": job.request.requirement,
            "product": job.request.product,
            "release": job.request.release,
            "knowledge_policy": job.request.knowledge_policy,
            "top_k": job.request.top_k,
        },
        "result": job.result,
        "error_code": job.error_code,
        "error": job.error,
        "duration_ms": job.duration_ms,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
    }


app = create_app()
