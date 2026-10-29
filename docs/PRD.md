# Product Requirements Document

> Kept identical in `bpo-backend/docs/PRD.md` and `bpo-frontend/docs/PRD.md`. Change both together.

## 1. Product Overview
- **Product name:** AssureVoice AI (sandbox edition)
- **One-line description:** An AI contact-centre platform for an Indian insurer that runs renewal calls, premium reminders, policy queries and follow-ups with a voice agent, with human escalation, QA, maker-checker approvals and a full audit log.
- **Product type:** Enterprise internal tool, shown as an executive showcase.
- **Primary platform:** Web (desktop, 1440px and 1920px).

## 2. Problem
- Renewal and reminder calls are high-volume and repetitive; missed renewals mean lapsed policies and lost premium.
- Regulated calling needs disclosure, verification, correct figures and no mis-selling on every call, which is hard to prove at scale.
- Today this is done by human outbound teams with spreadsheets and manual QA sampling.

## 3. Goal
- **Primary:** Show, end to end, that an AI voice agent can run compliant renewal and service calls, with humans in control.
- **Secondary:** Every action is governed (RBAC, maker-checker) and evidenced (audit log, QA score); the product looks and behaves like production software.

## 4. Target Users (seed personas, one per role)
| Persona | Role | Job title | Needs |
|---|---|---|---|
| Ravi Shankar | `super_admin` | IT Administrator | Manage users, roles, integrations; read audit |
| Anitha Raman | `ops_manager` | Head of Operations | Business KPIs, approve campaigns/scripts, oversee queues |
| Karthik Subramanian | `team_lead` | Floor Supervisor | Watch live calls, run escalations, coach agents |
| Divya Krishnan | `human_agent` | Senior Service Executive | Take over calls, resolve escalations, follow-ups |
| Suresh Babu | `campaign_manager` | Renewals Manager | Build and launch renewal campaigns |
| Meera Iyer | `bot_designer` | Conversation Designer | Write and test bot scripts, submit for approval |
| Prakash Natarajan | `qa_analyst` | Quality Analyst | Review AI QA scores, lock evaluations |
| Lakshmi Venkatesh | `compliance_officer` | Compliance Officer | Approve scripts/campaigns, read audit, export evidence |

Emails: `firstname.lastname@sandbox.assurevoice.in`; password from `SEED_PASSWORD`.

## 5. User Stories
- As Divya, I want to run a renewal call with the AI and take over when needed, so the customer gets a payment link, a callback, or a person.
- As Meera, I want to edit a script and submit it, so it reaches the bot only after approval.
- As Lakshmi, I want to see a side-by-side diff and a compliance checklist before approving.
- As Anitha, I want a dashboard of calls, renewals, collections and sentiment, so I can steer the operation.
- As Prakash, I want to review the AI's 5-point QA score against the transcript and lock it.
- As Ravi, I want to manage users and see the permission matrix.
- As any user, I want to see only what my role allows; masked roles never see full phone numbers or dates of birth.

## 6. Core Features
Access levels: **full / view / approve / masked / own / none**. Enforced in the backend on every endpoint, mirrored in the UI.

| Screen | Access | Phase |
|---|---|---|
| Login | public | 1 |
| Executive dashboard | super_admin, ops_manager full · team_lead, campaign_manager, qa_analyst, compliance_officer view | 3 |
| Agent console | team_lead, human_agent | 2 |
| Escalation queue | team_lead, human_agent full · ops_manager view | 3 |
| Live monitor | team_lead full · ops_manager view | 5 |
| Call history + detail | ops_manager, team_lead, qa_analyst full · human_agent own calls · super_admin, campaign_manager, bot_designer, compliance_officer view | 3 |
| Customers & policies (Customer 360) | team_lead, human_agent full · ops_manager, campaign_manager, qa_analyst, compliance_officer masked | 3 |
| Campaigns (3-step wizard) | campaign_manager full · ops_manager, compliance_officer approve | 4 |
| Follow-ups | team_lead, human_agent, campaign_manager full · ops_manager view | 3 |
| Bot studio | bot_designer full · compliance_officer view | 4 |
| Approvals | ops_manager, compliance_officer | 4 |
| QA review | qa_analyst full · ops_manager, team_lead, compliance_officer view | 4 |
| Audit log (+ audited CSV export) | super_admin, compliance_officer | 5 |
| Users & roles, Integrations | super_admin only | 5 |

