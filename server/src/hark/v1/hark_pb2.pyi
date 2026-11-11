from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ModelState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MODEL_STATE_UNSPECIFIED: _ClassVar[ModelState]
    MODEL_STATE_LOADING: _ClassVar[ModelState]
    MODEL_STATE_READY: _ClassVar[ModelState]
    MODEL_STATE_COLD: _ClassVar[ModelState]
    MODEL_STATE_FAILED: _ClassVar[ModelState]

class StreamingMode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    STREAMING_MODE_UNSPECIFIED: _ClassVar[StreamingMode]
    STREAMING_MODE_BUFFERED: _ClassVar[StreamingMode]
    STREAMING_MODE_INCREMENTAL: _ClassVar[StreamingMode]

class AudioEncoding(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    AUDIO_ENCODING_UNSPECIFIED: _ClassVar[AudioEncoding]
    AUDIO_ENCODING_PCM_S16LE: _ClassVar[AudioEncoding]
    AUDIO_ENCODING_OPUS: _ClassVar[AudioEncoding]

class BiasMechanism(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    BIAS_MECHANISM_UNSPECIFIED: _ClassVar[BiasMechanism]
    BIAS_MECHANISM_NONE: _ClassVar[BiasMechanism]
    BIAS_MECHANISM_DECODE_TIME_BOOST: _ClassVar[BiasMechanism]
    BIAS_MECHANISM_ACOUSTIC_RESCORE: _ClassVar[BiasMechanism]
    BIAS_MECHANISM_PROMPT: _ClassVar[BiasMechanism]

class MidstreamUpdate(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MIDSTREAM_UPDATE_UNSPECIFIED: _ClassVar[MidstreamUpdate]
    MIDSTREAM_UPDATE_UNSUPPORTED: _ClassVar[MidstreamUpdate]
    MIDSTREAM_UPDATE_FULL: _ClassVar[MidstreamUpdate]
    MIDSTREAM_UPDATE_FORWARD_ONLY: _ClassVar[MidstreamUpdate]

class ScoreKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SCORE_KIND_UNSPECIFIED: _ClassVar[ScoreKind]
    SCORE_KIND_LOG_PROB: _ClassVar[ScoreKind]
    SCORE_KIND_LOG_PROB_PER_TOKEN: _ClassVar[ScoreKind]
    SCORE_KIND_POSTERIOR: _ClassVar[ScoreKind]
    SCORE_KIND_RANK_ONLY: _ClassVar[ScoreKind]

class TimestampSource(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TIMESTAMP_SOURCE_UNSPECIFIED: _ClassVar[TimestampSource]
    TIMESTAMP_SOURCE_NONE: _ClassVar[TimestampSource]
    TIMESTAMP_SOURCE_DECODER_EMISSION: _ClassVar[TimestampSource]
    TIMESTAMP_SOURCE_FORCED_ALIGNMENT: _ClassVar[TimestampSource]
    TIMESTAMP_SOURCE_PROVIDER: _ClassVar[TimestampSource]

class AnchorCost(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ANCHOR_COST_UNSPECIFIED: _ClassVar[AnchorCost]
    ANCHOR_COST_UNAVAILABLE: _ClassVar[AnchorCost]
    ANCHOR_COST_FREE: _ClassVar[AnchorCost]
    ANCHOR_COST_DECODER_ONLY: _ClassVar[AnchorCost]
    ANCHOR_COST_FULL_PASS: _ClassVar[AnchorCost]

class AnchorMode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ANCHOR_MODE_UNSPECIFIED: _ClassVar[AnchorMode]
    ANCHOR_MODE_OFF: _ClassVar[AnchorMode]
    ANCHOR_MODE_IF_CHEAP: _ClassVar[AnchorMode]
    ANCHOR_MODE_ALWAYS: _ClassVar[AnchorMode]

class FieldStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    FIELD_STATUS_UNSPECIFIED: _ClassVar[FieldStatus]
    FIELD_STATUS_PRESENT: _ClassVar[FieldStatus]
    FIELD_STATUS_NOT_REQUESTED: _ClassVar[FieldStatus]
    FIELD_STATUS_SKIPPED_BUDGET: _ClassVar[FieldStatus]
    FIELD_STATUS_UNSUPPORTED: _ClassVar[FieldStatus]
    FIELD_STATUS_FAILED: _ClassVar[FieldStatus]

class SessionEndReason(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SESSION_END_REASON_UNSPECIFIED: _ClassVar[SessionEndReason]
    SESSION_END_REASON_CANCELLED: _ClassVar[SessionEndReason]
    SESSION_END_REASON_REJECTED: _ClassVar[SessionEndReason]
    SESSION_END_REASON_FAILED: _ClassVar[SessionEndReason]

class HypothesisOrigin(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    HYPOTHESIS_ORIGIN_UNSPECIFIED: _ClassVar[HypothesisOrigin]
    HYPOTHESIS_ORIGIN_BIASED: _ClassVar[HypothesisOrigin]
    HYPOTHESIS_ORIGIN_UNBIASED: _ClassVar[HypothesisOrigin]
    HYPOTHESIS_ORIGIN_BEAM: _ClassVar[HypothesisOrigin]
    HYPOTHESIS_ORIGIN_RESCORED: _ClassVar[HypothesisOrigin]

class ContextSource(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CONTEXT_SOURCE_UNSPECIFIED: _ClassVar[ContextSource]
    CONTEXT_SOURCE_USER_DICTIONARY: _ClassVar[ContextSource]
    CONTEXT_SOURCE_HISTORY: _ClassVar[ContextSource]
    CONTEXT_SOURCE_FOCUSED_TEXT: _ClassVar[ContextSource]
    CONTEXT_SOURCE_SELECTION: _ClassVar[ContextSource]
    CONTEXT_SOURCE_WINDOW_TITLE: _ClassVar[ContextSource]
    CONTEXT_SOURCE_TERMINAL_TEXT: _ClassVar[ContextSource]
    CONTEXT_SOURCE_GIT_REPO: _ClassVar[ContextSource]
    CONTEXT_SOURCE_AGENT_SESSION: _ClassVar[ContextSource]
    CONTEXT_SOURCE_OCR: _ClassVar[ContextSource]
    CONTEXT_SOURCE_APP_PROFILE: _ClassVar[ContextSource]

class TermKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TERM_KIND_UNSPECIFIED: _ClassVar[TermKind]
    TERM_KIND_WORD: _ClassVar[TermKind]
    TERM_KIND_PERSON_NAME: _ClassVar[TermKind]
    TERM_KIND_PRODUCT: _ClassVar[TermKind]
    TERM_KIND_IDENTIFIER: _ClassVar[TermKind]
    TERM_KIND_ACRONYM: _ClassVar[TermKind]
    TERM_KIND_PATH: _ClassVar[TermKind]
    TERM_KIND_COMMAND: _ClassVar[TermKind]

class AliasKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ALIAS_KIND_UNSPECIFIED: _ClassVar[AliasKind]
    ALIAS_KIND_SPOKEN_FORM: _ClassVar[AliasKind]
    ALIAS_KIND_SPELLED: _ClassVar[AliasKind]
    ALIAS_KIND_MISRECOGNITION: _ClassVar[AliasKind]

class EndReason(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    END_REASON_UNSPECIFIED: _ClassVar[EndReason]
    END_REASON_KEY_UP: _ClassVar[EndReason]
    END_REASON_VAD_ENDPOINT: _ClassVar[EndReason]
    END_REASON_MAX_DURATION: _ClassVar[EndReason]
    END_REASON_TOGGLE: _ClassVar[EndReason]

class CancelReason(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CANCEL_REASON_UNSPECIFIED: _ClassVar[CancelReason]
    CANCEL_REASON_USER_ABORT: _ClassVar[CancelReason]
    CANCEL_REASON_TOO_SHORT: _ClassVar[CancelReason]
    CANCEL_REASON_SWITCHED_PROVIDER: _ClassVar[CancelReason]
    CANCEL_REASON_SHUTDOWN: _ClassVar[CancelReason]

class BiasHitDecision(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    BIAS_HIT_DECISION_UNSPECIFIED: _ClassVar[BiasHitDecision]
    BIAS_HIT_DECISION_APPLIED: _ClassVar[BiasHitDecision]
    BIAS_HIT_DECISION_ALREADY_IN_ANCHOR: _ClassVar[BiasHitDecision]
    BIAS_HIT_DECISION_REJECTED: _ClassVar[BiasHitDecision]

class WarningCode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    WARNING_CODE_UNSPECIFIED: _ClassVar[WarningCode]
    WARNING_CODE_BIAS_TERMS_TRUNCATED: _ClassVar[WarningCode]
    WARNING_CODE_ALIASES_DROPPED: _ClassVar[WarningCode]
    WARNING_CODE_FREE_TEXT_TRUNCATED: _ClassVar[WarningCode]
    WARNING_CODE_FEATURE_DEGRADED: _ClassVar[WarningCode]
    WARNING_CODE_AUDIO_GAP: _ClassVar[WarningCode]
    WARNING_CODE_LANGUAGE_FALLBACK: _ClassVar[WarningCode]
    WARNING_CODE_CONTEXT_IGNORED: _ClassVar[WarningCode]
    WARNING_CODE_FINALIZE_BUDGET_TIGHT: _ClassVar[WarningCode]

class ErrorCode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ERROR_CODE_UNSPECIFIED: _ClassVar[ErrorCode]
    ERROR_CODE_MODEL_NOT_FOUND: _ClassVar[ErrorCode]
    ERROR_CODE_MODEL_NOT_READY: _ClassVar[ErrorCode]
    ERROR_CODE_OVERLOADED: _ClassVar[ErrorCode]
    ERROR_CODE_UNSUPPORTED_AUDIO_FORMAT: _ClassVar[ErrorCode]
    ERROR_CODE_UNSUPPORTED_LANGUAGE: _ClassVar[ErrorCode]
    ERROR_CODE_REQUIRED_FEATURE_UNAVAILABLE: _ClassVar[ErrorCode]
    ERROR_CODE_PROTOCOL_VIOLATION: _ClassVar[ErrorCode]
    ERROR_CODE_AUDIO_TOO_LONG: _ClassVar[ErrorCode]
    ERROR_CODE_BACKEND_FAILURE: _ClassVar[ErrorCode]
    ERROR_CODE_SUPERSEDED: _ClassVar[ErrorCode]
    ERROR_CODE_UNAUTHENTICATED: _ClassVar[ErrorCode]
    ERROR_CODE_PRIVACY_UNSATISFIABLE: _ClassVar[ErrorCode]
    ERROR_CODE_CANCELLED: _ClassVar[ErrorCode]

class EditRejectionReason(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    EDIT_REJECTION_REASON_UNSPECIFIED: _ClassVar[EditRejectionReason]
    EDIT_REJECTION_REASON_NOT_FOUND: _ClassVar[EditRejectionReason]
    EDIT_REJECTION_REASON_NOT_UNIQUE: _ClassVar[EditRejectionReason]
    EDIT_REJECTION_REASON_OVERLAP: _ClassVar[EditRejectionReason]
    EDIT_REJECTION_REASON_PROTECTED_SPAN: _ClassVar[EditRejectionReason]
    EDIT_REJECTION_REASON_KIND_NOT_ALLOWED: _ClassVar[EditRejectionReason]
    EDIT_REJECTION_REASON_INVARIANT: _ClassVar[EditRejectionReason]
    EDIT_REJECTION_REASON_NO_OP: _ClassVar[EditRejectionReason]
    EDIT_REJECTION_REASON_GLOBAL: _ClassVar[EditRejectionReason]

class ProtectedSpanKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PROTECTED_SPAN_KIND_UNSPECIFIED: _ClassVar[ProtectedSpanKind]
    PROTECTED_SPAN_KIND_IDENTIFIER: _ClassVar[ProtectedSpanKind]
    PROTECTED_SPAN_KIND_NUMBER: _ClassVar[ProtectedSpanKind]
    PROTECTED_SPAN_KIND_NEGATION: _ClassVar[ProtectedSpanKind]
    PROTECTED_SPAN_KIND_BIAS_TERM: _ClassVar[ProtectedSpanKind]
    PROTECTED_SPAN_KIND_URL: _ClassVar[ProtectedSpanKind]
    PROTECTED_SPAN_KIND_PATH: _ClassVar[ProtectedSpanKind]
    PROTECTED_SPAN_KIND_CODE: _ClassVar[ProtectedSpanKind]
    PROTECTED_SPAN_KIND_QUOTED: _ClassVar[ProtectedSpanKind]
    PROTECTED_SPAN_KIND_USER_LOCKED: _ClassVar[ProtectedSpanKind]

class EditKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    EDIT_KIND_UNSPECIFIED: _ClassVar[EditKind]
    EDIT_KIND_PUNCTUATION: _ClassVar[EditKind]
    EDIT_KIND_CAPITALIZATION: _ClassVar[EditKind]
    EDIT_KIND_SPACING: _ClassVar[EditKind]
    EDIT_KIND_FILLER_REMOVAL: _ClassVar[EditKind]
    EDIT_KIND_NUMBER_FORMAT: _ClassVar[EditKind]
    EDIT_KIND_SELF_CORRECTION: _ClassVar[EditKind]
    EDIT_KIND_GRAMMAR: _ClassVar[EditKind]
MODEL_STATE_UNSPECIFIED: ModelState
MODEL_STATE_LOADING: ModelState
MODEL_STATE_READY: ModelState
MODEL_STATE_COLD: ModelState
MODEL_STATE_FAILED: ModelState
STREAMING_MODE_UNSPECIFIED: StreamingMode
STREAMING_MODE_BUFFERED: StreamingMode
STREAMING_MODE_INCREMENTAL: StreamingMode
AUDIO_ENCODING_UNSPECIFIED: AudioEncoding
AUDIO_ENCODING_PCM_S16LE: AudioEncoding
AUDIO_ENCODING_OPUS: AudioEncoding
BIAS_MECHANISM_UNSPECIFIED: BiasMechanism
BIAS_MECHANISM_NONE: BiasMechanism
BIAS_MECHANISM_DECODE_TIME_BOOST: BiasMechanism
BIAS_MECHANISM_ACOUSTIC_RESCORE: BiasMechanism
BIAS_MECHANISM_PROMPT: BiasMechanism
MIDSTREAM_UPDATE_UNSPECIFIED: MidstreamUpdate
MIDSTREAM_UPDATE_UNSUPPORTED: MidstreamUpdate
MIDSTREAM_UPDATE_FULL: MidstreamUpdate
MIDSTREAM_UPDATE_FORWARD_ONLY: MidstreamUpdate
SCORE_KIND_UNSPECIFIED: ScoreKind
SCORE_KIND_LOG_PROB: ScoreKind
SCORE_KIND_LOG_PROB_PER_TOKEN: ScoreKind
SCORE_KIND_POSTERIOR: ScoreKind
SCORE_KIND_RANK_ONLY: ScoreKind
TIMESTAMP_SOURCE_UNSPECIFIED: TimestampSource
TIMESTAMP_SOURCE_NONE: TimestampSource
TIMESTAMP_SOURCE_DECODER_EMISSION: TimestampSource
TIMESTAMP_SOURCE_FORCED_ALIGNMENT: TimestampSource
TIMESTAMP_SOURCE_PROVIDER: TimestampSource
ANCHOR_COST_UNSPECIFIED: AnchorCost
ANCHOR_COST_UNAVAILABLE: AnchorCost
ANCHOR_COST_FREE: AnchorCost
ANCHOR_COST_DECODER_ONLY: AnchorCost
ANCHOR_COST_FULL_PASS: AnchorCost
ANCHOR_MODE_UNSPECIFIED: AnchorMode
ANCHOR_MODE_OFF: AnchorMode
ANCHOR_MODE_IF_CHEAP: AnchorMode
ANCHOR_MODE_ALWAYS: AnchorMode
FIELD_STATUS_UNSPECIFIED: FieldStatus
FIELD_STATUS_PRESENT: FieldStatus
FIELD_STATUS_NOT_REQUESTED: FieldStatus
FIELD_STATUS_SKIPPED_BUDGET: FieldStatus
FIELD_STATUS_UNSUPPORTED: FieldStatus
FIELD_STATUS_FAILED: FieldStatus
SESSION_END_REASON_UNSPECIFIED: SessionEndReason
SESSION_END_REASON_CANCELLED: SessionEndReason
SESSION_END_REASON_REJECTED: SessionEndReason
SESSION_END_REASON_FAILED: SessionEndReason
HYPOTHESIS_ORIGIN_UNSPECIFIED: HypothesisOrigin
HYPOTHESIS_ORIGIN_BIASED: HypothesisOrigin
HYPOTHESIS_ORIGIN_UNBIASED: HypothesisOrigin
HYPOTHESIS_ORIGIN_BEAM: HypothesisOrigin
HYPOTHESIS_ORIGIN_RESCORED: HypothesisOrigin
CONTEXT_SOURCE_UNSPECIFIED: ContextSource
CONTEXT_SOURCE_USER_DICTIONARY: ContextSource
CONTEXT_SOURCE_HISTORY: ContextSource
CONTEXT_SOURCE_FOCUSED_TEXT: ContextSource
CONTEXT_SOURCE_SELECTION: ContextSource
CONTEXT_SOURCE_WINDOW_TITLE: ContextSource
CONTEXT_SOURCE_TERMINAL_TEXT: ContextSource
CONTEXT_SOURCE_GIT_REPO: ContextSource
CONTEXT_SOURCE_AGENT_SESSION: ContextSource
CONTEXT_SOURCE_OCR: ContextSource
CONTEXT_SOURCE_APP_PROFILE: ContextSource
TERM_KIND_UNSPECIFIED: TermKind
TERM_KIND_WORD: TermKind
TERM_KIND_PERSON_NAME: TermKind
TERM_KIND_PRODUCT: TermKind
TERM_KIND_IDENTIFIER: TermKind
TERM_KIND_ACRONYM: TermKind
TERM_KIND_PATH: TermKind
TERM_KIND_COMMAND: TermKind
ALIAS_KIND_UNSPECIFIED: AliasKind
ALIAS_KIND_SPOKEN_FORM: AliasKind
ALIAS_KIND_SPELLED: AliasKind
ALIAS_KIND_MISRECOGNITION: AliasKind
END_REASON_UNSPECIFIED: EndReason
END_REASON_KEY_UP: EndReason
END_REASON_VAD_ENDPOINT: EndReason
END_REASON_MAX_DURATION: EndReason
END_REASON_TOGGLE: EndReason
CANCEL_REASON_UNSPECIFIED: CancelReason
CANCEL_REASON_USER_ABORT: CancelReason
CANCEL_REASON_TOO_SHORT: CancelReason
CANCEL_REASON_SWITCHED_PROVIDER: CancelReason
CANCEL_REASON_SHUTDOWN: CancelReason
BIAS_HIT_DECISION_UNSPECIFIED: BiasHitDecision
BIAS_HIT_DECISION_APPLIED: BiasHitDecision
BIAS_HIT_DECISION_ALREADY_IN_ANCHOR: BiasHitDecision
BIAS_HIT_DECISION_REJECTED: BiasHitDecision
WARNING_CODE_UNSPECIFIED: WarningCode
WARNING_CODE_BIAS_TERMS_TRUNCATED: WarningCode
WARNING_CODE_ALIASES_DROPPED: WarningCode
WARNING_CODE_FREE_TEXT_TRUNCATED: WarningCode
WARNING_CODE_FEATURE_DEGRADED: WarningCode
WARNING_CODE_AUDIO_GAP: WarningCode
WARNING_CODE_LANGUAGE_FALLBACK: WarningCode
WARNING_CODE_CONTEXT_IGNORED: WarningCode
WARNING_CODE_FINALIZE_BUDGET_TIGHT: WarningCode
ERROR_CODE_UNSPECIFIED: ErrorCode
ERROR_CODE_MODEL_NOT_FOUND: ErrorCode
ERROR_CODE_MODEL_NOT_READY: ErrorCode
ERROR_CODE_OVERLOADED: ErrorCode
ERROR_CODE_UNSUPPORTED_AUDIO_FORMAT: ErrorCode
ERROR_CODE_UNSUPPORTED_LANGUAGE: ErrorCode
ERROR_CODE_REQUIRED_FEATURE_UNAVAILABLE: ErrorCode
ERROR_CODE_PROTOCOL_VIOLATION: ErrorCode
ERROR_CODE_AUDIO_TOO_LONG: ErrorCode
ERROR_CODE_BACKEND_FAILURE: ErrorCode
ERROR_CODE_SUPERSEDED: ErrorCode
ERROR_CODE_UNAUTHENTICATED: ErrorCode
ERROR_CODE_PRIVACY_UNSATISFIABLE: ErrorCode
ERROR_CODE_CANCELLED: ErrorCode
EDIT_REJECTION_REASON_UNSPECIFIED: EditRejectionReason
EDIT_REJECTION_REASON_NOT_FOUND: EditRejectionReason
EDIT_REJECTION_REASON_NOT_UNIQUE: EditRejectionReason
EDIT_REJECTION_REASON_OVERLAP: EditRejectionReason
EDIT_REJECTION_REASON_PROTECTED_SPAN: EditRejectionReason
EDIT_REJECTION_REASON_KIND_NOT_ALLOWED: EditRejectionReason
EDIT_REJECTION_REASON_INVARIANT: EditRejectionReason
EDIT_REJECTION_REASON_NO_OP: EditRejectionReason
EDIT_REJECTION_REASON_GLOBAL: EditRejectionReason
PROTECTED_SPAN_KIND_UNSPECIFIED: ProtectedSpanKind
PROTECTED_SPAN_KIND_IDENTIFIER: ProtectedSpanKind
PROTECTED_SPAN_KIND_NUMBER: ProtectedSpanKind
PROTECTED_SPAN_KIND_NEGATION: ProtectedSpanKind
PROTECTED_SPAN_KIND_BIAS_TERM: ProtectedSpanKind
PROTECTED_SPAN_KIND_URL: ProtectedSpanKind
PROTECTED_SPAN_KIND_PATH: ProtectedSpanKind
PROTECTED_SPAN_KIND_CODE: ProtectedSpanKind
PROTECTED_SPAN_KIND_QUOTED: ProtectedSpanKind
PROTECTED_SPAN_KIND_USER_LOCKED: ProtectedSpanKind
EDIT_KIND_UNSPECIFIED: EditKind
EDIT_KIND_PUNCTUATION: EditKind
EDIT_KIND_CAPITALIZATION: EditKind
EDIT_KIND_SPACING: EditKind
EDIT_KIND_FILLER_REMOVAL: EditKind
EDIT_KIND_NUMBER_FORMAT: EditKind
EDIT_KIND_SELF_CORRECTION: EditKind
EDIT_KIND_GRAMMAR: EditKind

class GetCapabilitiesRequest(_message.Message):
    __slots__ = ("client_version", "client_protocol_minor")
    CLIENT_VERSION_FIELD_NUMBER: _ClassVar[int]
    CLIENT_PROTOCOL_MINOR_FIELD_NUMBER: _ClassVar[int]
    client_version: str
    client_protocol_minor: int
    def __init__(self, client_version: _Optional[str] = ..., client_protocol_minor: _Optional[int] = ...) -> None: ...

class ProtocolVersion(_message.Message):
    __slots__ = ("major", "minor")
    MAJOR_FIELD_NUMBER: _ClassVar[int]
    MINOR_FIELD_NUMBER: _ClassVar[int]
    major: int
    minor: int
    def __init__(self, major: _Optional[int] = ..., minor: _Optional[int] = ...) -> None: ...

class Capabilities(_message.Message):
    __slots__ = ("protocol", "server", "models", "cleanup_profiles", "limits", "etag", "features", "privacy")
    PROTOCOL_FIELD_NUMBER: _ClassVar[int]
    SERVER_FIELD_NUMBER: _ClassVar[int]
    MODELS_FIELD_NUMBER: _ClassVar[int]
    CLEANUP_PROFILES_FIELD_NUMBER: _ClassVar[int]
    LIMITS_FIELD_NUMBER: _ClassVar[int]
    ETAG_FIELD_NUMBER: _ClassVar[int]
    FEATURES_FIELD_NUMBER: _ClassVar[int]
    PRIVACY_FIELD_NUMBER: _ClassVar[int]
    protocol: ProtocolVersion
    server: ServerInfo
    models: _containers.RepeatedCompositeFieldContainer[ModelCapabilities]
    cleanup_profiles: _containers.RepeatedCompositeFieldContainer[CleanupProfile]
    limits: ServerLimits
    etag: str
    features: _containers.RepeatedScalarFieldContainer[str]
    privacy: PrivacyCapabilities
    def __init__(self, protocol: _Optional[_Union[ProtocolVersion, _Mapping]] = ..., server: _Optional[_Union[ServerInfo, _Mapping]] = ..., models: _Optional[_Iterable[_Union[ModelCapabilities, _Mapping]]] = ..., cleanup_profiles: _Optional[_Iterable[_Union[CleanupProfile, _Mapping]]] = ..., limits: _Optional[_Union[ServerLimits, _Mapping]] = ..., etag: _Optional[str] = ..., features: _Optional[_Iterable[str]] = ..., privacy: _Optional[_Union[PrivacyCapabilities, _Mapping]] = ...) -> None: ...

class PrivacyCapabilities(_message.Message):
    __slots__ = ("audio_retention", "transcript_retention", "context_logging", "eval_capture", "replay_cache")
    AUDIO_RETENTION_FIELD_NUMBER: _ClassVar[int]
    TRANSCRIPT_RETENTION_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_LOGGING_FIELD_NUMBER: _ClassVar[int]
    EVAL_CAPTURE_FIELD_NUMBER: _ClassVar[int]
    REPLAY_CACHE_FIELD_NUMBER: _ClassVar[int]
    audio_retention: bool
    transcript_retention: bool
    context_logging: bool
    eval_capture: bool
    replay_cache: bool
    def __init__(self, audio_retention: _Optional[bool] = ..., transcript_retention: _Optional[bool] = ..., context_logging: _Optional[bool] = ..., eval_capture: _Optional[bool] = ..., replay_cache: _Optional[bool] = ...) -> None: ...

class ServerInfo(_message.Message):
    __slots__ = ("name", "implementation", "accelerator", "started_unix_ms", "instance_id")
    NAME_FIELD_NUMBER: _ClassVar[int]
    IMPLEMENTATION_FIELD_NUMBER: _ClassVar[int]
    ACCELERATOR_FIELD_NUMBER: _ClassVar[int]
    STARTED_UNIX_MS_FIELD_NUMBER: _ClassVar[int]
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    name: str
    implementation: str
    accelerator: str
    started_unix_ms: int
    instance_id: str
    def __init__(self, name: _Optional[str] = ..., implementation: _Optional[str] = ..., accelerator: _Optional[str] = ..., started_unix_ms: _Optional[int] = ..., instance_id: _Optional[str] = ...) -> None: ...

class ServerLimits(_message.Message):
    __slots__ = ("max_concurrent_sessions", "max_audio_ms", "max_message_bytes", "replay_cache_ttl_ms")
    MAX_CONCURRENT_SESSIONS_FIELD_NUMBER: _ClassVar[int]
    MAX_AUDIO_MS_FIELD_NUMBER: _ClassVar[int]
    MAX_MESSAGE_BYTES_FIELD_NUMBER: _ClassVar[int]
    REPLAY_CACHE_TTL_MS_FIELD_NUMBER: _ClassVar[int]
    max_concurrent_sessions: int
    max_audio_ms: int
    max_message_bytes: int
    replay_cache_ttl_ms: int
    def __init__(self, max_concurrent_sessions: _Optional[int] = ..., max_audio_ms: _Optional[int] = ..., max_message_bytes: _Optional[int] = ..., replay_cache_ttl_ms: _Optional[int] = ...) -> None: ...

class ModelCapabilities(_message.Message):
    __slots__ = ("model_id", "display_name", "family", "state", "is_default", "languages", "language_detection", "streaming_mode", "partials", "audio_formats", "bias", "free_text", "n_best", "timestamps", "anchor_cost", "punctuation", "casing", "inverse_text_normalization", "perf")
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    FAMILY_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    IS_DEFAULT_FIELD_NUMBER: _ClassVar[int]
    LANGUAGES_FIELD_NUMBER: _ClassVar[int]
    LANGUAGE_DETECTION_FIELD_NUMBER: _ClassVar[int]
    STREAMING_MODE_FIELD_NUMBER: _ClassVar[int]
    PARTIALS_FIELD_NUMBER: _ClassVar[int]
    AUDIO_FORMATS_FIELD_NUMBER: _ClassVar[int]
    BIAS_FIELD_NUMBER: _ClassVar[int]
    FREE_TEXT_FIELD_NUMBER: _ClassVar[int]
    N_BEST_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMPS_FIELD_NUMBER: _ClassVar[int]
    ANCHOR_COST_FIELD_NUMBER: _ClassVar[int]
    PUNCTUATION_FIELD_NUMBER: _ClassVar[int]
    CASING_FIELD_NUMBER: _ClassVar[int]
    INVERSE_TEXT_NORMALIZATION_FIELD_NUMBER: _ClassVar[int]
    PERF_FIELD_NUMBER: _ClassVar[int]
    model_id: str
    display_name: str
    family: str
    state: ModelState
    is_default: bool
    languages: _containers.RepeatedScalarFieldContainer[str]
    language_detection: bool
    streaming_mode: StreamingMode
    partials: bool
    audio_formats: _containers.RepeatedCompositeFieldContainer[AudioFormatSupport]
    bias: BiasCapabilities
    free_text: FreeTextCapabilities
    n_best: NBestCapabilities
    timestamps: TimestampCapabilities
    anchor_cost: AnchorCost
    punctuation: bool
    casing: bool
    inverse_text_normalization: bool
    perf: PerfEstimate
    def __init__(self, model_id: _Optional[str] = ..., display_name: _Optional[str] = ..., family: _Optional[str] = ..., state: _Optional[_Union[ModelState, str]] = ..., is_default: _Optional[bool] = ..., languages: _Optional[_Iterable[str]] = ..., language_detection: _Optional[bool] = ..., streaming_mode: _Optional[_Union[StreamingMode, str]] = ..., partials: _Optional[bool] = ..., audio_formats: _Optional[_Iterable[_Union[AudioFormatSupport, _Mapping]]] = ..., bias: _Optional[_Union[BiasCapabilities, _Mapping]] = ..., free_text: _Optional[_Union[FreeTextCapabilities, _Mapping]] = ..., n_best: _Optional[_Union[NBestCapabilities, _Mapping]] = ..., timestamps: _Optional[_Union[TimestampCapabilities, _Mapping]] = ..., anchor_cost: _Optional[_Union[AnchorCost, str]] = ..., punctuation: _Optional[bool] = ..., casing: _Optional[bool] = ..., inverse_text_normalization: _Optional[bool] = ..., perf: _Optional[_Union[PerfEstimate, _Mapping]] = ...) -> None: ...

class AudioFormatSupport(_message.Message):
    __slots__ = ("encoding", "sample_rates_hz")
    ENCODING_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_RATES_HZ_FIELD_NUMBER: _ClassVar[int]
    encoding: AudioEncoding
    sample_rates_hz: _containers.RepeatedScalarFieldContainer[int]
    def __init__(self, encoding: _Optional[_Union[AudioEncoding, str]] = ..., sample_rates_hz: _Optional[_Iterable[int]] = ...) -> None: ...

class BiasCapabilities(_message.Message):
    __slots__ = ("mechanism", "max_terms", "max_aliases_per_term", "max_term_bytes", "max_total_tokens", "per_term_weight", "aliases_supported", "case_insensitive", "midstream_update", "bias_with_n_best", "hit_scores", "boost_min", "boost_max")
    MECHANISM_FIELD_NUMBER: _ClassVar[int]
    MAX_TERMS_FIELD_NUMBER: _ClassVar[int]
    MAX_ALIASES_PER_TERM_FIELD_NUMBER: _ClassVar[int]
    MAX_TERM_BYTES_FIELD_NUMBER: _ClassVar[int]
    MAX_TOTAL_TOKENS_FIELD_NUMBER: _ClassVar[int]
    PER_TERM_WEIGHT_FIELD_NUMBER: _ClassVar[int]
    ALIASES_SUPPORTED_FIELD_NUMBER: _ClassVar[int]
    CASE_INSENSITIVE_FIELD_NUMBER: _ClassVar[int]
    MIDSTREAM_UPDATE_FIELD_NUMBER: _ClassVar[int]
    BIAS_WITH_N_BEST_FIELD_NUMBER: _ClassVar[int]
    HIT_SCORES_FIELD_NUMBER: _ClassVar[int]
    BOOST_MIN_FIELD_NUMBER: _ClassVar[int]
    BOOST_MAX_FIELD_NUMBER: _ClassVar[int]
    mechanism: BiasMechanism
    max_terms: int
    max_aliases_per_term: int
    max_term_bytes: int
    max_total_tokens: int
    per_term_weight: bool
    aliases_supported: bool
    case_insensitive: bool
    midstream_update: MidstreamUpdate
    bias_with_n_best: bool
    hit_scores: bool
    boost_min: float
    boost_max: float
    def __init__(self, mechanism: _Optional[_Union[BiasMechanism, str]] = ..., max_terms: _Optional[int] = ..., max_aliases_per_term: _Optional[int] = ..., max_term_bytes: _Optional[int] = ..., max_total_tokens: _Optional[int] = ..., per_term_weight: _Optional[bool] = ..., aliases_supported: _Optional[bool] = ..., case_insensitive: _Optional[bool] = ..., midstream_update: _Optional[_Union[MidstreamUpdate, str]] = ..., bias_with_n_best: _Optional[bool] = ..., hit_scores: _Optional[bool] = ..., boost_min: _Optional[float] = ..., boost_max: _Optional[float] = ...) -> None: ...

class FreeTextCapabilities(_message.Message):
    __slots__ = ("description", "preceding_text", "max_bytes", "echo_risk")
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    PRECEDING_TEXT_FIELD_NUMBER: _ClassVar[int]
    MAX_BYTES_FIELD_NUMBER: _ClassVar[int]
    ECHO_RISK_FIELD_NUMBER: _ClassVar[int]
    description: bool
    preceding_text: bool
    max_bytes: int
    echo_risk: bool
    def __init__(self, description: _Optional[bool] = ..., preceding_text: _Optional[bool] = ..., max_bytes: _Optional[int] = ..., echo_risk: _Optional[bool] = ...) -> None: ...

class NBestCapabilities(_message.Message):
    __slots__ = ("max_n", "score_kind")
    MAX_N_FIELD_NUMBER: _ClassVar[int]
    SCORE_KIND_FIELD_NUMBER: _ClassVar[int]
    max_n: int
    score_kind: ScoreKind
    def __init__(self, max_n: _Optional[int] = ..., score_kind: _Optional[_Union[ScoreKind, str]] = ...) -> None: ...

class TimestampCapabilities(_message.Message):
    __slots__ = ("source", "resolution_ms", "word_confidence", "confidence_calibrated")
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    RESOLUTION_MS_FIELD_NUMBER: _ClassVar[int]
    WORD_CONFIDENCE_FIELD_NUMBER: _ClassVar[int]
    CONFIDENCE_CALIBRATED_FIELD_NUMBER: _ClassVar[int]
    source: TimestampSource
    resolution_ms: int
    word_confidence: bool
    confidence_calibrated: bool
    def __init__(self, source: _Optional[_Union[TimestampSource, str]] = ..., resolution_ms: _Optional[int] = ..., word_confidence: _Optional[bool] = ..., confidence_calibrated: _Optional[bool] = ...) -> None: ...

class PerfEstimate(_message.Message):
    __slots__ = ("rtfx", "finalize_ms_10s", "bias_compile_ms")
    RTFX_FIELD_NUMBER: _ClassVar[int]
    FINALIZE_MS_10S_FIELD_NUMBER: _ClassVar[int]
    BIAS_COMPILE_MS_FIELD_NUMBER: _ClassVar[int]
    rtfx: float
    finalize_ms_10s: int
    bias_compile_ms: int
    def __init__(self, rtfx: _Optional[float] = ..., finalize_ms_10s: _Optional[int] = ..., bias_compile_ms: _Optional[int] = ...) -> None: ...

class TranscribeRequest(_message.Message):
    __slots__ = ("start", "audio", "context_update", "end_of_audio", "cancel", "eager_end")
    START_FIELD_NUMBER: _ClassVar[int]
    AUDIO_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_UPDATE_FIELD_NUMBER: _ClassVar[int]
    END_OF_AUDIO_FIELD_NUMBER: _ClassVar[int]
    CANCEL_FIELD_NUMBER: _ClassVar[int]
    EAGER_END_FIELD_NUMBER: _ClassVar[int]
    start: SessionStart
    audio: AudioChunk
    context_update: ContextUpdate
    end_of_audio: EndOfAudio
    cancel: Cancel
    eager_end: EagerEnd
    def __init__(self, start: _Optional[_Union[SessionStart, _Mapping]] = ..., audio: _Optional[_Union[AudioChunk, _Mapping]] = ..., context_update: _Optional[_Union[ContextUpdate, _Mapping]] = ..., end_of_audio: _Optional[_Union[EndOfAudio, _Mapping]] = ..., cancel: _Optional[_Union[Cancel, _Mapping]] = ..., eager_end: _Optional[_Union[EagerEnd, _Mapping]] = ...) -> None: ...

class SessionStart(_message.Message):
    __slots__ = ("utterance_id", "attempt", "model_id", "languages", "audio_format", "context", "privacy", "options", "deadlines", "client", "required_features", "backend_params")
    class BackendParamsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    UTTERANCE_ID_FIELD_NUMBER: _ClassVar[int]
    ATTEMPT_FIELD_NUMBER: _ClassVar[int]
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    LANGUAGES_FIELD_NUMBER: _ClassVar[int]
    AUDIO_FORMAT_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    PRIVACY_FIELD_NUMBER: _ClassVar[int]
    OPTIONS_FIELD_NUMBER: _ClassVar[int]
    DEADLINES_FIELD_NUMBER: _ClassVar[int]
    CLIENT_FIELD_NUMBER: _ClassVar[int]
    REQUIRED_FEATURES_FIELD_NUMBER: _ClassVar[int]
    BACKEND_PARAMS_FIELD_NUMBER: _ClassVar[int]
    utterance_id: str
    attempt: int
    model_id: str
    languages: _containers.RepeatedScalarFieldContainer[str]
    audio_format: AudioFormat
    context: RecognitionContext
    privacy: PrivacyFlags
    options: ResultOptions
    deadlines: Deadlines
    client: ClientInfo
    required_features: _containers.RepeatedScalarFieldContainer[str]
    backend_params: _containers.ScalarMap[str, str]
    def __init__(self, utterance_id: _Optional[str] = ..., attempt: _Optional[int] = ..., model_id: _Optional[str] = ..., languages: _Optional[_Iterable[str]] = ..., audio_format: _Optional[_Union[AudioFormat, _Mapping]] = ..., context: _Optional[_Union[RecognitionContext, _Mapping]] = ..., privacy: _Optional[_Union[PrivacyFlags, _Mapping]] = ..., options: _Optional[_Union[ResultOptions, _Mapping]] = ..., deadlines: _Optional[_Union[Deadlines, _Mapping]] = ..., client: _Optional[_Union[ClientInfo, _Mapping]] = ..., required_features: _Optional[_Iterable[str]] = ..., backend_params: _Optional[_Mapping[str, str]] = ...) -> None: ...

class AudioFormat(_message.Message):
    __slots__ = ("encoding", "sample_rate_hz", "channels", "preroll_ms")
    ENCODING_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_RATE_HZ_FIELD_NUMBER: _ClassVar[int]
    CHANNELS_FIELD_NUMBER: _ClassVar[int]
    PREROLL_MS_FIELD_NUMBER: _ClassVar[int]
    encoding: AudioEncoding
    sample_rate_hz: int
    channels: int
    preroll_ms: int
    def __init__(self, encoding: _Optional[_Union[AudioEncoding, str]] = ..., sample_rate_hz: _Optional[int] = ..., channels: _Optional[int] = ..., preroll_ms: _Optional[int] = ...) -> None: ...

class RecognitionContext(_message.Message):
    __slots__ = ("revision", "bias", "free_text")
    REVISION_FIELD_NUMBER: _ClassVar[int]
    BIAS_FIELD_NUMBER: _ClassVar[int]
    FREE_TEXT_FIELD_NUMBER: _ClassVar[int]
    revision: int
    bias: BiasList
    free_text: FreeTextContext
    def __init__(self, revision: _Optional[int] = ..., bias: _Optional[_Union[BiasList, _Mapping]] = ..., free_text: _Optional[_Union[FreeTextContext, _Mapping]] = ...) -> None: ...

class BiasList(_message.Message):
    __slots__ = ("terms", "content_hash", "strength")
    TERMS_FIELD_NUMBER: _ClassVar[int]
    CONTENT_HASH_FIELD_NUMBER: _ClassVar[int]
    STRENGTH_FIELD_NUMBER: _ClassVar[int]
    terms: _containers.RepeatedCompositeFieldContainer[BiasTerm]
    content_hash: str
    strength: float
    def __init__(self, terms: _Optional[_Iterable[_Union[BiasTerm, _Mapping]]] = ..., content_hash: _Optional[str] = ..., strength: _Optional[float] = ...) -> None: ...

class BiasTerm(_message.Message):
    __slots__ = ("id", "canonical", "aliases", "weight", "kind", "provenance", "language")
    ID_FIELD_NUMBER: _ClassVar[int]
    CANONICAL_FIELD_NUMBER: _ClassVar[int]
    ALIASES_FIELD_NUMBER: _ClassVar[int]
    WEIGHT_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    PROVENANCE_FIELD_NUMBER: _ClassVar[int]
    LANGUAGE_FIELD_NUMBER: _ClassVar[int]
    id: str
    canonical: str
    aliases: _containers.RepeatedCompositeFieldContainer[SpokenAlias]
    weight: float
    kind: TermKind
    provenance: _containers.RepeatedScalarFieldContainer[ContextSource]
    language: str
    def __init__(self, id: _Optional[str] = ..., canonical: _Optional[str] = ..., aliases: _Optional[_Iterable[_Union[SpokenAlias, _Mapping]]] = ..., weight: _Optional[float] = ..., kind: _Optional[_Union[TermKind, str]] = ..., provenance: _Optional[_Iterable[_Union[ContextSource, str]]] = ..., language: _Optional[str] = ...) -> None: ...

class SpokenAlias(_message.Message):
    __slots__ = ("text", "kind")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    text: str
    kind: AliasKind
    def __init__(self, text: _Optional[str] = ..., kind: _Optional[_Union[AliasKind, str]] = ...) -> None: ...

class FreeTextContext(_message.Message):
    __slots__ = ("description", "preceding_text")
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    PRECEDING_TEXT_FIELD_NUMBER: _ClassVar[int]
    description: str
    preceding_text: str
    def __init__(self, description: _Optional[str] = ..., preceding_text: _Optional[str] = ...) -> None: ...

class PrivacyFlags(_message.Message):
    __slots__ = ("allow_audio_retention", "allow_transcript_retention", "allow_context_logging", "allow_eval_capture", "allow_replay_cache", "minimal_metadata")
    ALLOW_AUDIO_RETENTION_FIELD_NUMBER: _ClassVar[int]
    ALLOW_TRANSCRIPT_RETENTION_FIELD_NUMBER: _ClassVar[int]
    ALLOW_CONTEXT_LOGGING_FIELD_NUMBER: _ClassVar[int]
    ALLOW_EVAL_CAPTURE_FIELD_NUMBER: _ClassVar[int]
    ALLOW_REPLAY_CACHE_FIELD_NUMBER: _ClassVar[int]
    MINIMAL_METADATA_FIELD_NUMBER: _ClassVar[int]
    allow_audio_retention: bool
    allow_transcript_retention: bool
    allow_context_logging: bool
    allow_eval_capture: bool
    allow_replay_cache: bool
    minimal_metadata: bool
    def __init__(self, allow_audio_retention: _Optional[bool] = ..., allow_transcript_retention: _Optional[bool] = ..., allow_context_logging: _Optional[bool] = ..., allow_eval_capture: _Optional[bool] = ..., allow_replay_cache: _Optional[bool] = ..., minimal_metadata: _Optional[bool] = ...) -> None: ...

class ResultOptions(_message.Message):
    __slots__ = ("partials", "partial_interval_ms", "n_best", "word_timestamps", "word_confidence", "anchor", "bias_diagnostics", "n_best_words")
    PARTIALS_FIELD_NUMBER: _ClassVar[int]
    PARTIAL_INTERVAL_MS_FIELD_NUMBER: _ClassVar[int]
    N_BEST_FIELD_NUMBER: _ClassVar[int]
    WORD_TIMESTAMPS_FIELD_NUMBER: _ClassVar[int]
    WORD_CONFIDENCE_FIELD_NUMBER: _ClassVar[int]
    ANCHOR_FIELD_NUMBER: _ClassVar[int]
    BIAS_DIAGNOSTICS_FIELD_NUMBER: _ClassVar[int]
    N_BEST_WORDS_FIELD_NUMBER: _ClassVar[int]
    partials: bool
    partial_interval_ms: int
    n_best: int
    word_timestamps: bool
    word_confidence: bool
    anchor: AnchorMode
    bias_diagnostics: bool
    n_best_words: bool
    def __init__(self, partials: _Optional[bool] = ..., partial_interval_ms: _Optional[int] = ..., n_best: _Optional[int] = ..., word_timestamps: _Optional[bool] = ..., word_confidence: _Optional[bool] = ..., anchor: _Optional[_Union[AnchorMode, str]] = ..., bias_diagnostics: _Optional[bool] = ..., n_best_words: _Optional[bool] = ...) -> None: ...

class Deadlines(_message.Message):
    __slots__ = ("finalize_budget_ms", "max_audio_ms")
    FINALIZE_BUDGET_MS_FIELD_NUMBER: _ClassVar[int]
    MAX_AUDIO_MS_FIELD_NUMBER: _ClassVar[int]
    finalize_budget_ms: int
    max_audio_ms: int
    def __init__(self, finalize_budget_ms: _Optional[int] = ..., max_audio_ms: _Optional[int] = ...) -> None: ...

class ClientInfo(_message.Message):
    __slots__ = ("client_version", "device_id")
    CLIENT_VERSION_FIELD_NUMBER: _ClassVar[int]
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    client_version: str
    device_id: str
    def __init__(self, client_version: _Optional[str] = ..., device_id: _Optional[str] = ...) -> None: ...

class AudioChunk(_message.Message):
    __slots__ = ("data", "seq", "start_sample", "num_samples", "vad_speech_prob")
    DATA_FIELD_NUMBER: _ClassVar[int]
    SEQ_FIELD_NUMBER: _ClassVar[int]
    START_SAMPLE_FIELD_NUMBER: _ClassVar[int]
    NUM_SAMPLES_FIELD_NUMBER: _ClassVar[int]
    VAD_SPEECH_PROB_FIELD_NUMBER: _ClassVar[int]
    data: bytes
    seq: int
    start_sample: int
    num_samples: int
    vad_speech_prob: float
    def __init__(self, data: _Optional[bytes] = ..., seq: _Optional[int] = ..., start_sample: _Optional[int] = ..., num_samples: _Optional[int] = ..., vad_speech_prob: _Optional[float] = ...) -> None: ...

class ContextUpdate(_message.Message):
    __slots__ = ("context",)
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    context: RecognitionContext
    def __init__(self, context: _Optional[_Union[RecognitionContext, _Mapping]] = ...) -> None: ...

class EndOfAudio(_message.Message):
    __slots__ = ("last_seq", "total_samples", "reason")
    LAST_SEQ_FIELD_NUMBER: _ClassVar[int]
    TOTAL_SAMPLES_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    last_seq: int
    total_samples: int
    reason: EndReason
    def __init__(self, last_seq: _Optional[int] = ..., total_samples: _Optional[int] = ..., reason: _Optional[_Union[EndReason, str]] = ...) -> None: ...

class EagerEnd(_message.Message):
    __slots__ = ("total_samples", "speech_end_sample")
    TOTAL_SAMPLES_FIELD_NUMBER: _ClassVar[int]
    SPEECH_END_SAMPLE_FIELD_NUMBER: _ClassVar[int]
    total_samples: int
    speech_end_sample: int
    def __init__(self, total_samples: _Optional[int] = ..., speech_end_sample: _Optional[int] = ...) -> None: ...

class Cancel(_message.Message):
    __slots__ = ("reason",)
    REASON_FIELD_NUMBER: _ClassVar[int]
    reason: CancelReason
    def __init__(self, reason: _Optional[_Union[CancelReason, str]] = ...) -> None: ...

class TranscribeResponse(_message.Message):
    __slots__ = ("accepted", "partial", "final_result", "status", "warning", "session_ended")
    ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    PARTIAL_FIELD_NUMBER: _ClassVar[int]
    FINAL_RESULT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    WARNING_FIELD_NUMBER: _ClassVar[int]
    SESSION_ENDED_FIELD_NUMBER: _ClassVar[int]
    accepted: SessionAccepted
    partial: PartialResult
    final_result: FinalResult
    status: StreamStatus
    warning: ServerWarning
    session_ended: SessionEnded
    def __init__(self, accepted: _Optional[_Union[SessionAccepted, _Mapping]] = ..., partial: _Optional[_Union[PartialResult, _Mapping]] = ..., final_result: _Optional[_Union[FinalResult, _Mapping]] = ..., status: _Optional[_Union[StreamStatus, _Mapping]] = ..., warning: _Optional[_Union[ServerWarning, _Mapping]] = ..., session_ended: _Optional[_Union[SessionEnded, _Mapping]] = ...) -> None: ...

class SessionEnded(_message.Message):
    __slots__ = ("reason", "error", "utterance_id")
    REASON_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    UTTERANCE_ID_FIELD_NUMBER: _ClassVar[int]
    reason: SessionEndReason
    error: ErrorDetail
    utterance_id: str
    def __init__(self, reason: _Optional[_Union[SessionEndReason, str]] = ..., error: _Optional[_Union[ErrorDetail, _Mapping]] = ..., utterance_id: _Optional[str] = ...) -> None: ...

class SessionAccepted(_message.Message):
    __slots__ = ("session_id", "model_id", "applied", "server_time_unix_ms", "capabilities_etag", "replayed")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    APPLIED_FIELD_NUMBER: _ClassVar[int]
    SERVER_TIME_UNIX_MS_FIELD_NUMBER: _ClassVar[int]
    CAPABILITIES_ETAG_FIELD_NUMBER: _ClassVar[int]
    REPLAYED_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    model_id: str
    applied: AppliedConfig
    server_time_unix_ms: int
    capabilities_etag: str
    replayed: bool
    def __init__(self, session_id: _Optional[str] = ..., model_id: _Optional[str] = ..., applied: _Optional[_Union[AppliedConfig, _Mapping]] = ..., server_time_unix_ms: _Optional[int] = ..., capabilities_etag: _Optional[str] = ..., replayed: _Optional[bool] = ...) -> None: ...

class AppliedConfig(_message.Message):
    __slots__ = ("bias_mechanism", "bias_terms_used", "bias_terms_dropped", "free_text_used", "n_best", "anchor_planned", "word_timestamps", "word_confidence", "backend_params", "term_boosts")
    class BackendParamsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    class TermBoostsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    BIAS_MECHANISM_FIELD_NUMBER: _ClassVar[int]
    BIAS_TERMS_USED_FIELD_NUMBER: _ClassVar[int]
    BIAS_TERMS_DROPPED_FIELD_NUMBER: _ClassVar[int]
    FREE_TEXT_USED_FIELD_NUMBER: _ClassVar[int]
    N_BEST_FIELD_NUMBER: _ClassVar[int]
    ANCHOR_PLANNED_FIELD_NUMBER: _ClassVar[int]
    WORD_TIMESTAMPS_FIELD_NUMBER: _ClassVar[int]
    WORD_CONFIDENCE_FIELD_NUMBER: _ClassVar[int]
    BACKEND_PARAMS_FIELD_NUMBER: _ClassVar[int]
    TERM_BOOSTS_FIELD_NUMBER: _ClassVar[int]
    bias_mechanism: BiasMechanism
    bias_terms_used: int
    bias_terms_dropped: _containers.RepeatedScalarFieldContainer[str]
    free_text_used: bool
    n_best: int
    anchor_planned: bool
    word_timestamps: bool
    word_confidence: bool
    backend_params: _containers.ScalarMap[str, str]
    term_boosts: _containers.ScalarMap[str, float]
    def __init__(self, bias_mechanism: _Optional[_Union[BiasMechanism, str]] = ..., bias_terms_used: _Optional[int] = ..., bias_terms_dropped: _Optional[_Iterable[str]] = ..., free_text_used: _Optional[bool] = ..., n_best: _Optional[int] = ..., anchor_planned: _Optional[bool] = ..., word_timestamps: _Optional[bool] = ..., word_confidence: _Optional[bool] = ..., backend_params: _Optional[_Mapping[str, str]] = ..., term_boosts: _Optional[_Mapping[str, float]] = ...) -> None: ...

class PartialResult(_message.Message):
    __slots__ = ("text", "stable_bytes", "audio_processed_ms", "context_revision", "seq")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    STABLE_BYTES_FIELD_NUMBER: _ClassVar[int]
    AUDIO_PROCESSED_MS_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_REVISION_FIELD_NUMBER: _ClassVar[int]
    SEQ_FIELD_NUMBER: _ClassVar[int]
    text: str
    stable_bytes: int
    audio_processed_ms: int
    context_revision: int
    seq: int
    def __init__(self, text: _Optional[str] = ..., stable_bytes: _Optional[int] = ..., audio_processed_ms: _Optional[int] = ..., context_revision: _Optional[int] = ..., seq: _Optional[int] = ...) -> None: ...

class StreamStatus(_message.Message):
    __slots__ = ("audio_received_ms", "audio_processed_ms", "queued_sessions", "samples_received")
    AUDIO_RECEIVED_MS_FIELD_NUMBER: _ClassVar[int]
    AUDIO_PROCESSED_MS_FIELD_NUMBER: _ClassVar[int]
    QUEUED_SESSIONS_FIELD_NUMBER: _ClassVar[int]
    SAMPLES_RECEIVED_FIELD_NUMBER: _ClassVar[int]
    audio_received_ms: int
    audio_processed_ms: int
    queued_sessions: int
    samples_received: int
    def __init__(self, audio_received_ms: _Optional[int] = ..., audio_processed_ms: _Optional[int] = ..., queued_sessions: _Optional[int] = ..., samples_received: _Optional[int] = ...) -> None: ...

class ServerWarning(_message.Message):
    __slots__ = ("code", "message", "term_ids")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    TERM_IDS_FIELD_NUMBER: _ClassVar[int]
    code: WarningCode
    message: str
    term_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, code: _Optional[_Union[WarningCode, str]] = ..., message: _Optional[str] = ..., term_ids: _Optional[_Iterable[str]] = ...) -> None: ...

class FinalResult(_message.Message):
    __slots__ = ("utterance_id", "n_best", "anchor", "bias_hits", "detected_language", "language_confidence", "context_revision", "timings", "flags", "privacy", "replayed")
    UTTERANCE_ID_FIELD_NUMBER: _ClassVar[int]
    N_BEST_FIELD_NUMBER: _ClassVar[int]
    ANCHOR_FIELD_NUMBER: _ClassVar[int]
    BIAS_HITS_FIELD_NUMBER: _ClassVar[int]
    DETECTED_LANGUAGE_FIELD_NUMBER: _ClassVar[int]
    LANGUAGE_CONFIDENCE_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_REVISION_FIELD_NUMBER: _ClassVar[int]
    TIMINGS_FIELD_NUMBER: _ClassVar[int]
    FLAGS_FIELD_NUMBER: _ClassVar[int]
    PRIVACY_FIELD_NUMBER: _ClassVar[int]
    REPLAYED_FIELD_NUMBER: _ClassVar[int]
    utterance_id: str
    n_best: _containers.RepeatedCompositeFieldContainer[Hypothesis]
    anchor: Hypothesis
    bias_hits: _containers.RepeatedCompositeFieldContainer[BiasHit]
    detected_language: str
    language_confidence: float
    context_revision: int
    timings: Timings
    flags: ResultFlags
    privacy: PrivacyAck
    replayed: bool
    def __init__(self, utterance_id: _Optional[str] = ..., n_best: _Optional[_Iterable[_Union[Hypothesis, _Mapping]]] = ..., anchor: _Optional[_Union[Hypothesis, _Mapping]] = ..., bias_hits: _Optional[_Iterable[_Union[BiasHit, _Mapping]]] = ..., detected_language: _Optional[str] = ..., language_confidence: _Optional[float] = ..., context_revision: _Optional[int] = ..., timings: _Optional[_Union[Timings, _Mapping]] = ..., flags: _Optional[_Union[ResultFlags, _Mapping]] = ..., privacy: _Optional[_Union[PrivacyAck, _Mapping]] = ..., replayed: _Optional[bool] = ...) -> None: ...

class Hypothesis(_message.Message):
    __slots__ = ("text", "words", "score", "score_kind", "confidence", "origin")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    WORDS_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    SCORE_KIND_FIELD_NUMBER: _ClassVar[int]
    CONFIDENCE_FIELD_NUMBER: _ClassVar[int]
    ORIGIN_FIELD_NUMBER: _ClassVar[int]
    text: str
    words: _containers.RepeatedCompositeFieldContainer[Word]
    score: float
    score_kind: ScoreKind
    confidence: float
    origin: HypothesisOrigin
    def __init__(self, text: _Optional[str] = ..., words: _Optional[_Iterable[_Union[Word, _Mapping]]] = ..., score: _Optional[float] = ..., score_kind: _Optional[_Union[ScoreKind, str]] = ..., confidence: _Optional[float] = ..., origin: _Optional[_Union[HypothesisOrigin, str]] = ...) -> None: ...

class Word(_message.Message):
    __slots__ = ("text", "start_ms", "end_ms", "confidence", "text_byte_start", "text_byte_end", "bias_term_id")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    START_MS_FIELD_NUMBER: _ClassVar[int]
    END_MS_FIELD_NUMBER: _ClassVar[int]
    CONFIDENCE_FIELD_NUMBER: _ClassVar[int]
    TEXT_BYTE_START_FIELD_NUMBER: _ClassVar[int]
    TEXT_BYTE_END_FIELD_NUMBER: _ClassVar[int]
    BIAS_TERM_ID_FIELD_NUMBER: _ClassVar[int]
    text: str
    start_ms: int
    end_ms: int
    confidence: float
    text_byte_start: int
    text_byte_end: int
    bias_term_id: str
    def __init__(self, text: _Optional[str] = ..., start_ms: _Optional[int] = ..., end_ms: _Optional[int] = ..., confidence: _Optional[float] = ..., text_byte_start: _Optional[int] = ..., text_byte_end: _Optional[int] = ..., bias_term_id: _Optional[str] = ...) -> None: ...

class BiasHit(_message.Message):
    __slots__ = ("term_id", "matched_form", "via_alias", "decision", "mechanism", "text_byte_start", "text_byte_end", "start_ms", "end_ms", "anchor_text", "term_score", "anchor_score", "boost")
    TERM_ID_FIELD_NUMBER: _ClassVar[int]
    MATCHED_FORM_FIELD_NUMBER: _ClassVar[int]
    VIA_ALIAS_FIELD_NUMBER: _ClassVar[int]
    DECISION_FIELD_NUMBER: _ClassVar[int]
    MECHANISM_FIELD_NUMBER: _ClassVar[int]
    TEXT_BYTE_START_FIELD_NUMBER: _ClassVar[int]
    TEXT_BYTE_END_FIELD_NUMBER: _ClassVar[int]
    START_MS_FIELD_NUMBER: _ClassVar[int]
    END_MS_FIELD_NUMBER: _ClassVar[int]
    ANCHOR_TEXT_FIELD_NUMBER: _ClassVar[int]
    TERM_SCORE_FIELD_NUMBER: _ClassVar[int]
    ANCHOR_SCORE_FIELD_NUMBER: _ClassVar[int]
    BOOST_FIELD_NUMBER: _ClassVar[int]
    term_id: str
    matched_form: str
    via_alias: bool
    decision: BiasHitDecision
    mechanism: BiasMechanism
    text_byte_start: int
    text_byte_end: int
    start_ms: int
    end_ms: int
    anchor_text: str
    term_score: float
    anchor_score: float
    boost: float
    def __init__(self, term_id: _Optional[str] = ..., matched_form: _Optional[str] = ..., via_alias: _Optional[bool] = ..., decision: _Optional[_Union[BiasHitDecision, str]] = ..., mechanism: _Optional[_Union[BiasMechanism, str]] = ..., text_byte_start: _Optional[int] = ..., text_byte_end: _Optional[int] = ..., start_ms: _Optional[int] = ..., end_ms: _Optional[int] = ..., anchor_text: _Optional[str] = ..., term_score: _Optional[float] = ..., anchor_score: _Optional[float] = ..., boost: _Optional[float] = ...) -> None: ...

class Timings(_message.Message):
    __slots__ = ("audio_ms", "queue_ms", "bias_prepare_ms", "bias_cache_hit", "lag_at_end_ms", "end_to_final_ms", "encoder_ms", "decode_ms", "anchor_ms", "n_best_ms", "alignment_ms", "postprocess_ms", "server_end_received_unix_us", "server_final_sent_unix_us", "backend_ms", "eager_hit")
    class BackendMsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...
    AUDIO_MS_FIELD_NUMBER: _ClassVar[int]
    QUEUE_MS_FIELD_NUMBER: _ClassVar[int]
    BIAS_PREPARE_MS_FIELD_NUMBER: _ClassVar[int]
    BIAS_CACHE_HIT_FIELD_NUMBER: _ClassVar[int]
    LAG_AT_END_MS_FIELD_NUMBER: _ClassVar[int]
    END_TO_FINAL_MS_FIELD_NUMBER: _ClassVar[int]
    ENCODER_MS_FIELD_NUMBER: _ClassVar[int]
    DECODE_MS_FIELD_NUMBER: _ClassVar[int]
    ANCHOR_MS_FIELD_NUMBER: _ClassVar[int]
    N_BEST_MS_FIELD_NUMBER: _ClassVar[int]
    ALIGNMENT_MS_FIELD_NUMBER: _ClassVar[int]
    POSTPROCESS_MS_FIELD_NUMBER: _ClassVar[int]
    SERVER_END_RECEIVED_UNIX_US_FIELD_NUMBER: _ClassVar[int]
    SERVER_FINAL_SENT_UNIX_US_FIELD_NUMBER: _ClassVar[int]
    BACKEND_MS_FIELD_NUMBER: _ClassVar[int]
    EAGER_HIT_FIELD_NUMBER: _ClassVar[int]
    audio_ms: int
    queue_ms: int
    bias_prepare_ms: int
    bias_cache_hit: bool
    lag_at_end_ms: int
    end_to_final_ms: int
    encoder_ms: int
    decode_ms: int
    anchor_ms: int
    n_best_ms: int
    alignment_ms: int
    postprocess_ms: int
    server_end_received_unix_us: int
    server_final_sent_unix_us: int
    backend_ms: _containers.ScalarMap[str, int]
    eager_hit: bool
    def __init__(self, audio_ms: _Optional[int] = ..., queue_ms: _Optional[int] = ..., bias_prepare_ms: _Optional[int] = ..., bias_cache_hit: _Optional[bool] = ..., lag_at_end_ms: _Optional[int] = ..., end_to_final_ms: _Optional[int] = ..., encoder_ms: _Optional[int] = ..., decode_ms: _Optional[int] = ..., anchor_ms: _Optional[int] = ..., n_best_ms: _Optional[int] = ..., alignment_ms: _Optional[int] = ..., postprocess_ms: _Optional[int] = ..., server_end_received_unix_us: _Optional[int] = ..., server_final_sent_unix_us: _Optional[int] = ..., backend_ms: _Optional[_Mapping[str, int]] = ..., eager_hit: _Optional[bool] = ...) -> None: ...

class ResultFlags(_message.Message):
    __slots__ = ("finalize_budget_exceeded", "context_truncated", "context_echo_suspected", "no_speech", "audio_gap", "n_best", "anchor", "word_timestamps", "word_confidence", "bias_hits")
    FINALIZE_BUDGET_EXCEEDED_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_TRUNCATED_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_ECHO_SUSPECTED_FIELD_NUMBER: _ClassVar[int]
    NO_SPEECH_FIELD_NUMBER: _ClassVar[int]
    AUDIO_GAP_FIELD_NUMBER: _ClassVar[int]
    N_BEST_FIELD_NUMBER: _ClassVar[int]
    ANCHOR_FIELD_NUMBER: _ClassVar[int]
    WORD_TIMESTAMPS_FIELD_NUMBER: _ClassVar[int]
    WORD_CONFIDENCE_FIELD_NUMBER: _ClassVar[int]
    BIAS_HITS_FIELD_NUMBER: _ClassVar[int]
    finalize_budget_exceeded: bool
    context_truncated: bool
    context_echo_suspected: bool
    no_speech: bool
    audio_gap: bool
    n_best: FieldStatus
    anchor: FieldStatus
    word_timestamps: FieldStatus
    word_confidence: FieldStatus
    bias_hits: FieldStatus
    def __init__(self, finalize_budget_exceeded: _Optional[bool] = ..., context_truncated: _Optional[bool] = ..., context_echo_suspected: _Optional[bool] = ..., no_speech: _Optional[bool] = ..., audio_gap: _Optional[bool] = ..., n_best: _Optional[_Union[FieldStatus, str]] = ..., anchor: _Optional[_Union[FieldStatus, str]] = ..., word_timestamps: _Optional[_Union[FieldStatus, str]] = ..., word_confidence: _Optional[_Union[FieldStatus, str]] = ..., bias_hits: _Optional[_Union[FieldStatus, str]] = ...) -> None: ...

class PrivacyAck(_message.Message):
    __slots__ = ("audio_retained", "transcript_retained", "context_logged", "eval_captured", "replay_cached")
    AUDIO_RETAINED_FIELD_NUMBER: _ClassVar[int]
    TRANSCRIPT_RETAINED_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_LOGGED_FIELD_NUMBER: _ClassVar[int]
    EVAL_CAPTURED_FIELD_NUMBER: _ClassVar[int]
    REPLAY_CACHED_FIELD_NUMBER: _ClassVar[int]
    audio_retained: bool
    transcript_retained: bool
    context_logged: bool
    eval_captured: bool
    replay_cached: bool
    def __init__(self, audio_retained: _Optional[bool] = ..., transcript_retained: _Optional[bool] = ..., context_logged: _Optional[bool] = ..., eval_captured: _Optional[bool] = ..., replay_cached: _Optional[bool] = ...) -> None: ...

class ErrorDetail(_message.Message):
    __slots__ = ("code", "message", "retryable", "retry_after_ms", "utterance_id", "unavailable_features")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    RETRYABLE_FIELD_NUMBER: _ClassVar[int]
    RETRY_AFTER_MS_FIELD_NUMBER: _ClassVar[int]
    UTTERANCE_ID_FIELD_NUMBER: _ClassVar[int]
    UNAVAILABLE_FEATURES_FIELD_NUMBER: _ClassVar[int]
    code: ErrorCode
    message: str
    retryable: bool
    retry_after_ms: int
    utterance_id: str
    unavailable_features: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, code: _Optional[_Union[ErrorCode, str]] = ..., message: _Optional[str] = ..., retryable: _Optional[bool] = ..., retry_after_ms: _Optional[int] = ..., utterance_id: _Optional[str] = ..., unavailable_features: _Optional[_Iterable[str]] = ...) -> None: ...

class RecognizeRequest(_message.Message):
    __slots__ = ("config", "audio")
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    AUDIO_FIELD_NUMBER: _ClassVar[int]
    config: SessionStart
    audio: _containers.RepeatedCompositeFieldContainer[AudioChunk]
    def __init__(self, config: _Optional[_Union[SessionStart, _Mapping]] = ..., audio: _Optional[_Iterable[_Union[AudioChunk, _Mapping]]] = ...) -> None: ...

class RecognizeResponse(_message.Message):
    __slots__ = ("accepted", "final_result", "warnings")
    ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    FINAL_RESULT_FIELD_NUMBER: _ClassVar[int]
    WARNINGS_FIELD_NUMBER: _ClassVar[int]
    accepted: SessionAccepted
    final_result: FinalResult
    warnings: _containers.RepeatedCompositeFieldContainer[ServerWarning]
    def __init__(self, accepted: _Optional[_Union[SessionAccepted, _Mapping]] = ..., final_result: _Optional[_Union[FinalResult, _Mapping]] = ..., warnings: _Optional[_Iterable[_Union[ServerWarning, _Mapping]]] = ...) -> None: ...

class WarmupRequest(_message.Message):
    __slots__ = ("model_id", "include_bias")
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_BIAS_FIELD_NUMBER: _ClassVar[int]
    model_id: str
    include_bias: bool
    def __init__(self, model_id: _Optional[str] = ..., include_bias: _Optional[bool] = ...) -> None: ...

class WarmupResponse(_message.Message):
    __slots__ = ("state", "warm_ms", "server_time_unix_ms")
    STATE_FIELD_NUMBER: _ClassVar[int]
    WARM_MS_FIELD_NUMBER: _ClassVar[int]
    SERVER_TIME_UNIX_MS_FIELD_NUMBER: _ClassVar[int]
    state: ModelState
    warm_ms: int
    server_time_unix_ms: int
    def __init__(self, state: _Optional[_Union[ModelState, str]] = ..., warm_ms: _Optional[int] = ..., server_time_unix_ms: _Optional[int] = ...) -> None: ...

class CleanupProfile(_message.Message):
    __slots__ = ("id", "description", "allowed_kinds", "max_input_bytes", "typical_ms", "model")
    ID_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    ALLOWED_KINDS_FIELD_NUMBER: _ClassVar[int]
    MAX_INPUT_BYTES_FIELD_NUMBER: _ClassVar[int]
    TYPICAL_MS_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    id: str
    description: str
    allowed_kinds: _containers.RepeatedScalarFieldContainer[EditKind]
    max_input_bytes: int
    typical_ms: int
    model: str
    def __init__(self, id: _Optional[str] = ..., description: _Optional[str] = ..., allowed_kinds: _Optional[_Iterable[_Union[EditKind, str]]] = ..., max_input_bytes: _Optional[int] = ..., typical_ms: _Optional[int] = ..., model: _Optional[str] = ...) -> None: ...

class CleanupRequest(_message.Message):
    __slots__ = ("request_id", "utterance_id", "text", "protected_spans", "profile_id", "language", "context", "max_edits", "budget_ms", "privacy")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    UTTERANCE_ID_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    PROTECTED_SPANS_FIELD_NUMBER: _ClassVar[int]
    PROFILE_ID_FIELD_NUMBER: _ClassVar[int]
    LANGUAGE_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    MAX_EDITS_FIELD_NUMBER: _ClassVar[int]
    BUDGET_MS_FIELD_NUMBER: _ClassVar[int]
    PRIVACY_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    utterance_id: str
    text: str
    protected_spans: _containers.RepeatedCompositeFieldContainer[ProtectedSpan]
    profile_id: str
    language: str
    context: CleanupContext
    max_edits: int
    budget_ms: int
    privacy: PrivacyFlags
    def __init__(self, request_id: _Optional[str] = ..., utterance_id: _Optional[str] = ..., text: _Optional[str] = ..., protected_spans: _Optional[_Iterable[_Union[ProtectedSpan, _Mapping]]] = ..., profile_id: _Optional[str] = ..., language: _Optional[str] = ..., context: _Optional[_Union[CleanupContext, _Mapping]] = ..., max_edits: _Optional[int] = ..., budget_ms: _Optional[int] = ..., privacy: _Optional[_Union[PrivacyFlags, _Mapping]] = ...) -> None: ...

class ProtectedSpan(_message.Message):
    __slots__ = ("byte_start", "byte_end", "kind")
    BYTE_START_FIELD_NUMBER: _ClassVar[int]
    BYTE_END_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    byte_start: int
    byte_end: int
    kind: ProtectedSpanKind
    def __init__(self, byte_start: _Optional[int] = ..., byte_end: _Optional[int] = ..., kind: _Optional[_Union[ProtectedSpanKind, str]] = ...) -> None: ...

class CleanupContext(_message.Message):
    __slots__ = ("app_category", "preceding_text")
    APP_CATEGORY_FIELD_NUMBER: _ClassVar[int]
    PRECEDING_TEXT_FIELD_NUMBER: _ClassVar[int]
    app_category: str
    preceding_text: str
    def __init__(self, app_category: _Optional[str] = ..., preceding_text: _Optional[str] = ...) -> None: ...

class CleanupResponse(_message.Message):
    __slots__ = ("edits", "model", "generation_ms", "total_ms", "truncated", "rejected_by_server", "rejections")
    EDITS_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    GENERATION_MS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_MS_FIELD_NUMBER: _ClassVar[int]
    TRUNCATED_FIELD_NUMBER: _ClassVar[int]
    REJECTED_BY_SERVER_FIELD_NUMBER: _ClassVar[int]
    REJECTIONS_FIELD_NUMBER: _ClassVar[int]
    edits: _containers.RepeatedCompositeFieldContainer[Edit]
    model: str
    generation_ms: int
    total_ms: int
    truncated: bool
    rejected_by_server: int
    rejections: _containers.RepeatedCompositeFieldContainer[EditRejection]
    def __init__(self, edits: _Optional[_Iterable[_Union[Edit, _Mapping]]] = ..., model: _Optional[str] = ..., generation_ms: _Optional[int] = ..., total_ms: _Optional[int] = ..., truncated: _Optional[bool] = ..., rejected_by_server: _Optional[int] = ..., rejections: _Optional[_Iterable[_Union[EditRejection, _Mapping]]] = ...) -> None: ...

class Edit(_message.Message):
    __slots__ = ("byte_start", "byte_end", "find", "replace", "kind", "reason")
    BYTE_START_FIELD_NUMBER: _ClassVar[int]
    BYTE_END_FIELD_NUMBER: _ClassVar[int]
    FIND_FIELD_NUMBER: _ClassVar[int]
    REPLACE_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    byte_start: int
    byte_end: int
    find: str
    replace: str
    kind: EditKind
    reason: str
    def __init__(self, byte_start: _Optional[int] = ..., byte_end: _Optional[int] = ..., find: _Optional[str] = ..., replace: _Optional[str] = ..., kind: _Optional[_Union[EditKind, str]] = ..., reason: _Optional[str] = ...) -> None: ...

class EditRejection(_message.Message):
    __slots__ = ("find", "replace", "reason")
    FIND_FIELD_NUMBER: _ClassVar[int]
    REPLACE_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    find: str
    replace: str
    reason: EditRejectionReason
    def __init__(self, find: _Optional[str] = ..., replace: _Optional[str] = ..., reason: _Optional[_Union[EditRejectionReason, str]] = ...) -> None: ...
