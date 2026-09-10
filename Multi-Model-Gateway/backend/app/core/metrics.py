from prometheus_client import Counter, Histogram, Gauge
from app.core.config import settings


# Prometheus metrics

active_conversations = Gauge(
    "active_conversations_total",
    "Number of active conversations"
)

prompt_tokens_counter = Counter(
    "prompt_tokens_total",
    "Total prompt tokens sent",
    ["provider", "model"]
)

completion_tokens_counter = Counter(
    "completion_tokens_total",
    "Total completion tokens generated",
    ["provider", "model"]
)


chat_requests_total = Counter(
    "chat_requests_total",
    "Total chat requests",
    ["provider", "model"]
)

chat_latency_seconds = Histogram(
    "chat_latency_seconds", 
    "Chat request latency in seconds",
    ["provider"]
)

tokens_per_second = Gauge(
    "tokens_per_second",
    "Token generation speed",
    ["model"]
)

search_degraded_total = Counter(
    "search_degraded_total",
    "Search calls that returned zero results",
    ["source"]
)


from langfuse import get_client

def record_metrics(provider: str, model: str, elapsed: float,
                   prompt_tok: int, completion_tok: int, messages: list,
                   full_response: str, conversation_id: str,
                   record_content: bool = True):
    """Prometheus counters + a Langfuse generation trace.

    `record_content=False` (set for `private: true` chats) records only
    metadata — latency, token counts, model — and omits the message text and
    the response, so a private conversation's content never leaves the box.
    """
    total_tokens = (prompt_tok or 0) + (completion_tok or 0)
    tps = completion_tok / elapsed if elapsed > 0 and completion_tok else 0

    # Prometheus
    chat_requests_total.labels(provider=provider, model=model).inc()
    chat_latency_seconds.labels(provider=provider).observe(elapsed)
    tokens_per_second.labels(model=model).set(tps)
    prompt_tokens_counter.labels(provider=provider, model=model).inc(prompt_tok or 0)
    completion_tokens_counter.labels(provider=provider, model=model).inc(completion_tok or 0)

    # Langfuse v4 - correct API
    langfuse = get_client()
    with langfuse.start_as_current_observation(
        as_type="generation",
        name="chat_completion",
        model=model,
    ) as observation:
        observation.update(
            # content omitted entirely for private chats
            input={"messages": messages} if record_content else None,
            output={"response": full_response} if record_content else None,
            metadata={
                "provider": provider,
                "conversation_id": conversation_id,
                "private": not record_content,
                "latency_seconds": elapsed,
                "tokens_per_second": tps,
                "prompt_tokens": prompt_tok,
                "completion_tokens": completion_tok,
                "total_tokens": total_tokens,
            }
        )