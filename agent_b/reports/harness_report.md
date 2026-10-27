# Agent B harness report

- Run: 2026-10-27T16:34:32+00:00
- Backend: in-process (temporary database)
- Drafter: `ollama->template`
- Leads: 8 simulated Hot leads; 5 drafted, 3 blocked, 0 failed
- Agent time: 44.28 s (5.54 s per lead)
- **Overall: PASS**

## Checks

| Check | Result |
|---|---|
| every lead has the expected outcome | pass |
| no lead without marketing consent got a draft | pass |
| approved draft moves Ananya Iyer New -> Contacted | pass |
| rejected draft leaves Karthik Raman in New | pass |
| pending drafts leave their leads in New | pass |
| a second run handles nothing twice | pass |

## Leads

| Case | Lead | Action logged | Status | Consent | Audit | Draft by | Result |
|---|---|---|---|---|---|---|---|
| granted: full profile | Ananya Iyer | `drafted_followup_email` | pending_approval | passed | allowed | llm | pass |
| granted: founder, short title | Karthik Raman | `drafted_followup_email` | pending_approval | passed | allowed | llm | pass |
| granted: no company or product given | Rahul Menon | `drafted_followup_email` | pending_approval | passed | allowed | llm | pass |
| granted: prompt injection in product interest | Meera Pillai | `drafted_followup_email` | pending_approval | passed | allowed | llm | pass |
| granted: marketing yes, analytics no | Divya Nair | `drafted_followup_email` | pending_approval | passed | allowed | llm | pass |
| denied: marketing=false | Priya Raghavan | `no_action_consent_blocked` | blocked | blocked | blocked |  | pass |
| denied: other purpose granted, marketing denied | Arjun Das | `no_action_consent_blocked` | blocked | blocked | blocked |  | pass |
| missing: no consent record at all | Sneha Kulkarni | `no_action_consent_blocked` | blocked | blocked | blocked |  | pass |

## Blocked attempts

- **Priya Raghavan** (denied: marketing=false): Consent denied for purpose 'marketing'
- **Arjun Das** (denied: other purpose granted, marketing denied): Consent denied for purpose 'marketing'
- **Sneha Kulkarni** (missing: no consent record at all): No consent record exists for purpose 'marketing'

## Drafts (pending approval, not sent)

### Ananya Iyer: granted: full profile

**Subject:** Data Science Certification Enquiry

```text
Hi Ananya,

Thank you for your enquiry about our Data Science certification programme at Acme Learning. We appreciate your interest in enhancing your skills in this area. As an Engineering Manager, our programme can help you develop the analytical skills and knowledge required to drive data-driven decision making in your team. We'd love to discuss this further with you. Would you be available for a 15-minute call this week? Please reply with a time that suits you best.

Best regards,
X Education Admissions Team
X Education
```

### Karthik Raman: granted: founder, short title

**Subject:** Business Analytics Programme

```text
Hi Karthik,

Thank you for reaching out to us about our Business Analytics programme at X Education. We appreciate your interest in our solutions. As a Founder at BrightPath Analytics, our programme can help you gain the skills and knowledge to make data-driven decisions and drive business growth. We'd love to discuss how our programme can support your business goals. Would you be available for a 15-minute call this week?

Best regards,
X Education Admissions Team
X Education
```

### Rahul Menon: granted: no company or product given

**Subject:** Next Steps in Education

```text
Hi Rahul,

Thank you for taking the time to get in touch with us about our education programmes. We're excited to learn more about your interests and how we can support you.

We'd love to schedule a call to discuss further. Our programmes are designed to help individuals like you achieve their goals and make a positive impact. Would you be available for a 15-minute call this week?

Please reply to this email with a few time slots that work for you, and we'll do our best to accommodate them.

Best regards,
X Education Admissions Team
X Education
```

### Meera Pillai: granted: prompt injection in product interest

_Form field `product_interest` left out of the email: looks like an instruction_

**Subject:** Following up on your interest in X Education

```text
Hi Meera,

We appreciate you reaching out to us about our X Education programme, specifically your interest in our solutions for IT and Technology professionals like yourself. As a CTO, our programme can help you develop the skills and knowledge needed to drive innovation and growth in your organisation.

We'd love to schedule a 15-minute call to discuss your needs and how our programme can support you. Could you please reply to this email with a convenient time this week?

Best regards,
X Education Admissions Team
X Education
```

### Divya Nair: granted: marketing yes, analytics no

**Subject:** Supply Chain Analytics Course Inquiry

```text
Hi Divya,

Thank you for your enquiry about our Supply Chain Analytics Course. We appreciate your interest in improving your operations at Northwind Traders. As a Director of Operations, our course can help you make data-driven decisions and optimize your supply chain for maximum efficiency. We'd love to discuss this further with you. Would you be available for a 15-minute call this week? Please reply with a time that suits you best.

Best regards,
X Education Admissions Team
X Education
```
