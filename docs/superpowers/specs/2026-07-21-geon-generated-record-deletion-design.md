# geon generated record deletion design

## Goal

Extend Agent Control so operators can remove records created inside geon without touching AppDeploy, CB-Tumblebug, or cloud VM resources.

## Ownership boundary

The UI exposes deletion only for records owned by the current geon process or browser:

- Runtime Agents registered through the geon API
- Autonomous Loop event records
- Execution Feedback records accepted by geon
- Recent control results stored in browser `localStorage`

Configuration-backed Agents remain visible without a deletion control. AppDeploy Apps, deployments, profiles, targets, CB-Tumblebug infrastructure, and VMs remain out of scope.

## Deletion behavior

- Runtime Agents retain the existing individual deletion control.
- Autonomous Loop events support individual deletion by monotonic event sequence and clear-all.
- Execution Feedback supports list, individual deletion by correlation ID, and clear-all.
- Recent control results support individual deletion by browser-local record ID and clear-all.
- Every destructive UI action asks for confirmation and updates the screen only after success.
- Server-side DELETE routes use the existing loopback/admin-token mutation guard.

Deleting an event does not renumber later events. Deleting a Feedback record does not remove the approved correlation registration, so a later valid Feedback submission for the same operation can be recorded again.

## API surface

- `DELETE /api/v1/autonomy/events/{sequence}`
- `GET /api/v1/automation/feedback`
- `DELETE /api/v1/automation/feedback/{correlation_id}`
- `DELETE /api/v1/automation/feedback`

Missing individual records return `404`. Successful individual deletion returns the deleted record. Clear-all responses include `deleted=true` and `deleted_count`.

## UI

Timeline, Feedback records, and Recent control results each render a trash icon on deletable rows and a clear-all icon in the section header. Existing Configuration Agent rows have neither a trash icon nor a protection label.

## Verification

- Domain tests cover deletion, not-found behavior, ordering, and sequence preservation.
- Feedback tests verify clear-all and re-recording after deletion.
- API tests cover routes, responses, and remote mutation authentication.
- Embedded UI contract tests cover controls, handler wiring, and JavaScript syntax.
- Desktop and mobile browser checks confirm usable controls and no horizontal overflow.

