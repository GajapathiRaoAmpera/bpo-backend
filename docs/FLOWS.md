# Flows

## 1. A call, step by step (voice bot)
1. **Open:** the bot says it is an automated AI assistant from Ampera BPO and that the call is recorded.
2. **Language:** the bot asks which language to continue in (English, Hindi, Tamil, Telugu, Kannada or Malayalam), then calls `set_language`. The choice is saved to the customer's record only after step 3 succeeds.
3. **Verify:** the bot asks for the policy number, reads it back, then asks for the date of birth, then calls `verify_customer`. After 3 failures it is locked and the bot offers a person. Nothing about a policy is shared before this step.
4. **Intent:**
   - **Renewal / policy questions:** `get_policy_details`, then the bot quotes the `*_spoken` figures. `send_payment_link` creates a Razorpay link and sends it on WhatsApp in the customer's language. Or `schedule_followup` books a callback (09:00–19:00 IST).
   - **New claim:** the bot asks what happened, when and where, then calls `register_claim` and reads the claim number back.
   - **Upset, dispute, or asks for a person:** `escalate_to_human`.
5. **Close:** one line on what happens next, then thanks.
6. **After the call:** post-call analysis (summary, outcome, QA score). System facts win over the model.

**Interruptions and silence:**
- Tools are never cancelled by the customer talking over the bot.
- On phone lines, noise and short "hmm"s don't cut the bot off.
- After 7 s of silence the bot re-prompts. It does this twice, then says goodbye, then ends the call.

## 2. After the call (system)
1. The follow-up timer (every few minutes) sends due follow-ups: reminders, callbacks and payment links. Each goes on WhatsApp in the approved template language closest to the customer's, falling back to English.
2. The customer pays on Razorpay's page. The `payment_link.paid` webhook marks the follow-up done and the policy renewed, and the dashboard's renewals count goes up.
3. If the customer replies **RENEW** on WhatsApp, they get a fresh link as free-form text. This is free inside Meta's 24 h window.

## 3. People (who does what)
| Role | Does |
|---|---|
| Campaign manager | Builds a campaign segment (product, renewal window, languages), then submits it |
| Ops manager / Compliance officer | Approves or rejects campaigns and script versions (maker-checker) |
| Bot designer | Writes new script versions in Bot studio; they go live only after approval |
| Human agent | Runs console calls, takes over live calls, works escalations and follow-ups |
| Team lead | Monitors live calls, works escalations, moves claims through review |
| Ops manager | Dashboard; moves claims through review |
| QA analyst | Scores calls on the 5 checks |
| Compliance officer | Reads and exports the audit log |
| Super admin | Users and integrations |
