# Stage 5 — First capability: places

One end-to-end slice, in English and Hebrew. The overview is in `SPECS.md`, and the
project invariants in `CLAUDE.md` still apply. This file is the detailed plan: what
gets built, the decisions already made, and the order of the steps.

Gate: the handoff prompt returns 3–5 grounded items inside the polygon, in English
and Hebrew, and the main agent never sees raw rows.

The handoff prompt is the README example, with this polygon:

```text
POLYGON((34.75 32.05, 34.80 32.05, 34.80 32.10, 34.75 32.10, 34.75 32.05))
EN: Find me a good Asian restaurant that is not too expensive.
HE: מצא לי מסעדה אסייתית טובה שלא יקרה מדי.
```

## 1. Decisions (settled before step 1)

| # | Topic | Decision |
| --- | --- | --- |
| D1 | Fixtures | Hand-authored now, in the exact Places API (New) response shape and marked `"_synthetic": true`. `scripts/record_places.py` overwrites them from the live API once a key is available. |
| D2 | Naming | The code's names stay: `load_tools`, `sources/`, `loaded_tools`. `SPECS.md` was updated to match. |
| D3 | Raw rows | Raw rows still go to `/turns/<t>/results/<source_id>/<k>.json`, but the tool message no longer shows the path. `read_file` stays allowed; the eval flags any read of a results file. |
| D4 | Language | Code decides, never the model: `he` if the prompt has Hebrew letters, else `en`. It is stored on the turn's `request`, and handlers (Google `languageCode`) and the summarizer both read it. |
| D5 | Item ids | Short ids that live for the whole conversation (`i1`, `i2`, …), assigned in code. The provider's id (a Google `place_id`, ~27 chars) is never shown to any model. One provider id always maps to one short id, so a place returned by search and then by details is the same item. |
| D6 | Summarizer | Code calls it on every result that has rows; the model never decides to. It makes plain model calls with an isolated message list and no tools. We don't use deepagents' `task` sub-agent, because that would let the model choose whether to summarize. |
| D7 | Data-only results | A small result with no rows (e.g. a single weather reading, ≤ `max_inline_result_chars`) skips the summarizer and passes through as today. It isn't "raw rows", and summarizing it would cost a model call for nothing. This departs from "every tool result" in `SPECS.md`; approved at the step 0 review. |

## 2. Data flow of one registry tool call

```text
model ──tool call──► DisclosureMiddleware (loaded?) ──► SummarizerMiddleware ──► resolver
                                                                                   │
  resolver (registry/, no LLM):                                                    │
    validate args → handler(args, ctx) → check output                              │
    → area filter: drop rows whose lon/lat fall outside the polygon (every tool)   │
    → items: rows that have `id` get short ids (state `items`, reducer)            │
    → write raw rows to /turns/<t>/results/<source_id>/<k>.json (path NOT shown)   │
    → ToolMessage(content=handler summary + counts, artifact=compact rows)         │
                                                                                   ▼
  SummarizerMiddleware (agent/, LLM):  rows in artifact?
    no  → pass through unchanged
    yes → chunk rows → summarize each chunk for the user's question → merge
        → strip [ids] that are not in this result → content = summary, artifact = None
                                                                                   │
model ◄──────────────── ToolMessage: "Found 14 places (3 outside the area removed).
                         [i2] Lotus Noodle Bar — 4.6, moderate prices ..."
```

`ToolMessage.artifact` is LangChain's slot for data that isn't sent to the model. It
carries the rows from the resolver to the summarizer, and the summarizer clears it so
the rows are never checkpointed in the message history.

## 3. Components

### 3.1 Request language (`request/`)

- `request/language.py: detect_language(prompt) -> Literal["en", "he"]`. Pure: the
  answer is `he` if any character is in U+0590–U+05FF.
- `RequestRef` gets `language`. `build_new_turn_state` fills it, for new
  conversations and follow-ups alike.
- `HandlerContext` gets `language`, taken from `state["request"]`.

### 3.2 Places provider (`providers/places.py`)

Providers are not handlers. They are the external service a handler calls, kept
behind one protocol so tests and evals never touch the network.

```python
class PlacesProvider(Protocol):
    def search(self, q: PlaceSearch) -> list[ProviderPlace]: ...
    def details(self, place_id: str, language: str) -> ProviderPlace | None: ...
```

