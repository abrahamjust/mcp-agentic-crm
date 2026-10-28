"""Small helpers shared by the test modules."""
import uuid

import httpx

# A realistic form submission with every scoring input filled in.
FULL_FORM = {
    "product_interest": "Data Science certificate",
    "source": "Reference",
    "lead_origin": "Lead Add Form",
    "occupation": "Working Professional",
    "total_visits": 6,
    "time_on_website_sec": 1500,
    "pages_per_visit": 3.5,
    "consent": {"marketing": True},
}


async def create_lead(http: httpx.AsyncClient, **fields) -> dict:
    payload = {"name": "Test Lead", "email": f"lead-{uuid.uuid4().hex[:8]}@example.com", **fields}
    response = await http.post("/leads", json=payload)
    assert response.status_code == 201, response.text
    return response.json()
