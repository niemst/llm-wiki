Run an autoresearch loop against the wiki: pick an open question, research it, update the relevant page.

Usage: `/wiki-autoresearch [topic or slug]`

`$ARGUMENTS` — optional. If provided, narrow the scope to that topic or page slug. If empty, pick the oldest unresolved question automatically.

## Workflow

1. **Pick a target.** If `$ARGUMENTS` is empty, call `wiki_query` for "open questions" and "TODO" to surface candidates. Pick the one whose referenced entity has the lowest `confidence` (via `wiki_confidence`). Otherwise resolve `$ARGUMENTS` to a concrete page slug.

2. **State the question.** Print a one-line restatement of the question to answer. Ask the user to confirm before proceeding (simple y/n). If they decline, stop.

3. **Gather what you already know.** Call `wiki_read_page` on the target page + any pages it wikilinks to. Summarize internally the facts you already have, the gaps, and the questions that need external evidence.

4. **Search externally.** Use WebSearch for the gaps identified in step 3. Capture 3-5 high-quality hits. Prefer official docs, peer-reviewed papers, and reputable blogs over random forum posts.

5. **Synthesize findings.** Write a short synthesis (5-10 bullets) citing the URLs you used. Tag each fact with a confidence label: `[verified]` (official source), `[inferred]` (reasonable extrapolation), or `[unverified]` (single weak source).

6. **Update the wiki.** Append a `## Findings (YYYY-MM-DD)` section to the target page containing the synthesis. Do NOT overwrite existing content. If you found a contradiction with what's already there, add a one-line entry under a `## Contradictions` section (the existing lint rule will flag it).

7. **Queue the sources.** For every URL you used, append it to `~/brain/_inbox/articles.txt` so the next `llmwiki sync` ingests it via the `article` adapter (Patch 4c). That way the findings become cite-able wiki sources, not ephemeral chat context.

8. **Log it.** Append to `wiki/log.md`:
   `## [YYYY-MM-DD] autoresearch | <target-slug> | <one-line question>`

9. **Confidence refresh.** After the update, run `python3 -m llmwiki.confidence_backfill --only wiki/<subdir>/<slug>.md` so the target page's confidence is recomputed against its new source count + recency.

## Stop conditions

- User declines in step 2.
- Fewer than 2 external sources found → tell the user, don't update the wiki, suggest rephrasing the question.
- Target page has `frozen: true` in frontmatter → refuse and tell the user.

## Output format

End the response with a structured footer:
```
autoresearch: <target-slug>
sources added: N
findings written to: wiki/<subdir>/<slug>.md §Findings (YYYY-MM-DD)
inbox queue: ~/brain/_inbox/articles.txt (+N URLs)
```