- `PlaceSearch`: `text`, `language`, `rect` (the area's bbox), `max_results`,
  `min_rating`, `price_levels`, `open_now`.
- `GooglePlacesProvider` (httpx):
  - Text Search (New) is `POST places.googleapis.com/v1/places:searchText` with
    `locationRestriction.rectangle` = the area's bbox and `pageSize = max_results`
    (one page only).
  - Place Details (New) is `GET /v1/places/{id}`.
  - Both send `X-Goog-Api-Key` and an explicit `X-Goog-FieldMask` that asks only for
    the fields we map, which also keeps billing at the cheapest SKU that has them.
- `ReplayPlacesProvider` reads `fixtures/places/index.json`. Each entry holds a
  recorded request and its response file. Matching is deterministic:
  1. exact match on (endpoint, language, normalized text);
  2. otherwise the fixture with the same endpoint and language whose query shares the
     most normalized words with the request (at least one);
  3. otherwise empty results, plus a warning in the log.

  Step 2 is there because the real model words its queries freely ("asian food",
  "cheap asian restaurants"), and the evals must still replay.
- Fixtures: about 12 places around the handoff polygon, EN and HE, including 2–3
  outside the polygon (to exercise the area filter) and 2 expensive ones (so "not too
  expensive" matters). Each place has a details file.
- `PlacesConfig` (`GEOSEARCH_PLACES__*`): `provider: "google" | "replay"` (default
  `replay`, so a missing key never breaks a dev run), `api_key: SecretStr`,
  `base_url`, `timeout_s`, `max_results` (≤ 20), `fixtures_dir`. Startup fails if
  `provider=google` and no key is set.
- `httpx` moves from the dev group to the runtime dependencies.

### 3.3 Resolver: area filter, items, no path (`registry/resolver.py`)

- **Area filter, for every tool.** If the output model declares `lon` and `lat`,
  rows outside `area_ops.contains(area_id, lon, lat)` are dropped. This applies to
  artifact rows, and to `data` when it is a single row. The area comes from
  `state.conversation.area_id` whether or not the tool sets `uses_area`. The message
  says how many rows were dropped. Tools without coordinates are unaffected.
- **Items.** If the output model declares `id: str` (the provider's id), each kept
  row becomes an item:
  - the state field `items: Annotated[dict[str, ItemRecord], merge_items]` maps a
    short id to `{source_id, ref, row}`;
  - the reducer merges a later row into an existing item's `row` (details adds fields
    to what search found);
  - the short-id counter is the item count, kept in state; parallel calls in one step
    are handled like `_FileNumbers`.

  The compact rows the summarizer sees replace `id` with the short id and drop
  `lon`/`lat`, which are no use to it.
- **Item input.** `HandlerContext.item_ref(short_id) -> str | None` lets a handler
  turn `i3` back into the provider id. The details tool takes `item_id: str`.
- **No path in the message.** Files are still written; the `full result: …` line goes
  away. Stage 3/4 tests that assert on it are updated.
- `items` carries across turns (like `loaded_tools`) and is seeded `{}` on the first
  turn.

### 3.4 Places handlers (`sources/places.py`) and seed (`registry/seeds/places.yaml`)

| id | Input | Output row |
| --- | --- | --- |
| 9101 search | `query: str`, `min_rating: float \| None`, `max_price: Literal["inexpensive","moderate","expensive","very_expensive"] \| None`, `open_now: bool = False` | `id, name, address, lon, lat, rating, rating_count, price_level, primary_type` |
| 9102 details | `item_id: str` (an id from a search result, e.g. `i3`) | `id, name, address, lon, lat, phone, website, opening_hours, editorial_summary, rating, price_level` |

Both use `uses_area=True` and send the bbox as the restriction rectangle. The
provider comes from `get_places_provider(ctx.cfg.places)`, cached per config. Seed
descriptions are ≤ 200 chars and distinct from the scale fakes (transit stops,
parking).

### 3.5 Summarizer (`agent/summarizer.py`)

- `ResultSummarizer(model, cfg.summarizer)`. It gets a `BaseChatModel`, by default
  the same instance as the main agent; it never imports a provider (invariant 11).
- Input: the user's question (`state.request.prompt`), the language, the tool's
  catalog description, the handler's summary line, and the compact rows.
- Rows are rendered one per line, in order: `[i3] name=…; rating=4.6; price_level=MODERATE`.
- Chunking: rows are packed into chunks of ≤ `summarizer.chunk_tokens` (estimated
  with `count_tokens_approximately`). Each chunk is summarized on its own (map); if
  there was more than one, a merge call combines the partial summaries (reduce). At
  most `summarizer.max_chunks`; rows beyond that are left out, and the summary says
  how many.
- The prompt tells it to write in the request language, keep only what bears on the
  question, cite every item as `[id]`, and invent nothing.
- After every call, `strip_invalid_ids(text, valid)` removes `[x]` markers whose id is
  not among the rows given. This runs in code, never in the model.
- Each call is written to the ledger as a `role="summarizer"` record. `UsageSummary`
  counts summarizer calls separately, so the main agent's budget numbers stay
  comparable to Stage 4.
- `SummarizerMiddleware.wrap_tool_call` sits after `DisclosureMiddleware`. It only
  touches results whose ToolMessage has rows in its artifact (D7). If the summarizer
  fails, it falls back to the handler summary plus the cited ids, with no rows.
- `SummarizerConfig` (`GEOSEARCH_SUMMARIZER__*`): `chunk_tokens` (default 2,500),
  `max_chunks` (default 4), `max_output_tokens` (default 400).

### 3.6 submit_answer, reminder, fallback (`agent/tools/answer.py`, `agent/answer.py`)

- `submit_answer(text: str, item_ids: list[str])` is a core tool with
  `return_direct=True`, so a successful submit ends the turn.
  - Unknown ids return an error that lists them, so the model can retry, and nothing
    is stored.
  - On success, the `[ids]` in `text` that are not in `item_ids` (or not known) are
    stripped, and `answer: {text, item_ids}` is stored in state (reset every turn).
- `SubmitAnswerMiddleware.after_model` (`can_jump_to=["model"]`): when the model
  replies without tool calls and nothing has been submitted, it appends one reminder
  ("Finish by calling submit_answer(text=..., item_ids=[...]).") and jumps back to
  the model. After one reminder it lets the turn end.
- Fallback in `invoke_turn`: with no submitted answer (no submit after the reminder,
  or a call-limit stop), the answer is the last AI text with invalid ids stripped,
  and the items are the valid ids cited in it.
- `AgentResponse` gets:
  - `items: list[ResponseItem]`, where `ResponseItem` is
    `{id, source_id, name, lon, lat, data}`, built from `state.items` (data, never
    model text). The provider ref stays server-side.
  - `answer_source: "submitted" | "fallback"`.
- Prompt: two short lines. Results are summaries citing items as `[id]`; finish with
  `submit_answer(text=..., item_ids=[...])` and cite only ids from results. Named
  arguments, per the Stage 4 Ollama finding.
- `TRIMMED_TOOLS` gets `submit_answer`.

## 4. Steps

Each step is one reviewed commit (`stage5: step N — …`), with `uv run pytest` and
`uv run ruff check .` green.

| Step | Content | Tests |
| --- | --- | --- |
| 0 | This spec. | — |
| 1 | `PlacesConfig`, `SummarizerConfig`; `detect_language`; `RequestRef.language`, `HandlerContext.language`; `httpx` as a runtime dependency. | config validation, language detection (EN/HE/mixed/digits), turn state carries the language |
| 2 | `providers/places.py`: protocol, Google (httpx), Replay; hand-authored fixtures; `scripts/record_places.py`. | Google request shape via `httpx.MockTransport` (URL, headers, field mask, rectangle, languageCode); response mapping; replay exact, overlap and miss |
| 3 | Resolver: area filter for every tool; `items` state, reducer, short ids, `item_ref`; artifact rows; no path line. | outside rows dropped and counted; tools without lon/lat untouched; ids are stable across calls and turns; details merges into the same item; provider id never in content; existing resolver tests updated |
| 4 | `sources/places.py` handlers 9101/9102 + `registry/seeds/places.yaml` + `MODULES`. | handlers against Replay; output models pass `check_output`; seed validates; descriptions ≤ 200 chars |
| 5 | `ResultSummarizer` + `SummarizerMiddleware`; ledger `role`. | chunk packing; map-reduce call count; invalid ids stripped; language passed through; failure fallback; artifact cleared; data-only results pass through |
| 6 | `submit_answer`, `SubmitAnswerMiddleware`, fallback, `AgentResponse.items/answer_source`, prompt. | unknown ids rejected; `return_direct` ends the turn; exactly one reminder; fallback after the reminder and after the call limit; items built from state |
| 7 | Scripted end-to-end suite (EN + HE) and a `stage5` eval suite (replay provider, seeded `geosearch_eval`), plus a report. | see §5 |
| 8 | Docs: Stage 5 invariants and verified APIs in `CLAUDE.md`, README config table, `SPECS.md` Stage 5 done. | — |

## 5. Gate checks

Scripted (pytest, no model) and eval (gemma4:12b, replay fixtures). Both run the
EN and HE handoff prompts:

- `answer_source == "submitted"` (the eval reports the fallback rate);
- 3–5 response items, every one a known item, every one inside the polygon
  (`AreaOps.contains`);
- no main-agent model call contains a provider id or a raw-row field string from the
  fixtures, and none calls `read_file` on `/turns/*/results/` (D3);
- the HE answer is mostly Hebrew (share of letters in U+0590–U+05FF > 50%);
- the token ledger: main-agent peak input stays within budget; summarizer calls are
  reported separately;
- the Stage 2 and Stage 4 suites still pass.

## 6. Library behavior to verify in-step

- Present in the langchain 1.4.3 source, still to be confirmed by tests in step 6:
  `return_direct=True` routes to the exit after the tool node, and
  `@hook_config(can_jump_to=["model"])` works on `after_model`.
- Step 3: `ToolMessage.artifact` survives a `Command` update and is not sent to the
  model by ChatOllama.

## 7. Out of scope

Ranking and scores, relaxation and sufficiency (Stage 6), pagination beyond one page,
response caching (Stage 8), and a separate summarizer model (possible later as config
only).
