"""Small helpers shared by the test modules."""
import json
import uuid

import httpx

GOOD_PARAGRAPHS = [
    "Thank you for your enquiry about our Data Science certification. It is a strong fit for "
    "engineering managers at growing companies like Acme Learning who want their teams to work with data.",
    "The programme combines live mentoring with practical projects, so your team can apply what they "
    "learn straight away.",
    "Would you have 15 minutes for a short call this week? Just reply with a time that suits you.",
]


async def create_lead(http: httpx.AsyncClient, **fields) -> dict:
    payload = {"name": "Test Lead", "email": f"lead-{uuid.uuid4().hex[:8]}@example.com", **fields}
    response = await http.post("/leads", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def create_hot_lead(http: httpx.AsyncClient, bucket: str = "Hot", **fields) -> dict:
    """A lead as Agent A would leave it."""
    fields.setdefault("company", "Acme Learning")
    fields.setdefault("product_interest", "Data Science certification")
    lead = await create_lead(http, **fields)
    response = await http.patch(f"/leads/{lead['id']}/score", json={
        "score": 0.9 if bucket == "Hot" else 0.3, "bucket": bucket, "model_version": "test",
        "top_factors": ["Occupation: Working Professional (raises score)"],
    })
    assert response.status_code == 200, response.text
    return response.json()


async def interactions(http: httpx.AsyncClient, lead_id: str) -> list[dict]:
    return (await http.get(f"/leads/{lead_id}/interactions")).json()


async def audits(http: httpx.AsyncClient, lead_id: str) -> list[dict]:
    return (await http.get("/audit-log", params={"lead_id": lead_id})).json()


def ollama_transport(*drafts, status: int = 200, seen: list | None = None) -> httpx.MockTransport:
    """A stand-in for Ollama's /api/chat that returns `drafts` in turn
    (dicts become the model's JSON reply; strings are sent as-is)."""
    replies = list(drafts)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        if status != 200:
            return httpx.Response(status, text="model not found")
        reply = replies.pop(0) if len(replies) > 1 else replies[0]
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})

    return httpx.MockTransport(handler)
