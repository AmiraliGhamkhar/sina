"""Version + protocol constants (single source of truth for both API and
client manifest responses)."""

API_VERSION = "0.1.0"
#: WebSocket protocol version — bump on breaking message-schema changes only.
#: See docs/WEBSOCKET_PROTOCOL.md (additive fields are allowed within v1).
WS_PROTOCOL_VERSION = 1
#: Minimum client protocol version the server still accepts.
WS_PROTOCOL_MIN = 1

APP_NAME = "MedicalScribe API"
#: Phase this build implements (for honest capability reporting to clients).
PHASE = 7
