# Lab guide: Your Agent Doesn't Get Root

The attendee-facing lab guide for the GTC Berlin 2026 X-Lab, BT61191. Static site, no build step, deployed to Cloudflare Pages.

This is the page attendees are pointed at from slide 2 of the deck via `https://go.n8n.io/E5WqJP`.

## Deploy

```bash
npx wrangler login
npx wrangler pages deploy . --project-name=agent-root-lab
```

Same project name means the same URL. `_headers` sets `no-cache` on the shell, so a redeploy lands immediately rather than fighting a stale copy.

Dashboard alternative: Workers and Pages, Create, Pages, Upload assets, then drag this folder in. Framework preset none, build command empty, output directory `/`.

## Files

| Path | What it is |
|---|---|
| `index.html` | The whole guide. Self-contained, no build |
| `_headers` | Security headers, `no-cache` on the shell, `Content-Disposition: attachment` on `/files/*` |
| `files/` | The workflows and CSVs attendees download |
| `package.json` | `npm run deploy` and `npm run dev` |
| `.stale/` | Superseded files, kept for reference. The leading dot means Cloudflare Pages skips the folder, so nothing in here deploys |

### files/

| File | Used in |
|---|---|
| `mcp-server-incident_tools.json` | Exercise 2, the workflow they import |
| `approval-dashboard.json` | Exercise 3, the approval dashboard |
| `incidents.csv` | Data table, 40 incidents |
| `pending_actions.csv` | Data table, the approval queue, header row only |
| `runbooks.csv` | Data table, remediation steps by category |

The `_headers` rule on `/files/*` forces a download rather than rendering JSON and CSV in the browser tab, which is what you want when the next instruction is "import this file".

## Known gaps

- `mcp-server-incident_tools-completed.json` is not here. There is no fallback download for anyone who falls behind.
- `incidents.csv` and `runbooks.csv` were salvaged from the old `4-seed-data` folder before it was deleted. Replace them with the copies from `n8n/datatables` and re-verify.
- The wifi network and password are not on the page yet.
- The NemoClaw docs URL is unverified.

## Editing

One file, plain HTML. Two conventions to keep.

**Copyable blocks.** Anything attendees have to type or paste goes in a block. The Copy button and header bar are added by JS at load, so the markup is just this:

```html
<div class="block" data-label="Prompt"><pre>text they copy</pre></div>
```

- `class="block prose"` renders the text in Manrope instead of mono, for agent prompts and tool descriptions
- `data-nocopy` suppresses the Copy button, used for the expected-output block
- `data-label` is the grey label in the block header
- For a value inside a config table, put `data-copy="the value"` on the `td` and an inline copy link appears

**Step progress.** Each step heading is wrapped in a `.step` div with a checkbox carrying a unique `data-step`. Progress saves to `localStorage` in the reader's browser, wrapped in try/catch so a locked-down browser degrades quietly. Nothing is sent anywhere. There is a clear button at the end of the guide.

Adding a step means adding both the section and its nav entry. The nav numbers are hand-written, so renumbering is manual.

## Design

Follows the n8n brand: `#040506` base, `#EA4B71` pink accent, white, Manrope with JetBrains Mono for anything typed. No gradients. The three CDN origins in the CSP are Google Fonts CSS, Google Fonts files, and nothing else. No JS libraries.

Dark only, and readable at phone width, because a chunk of the room will follow along on a phone while both laptop windows are full of terminals.
