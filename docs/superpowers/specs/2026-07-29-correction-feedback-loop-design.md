# Correction Feedback Loop Design

## Context

Manual receipt review already captures the two sides of an extraction mistake:

- `raw_extraction`: what the model originally produced
- `corrected_payload`: what the reviewer approved

The category pipeline already has a feedback-like mechanism, but it learns from `ReceiptItem.embedding` rows. New item names are embedded with `text-embedding-3-small`, then pgvector cosine distance finds the closest historical item. If the closest item is within the configured threshold, its category is reused.

The gap is that review-approved corrections do not reliably become trusted category memory. During approval, corrected items are recreated from form data through `_replace_receipt_items(..., items=None)`, and `_payload_item_to_dataclass()` does not populate embeddings. That means a corrected category can be stored on the final item row without an embedding, making it unavailable as a strong future semantic match.

Amount and item extraction corrections are different. A corrected total, quantity, line total, added item, or removed item is valuable training/evaluation data, but it should not be blindly reused for future tickets because prices, quantities, totals, and item counts change per receipt.

## Goal

Implement the first feedback-loop slice:

- Approved corrected item names generate embeddings.
- Approved corrected categories become searchable future examples through existing pgvector category lookup.
- Raw-vs-corrected review payloads remain the golden source for later amount/item evals.

## Non-Goals

- Do not auto-apply old totals, quantities, line totals, or item counts to future receipts.
- Do not fine-tune a model in this slice.
- Do not change the receipt extraction prompt in this slice.
- Do not add a new correction dashboard or eval report in this slice.
- Do not add a new database table unless implementation shows the existing `ReceiptItem.embedding` field cannot support the category-memory use case.

## Recommended Approach

Use the existing `ReceiptItem` table as category memory.

When a staff reviewer approves a corrected receipt:

1. Build `corrected_payload` from the review form, as today.
2. Validate the corrected payload, as today.
3. If approval succeeds, convert corrected items into final `ReceiptItem` rows with embeddings generated from corrected item names.
4. Store those embeddings on the final approved items.
5. Future `find_nearest_category()` calls continue using the existing pgvector lookup, now with human-approved corrected examples included.

If approval is blocked and the receipt remains `needs_review`, do not generate embeddings. Only approved corrections should become trusted future examples.

## Alternatives Considered

Create a dedicated correction memory table. This would make provenance explicit and allow extra metadata such as approval time, reviewer, and raw-vs-corrected deltas. It is useful later, but it duplicates category lookup storage before the existing `ReceiptItem.embedding` mechanism has been fully used.

Feed corrected examples directly into the extraction prompt. This can help specific stores and item patterns, but dynamic prompt examples add token cost and need retrieval/eval controls. Category memory is cheaper and already aligned with the current architecture.

Fine-tune on approved corrections. This should wait until there is a measured golden dataset and enough examples. Fine-tuning without evals risks training on noisy or unrepresentative corrections.

## Data Flow

### Approval

1. `approve_review()` calls `_apply_review_action(..., approve=True, user=user)`.
2. `_apply_review_action()` builds `corrected_payload`.
3. `validate_receipt_extraction(corrected_payload)` blocks invalid approvals.
4. When `approved` is true:
   - corrected item names are embedded
   - final `ReceiptItem` rows store corrected category and embedding
5. `ReceiptExtractionReview.corrected_payload` remains saved for raw-vs-corrected analysis.

### Future Extraction

1. `process_file_task()` extracts a new item name.
2. `extract_info_service.find_nearest_category(item_name)` embeds that name.
3. `receipt_services.get_closest_match_receipt_item()` searches historical item embeddings.
4. If a corrected approved item is the closest match under threshold, its category is reused.

## Amount And Item Corrections

For totals, subtotals, discounts, quantities, line totals, added items, removed items, and missed rows, the first feedback-loop slice is data capture, not automatic reuse.

The existing review records already provide the necessary golden pair:

- prediction: `raw_extraction`
- approved answer: `corrected_payload`

Those pairs should be preserved and used by the next slice to calculate segmented evals:

- receipt scalar accuracy: total, subtotal, discount, store
- item extraction precision and recall
- quantity accuracy
- line-total accuracy
- category accuracy

## Failure Handling

Embedding generation is an external API call. Approval should not fail only because category-memory embedding failed.

If embedding generation fails for a corrected item:

- log the failure
- save the approved item with `embedding=None`
- continue approving the receipt

This keeps the review workflow reliable while allowing future retries or backfills.

## Testing

Add focused tests:

- Approval stores embeddings for corrected items when embedding generation succeeds.
- Approval stores the corrected category with that embedding.
- Approval still succeeds if embedding generation fails, with `embedding=None`.
- Blocked approval does not generate trusted embeddings.

Existing extraction task tests continue to cover that future category lookup consumes `find_nearest_category()` output.

## Follow-Up Slice

Build an eval/report command from `raw_extraction` and `corrected_payload` to quantify amount/item extraction mistakes. That is the right place to improve totals, quantities, missed rows, and prompt/rule behavior safely.
