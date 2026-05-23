# PolicyPulse MVP

Hackathon-safe autonomous policy monitor for public UnitedHealthcare policy pages using `Nimble`, `ClickHouse`, `CDS Hooks`, `CRD`, `DTR`, `Senso`, and a demo `402` payment scaffold.

## Run

### API

```bash
uvicorn app.api.main:app --reload
```

### UI

```bash
streamlit run app/ui/streamlit_app.py
```

The UI expects the API at `http://localhost:8000` by default. Override with `POLICYPULSE_API_BASE`.

## Demo story

For the hackathon demo, PolicyPulse does not rely on proprietary payer APIs such as Myndshft, AccountableHQ, Availity AuthAI, or CoverMyMeds.

Instead, the app shows the same autonomous loop with public UHC policy sources:

1. `Nimble` fetches the latest public UHC policy snapshot
2. PolicyPulse compares it with a controlled prior snapshot
3. `ClickHouse` stores snapshots, policy changes, and doctor-trigger events
4. A CDS-like doctor action triggers `CRD` and `DTR` relevance checks
5. Only relevant material changes continue to payment, `Senso`, and `cited.md` publishing

## Demo flow

1. Run scheduled policy check
2. Simulate relevant or irrelevant doctor action
3. Review CRD / DTR decision
4. Generate paid readiness card
5. Verify demo payment and publish the per-record output through Senso to `cited.md`

Use the irrelevant doctor action path to demonstrate that the agent stays quiet when the workflow does not align with the detected policy change.