### Feature: Authentication and role routing (Phase 1)
- **Flow:** email + password → httpOnly JWT cookie → redirect to the role's landing page.
- **Landing pages:** super_admin, ops_manager → dashboard · team_lead → live monitor · human_agent → agent console · campaign_manager → campaigns · bot_designer → bot studio · qa_analyst → QA review · compliance_officer → approvals.
- **Acceptance:** all 8 personas log in; each sees only its menu; unauthenticated users go to `/login`; a wrong-role API call returns 403; every login (and failed login) is audited.

### Feature: Voice agent (Phase 2)
- Pipecat + Gemini Live, real-time audio with interruption, browser connects over WebRTC via the Pipecat JS client.
- Tools: `verify_customer(policy_no, dob)`, `get_policy_details(policy_no)`, `send_payment_link(policy_no)` (sandbox link + WhatsApp-style follow-up record), `schedule_followup(policy_no, when, reason)`, `escalate_to_human(policy_no, reason)`.
- System prompt = latest **approved** script for the mode (inbound / renewal).
- **Must:** disclose it is automated and recorded; share nothing before `verify_customer` succeeds; state no figure a tool did not return.
- After each call: save transcript; Gemini Flash returns `{summary, outcome, sentiment, next_action}` and a 5-point QA score (AI disclosure, identity verified, correct figures, no mis-selling, polite close).
- **Acceptance:** as Divya, a full renewal call ends in a payment link, callback or escalation and appears in the database.

### Feature: Maker-checker (Phases 1 and 4)
- Scripts (bot_designer) and campaigns (campaign_manager) stay `pending` until ops_manager or compliance_officer approves.
- The bot always uses the latest approved script; launching a campaign runs 3 simulated calls and updates progress.

### Feature: Follow-ups (Phase 3)
- APScheduler, 2-minute timer in the sandbox; WhatsApp message text generated with Gemini Flash; nothing is sent to real numbers.

## 7. Non-Goals
- Real PSTN/SIP calling, real WhatsApp sends, real payments, real PAS/CRM/IRDAI integrations (shown as "Pilot integration").
- Mobile layouts below 1280px; dark mode; multi-tenant.
- Inventing telemetry or vendor claims that the sandbox does not have.

## 8. MVP Scope
- **Must have:** all screens above, synthetic data, RBAC + masking + audit, working voice call, maker-checker.
- **Nice to have:** visual polish beyond the Stitch designs, per-agent coaching.
- **Future:** PostgreSQL / Cloud SQL deployment, real telephony, SSO.

## 9. Success Metrics
- Every persona completes its walkthrough with no errors or unexpected 403s.
- A live renewal call reaches an outcome and shows up in call history with summary and QA score.
- Every screen visually matches its Stitch `screen.png`.

## 10. Constraints
- **Technical:** two separate repos (`bpo-backend`, `bpo-frontend`); OpenAPI is the only contract; API under `/api/v1`.
- **Data:** all synthetic (Faker `en_IN`, deterministic seed); `make reset` restores the sandbox.
- **Formatting:** INR with Indian grouping (₹1,25,000); dates "29 Oct 2026"; all times in IST.
- **Design:** `bpo-frontend/design/stitch/` is the visual source of truth.
- **Every page:** slim "Sandbox environment · synthetic data" banner; header with persona name, initials avatar, job title and role.

## 11. Assumptions
- One human_agent and one team_lead persona are enough to represent the floor; seeded calls are attributed to them.
- SQLite is adequate for the showcase; schema is portable to PostgreSQL.

## 12. Open Questions
- Should the header search (in the designs) search customers only, or customers + policies + calls? (Decide in Phase 3.)
- Should super_admin see the executive dashboard as landing, or Users & roles? (Currently dashboard.)

## 13. Definition of Done (per phase)
- [ ] Phase acceptance criteria met
- [ ] Backend: `make test` passes
- [ ] Frontend: `npm run lint`, `npm run typecheck`, `npm run build` pass; e2e passes
- [ ] App runs; critical flows verified in the browser
- [ ] Security rules respected (RBAC, masking, audit)
- [ ] Docs updated; anything simulated is listed
