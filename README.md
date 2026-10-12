# ShelfReady

**The trust layer for product data.** Built for the AMD Developer Hackathon: ACT III (Reinvent Commerce track + Evolus Business Agents on AMD).

EU online shops receive supplier spec sheets and photos that often contradict each other: wrong size, colour or material, missing safety information. ShelfReady:
- extracts the specs
- checks every attribute against the photos, with side-by-side evidence
- flags missing EU listing information (GPSR, accessibility alt text)
- writes multilingual listings only from verified data

A human approves everything at a publish gate.

> 🚧 Work in progress (hackathon week, Oct 12–17, 2026). Setup and run instructions will follow.

## Stack
- **Model:** Gemma 4 31B served with vLLM on an AMD Instinct MI300X (AMD Developer Cloud, ROCm)
- **Agents:** Evolus (email intake workflow, document extractor, AI Tools, knowledge library, chat widget), driven through its REST API
- **App:** FastAPI (Python 3.12, uv) + Next.js

## Licence
MIT for the code. Data sources and their licences are listed in [DATA_LICENSES.md](DATA_LICENSES.md).
