# Image Generation

Fill or update the embedded images in an existing plan `.html` file. Pick the sub-workflow based on the incoming `USER_PROMPT`:

| Sub-workflow | When to call it |
| --- | --- |
| Create | The prompt asks to generate, fill, or add the plan's images from scratch (empty `{{...IMAGE` slots) |
| Update | The prompt asks to change, refine, regenerate, or replace images that already exist in the plan |

## Backends

Two image backends, selected in this order:

1. **Explicit override** - `--renderer gpt` or `--renderer tldraw` if the `USER_PROMPT` names one.
2. **gpt-image** - used when `OPENAI_API_KEY` resolves (from the environment or `.env`). If `generate_gpt_image.py` exits non-zero (API error, no key, etc.), fall through to tldraw for that slot rather than failing.
3. **tldraw** - offline fallback, used when no key resolves or gpt-image failed.

| Backend | Create | Edit |
| --- | --- | --- |
| gpt-image (needs `OPENAI_API_KEY`) | `uv run scripts/generate_gpt_image.py "<prompt>" <output.png> --size 1536x1024 --quality high` | `uv run scripts/edit_gpt_image.py "<instruction>" <output.png> <input.png> --size 1536x1024 --quality high` |
| tldraw (needs tldraw Desktop running) | `uv run scripts/render_tldraw_image.py "<diagram spec>" <output.png> --size 1536x1024` | not supported — re-run Create for that slot |

When tldraw is the active backend it fills **every** slot (hero, problem, solution, phase, notes, questionables), rendering the hero as a titled overview diagram — consistency beats per-slot fidelity, a plan with some slots filled and others blank reads as broken.

Shared rules for every image prompt:

- always generate in wide format (`--size 1536x1024`) at high quality (`--quality high` for gpt-image)
- convey the one or two core ideas of that section for a professional software engineer
- match the plan's synced visual identity (professional, focused, minimal)
- keep total words shown in the image under 10
- save images to `IMAGES_OUTPUT_DIR` (create it if missing)

### Degrade, never fail

If neither backend is available (no `OPENAI_API_KEY` and tldraw Desktop not running), leave the `{{...IMAGE}}` placeholder comment in place for that slot, do not fail the step, and list the skipped slots in the step 5/3 report. Images aid the plan; they are not the plan.

## Create

1. Find slots - Grep the plan for `{{...IMAGE` placeholders (hero + per-phase). Each comment names the intended subject.
2. Write prompts - For each slot, write a prompt following the shared rules above.
3. Generate - Per the backend selection order above, run `generate_gpt_image.py` or `render_tldraw_image.py` once per slot, writing to `IMAGES_OUTPUT_DIR`. If a slot can't be filled by either backend, skip it and keep its placeholder comment.
4. Embed - For each filled slot, replace `<!-- {{...IMAGE: ...}} -->` with `<img src="<plan-name>/<file>.png" alt="...">`, keeping the existing `<figure>`/`<figcaption>`. Leave unfilled slots untouched.
5. Report - List the images generated, the slots filled, the backend used per slot, and any slots skipped because neither backend was available.

## Update

1. Identify targets - From the `USER_PROMPT`, determine which embedded `<img>` images to change.
2. Write instruction - Write an edit instruction describing the change, following the shared rules above.
3. Edit - If gpt-image is available (per the selection order above), run `edit_gpt_image.py` with the existing PNG as input, overwriting it (the script backs up the original first). tldraw has no edit mode — if gpt-image is unavailable, re-run `render_tldraw_image.py` (Create) for that slot's output path instead (it also backs up the original first), or leave the existing image in place and report it as skipped.
4. Verify embed - Confirm the `<img>` still points at the updated file; update `src`/`alt`/`<figcaption>` if the change warrants it.
5. Report - List the images updated, the backend used, and what changed (or which updates were skipped and why).
