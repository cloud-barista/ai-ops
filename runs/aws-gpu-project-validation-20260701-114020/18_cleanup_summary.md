# CB-Tumblebug AWS GPU Cleanup Summary

## Target

- Namespace: default
- Infra ID: mc-gpu-small-test
- Node ID: nvidial4small-1
- AWS instance ID: i-0b45a6cc31fc00c5b
- Public IP: 34.212.5.116

## Cleanup actions

1. OpenBao was unsealed so CB-Tumblebug could access CSP credentials again.
2. CB-Tumblebug infra deletion was requested with `DELETE /tumblebug/ns/default/infra/mc-gpu-small-test?option=terminate`.
3. Infra status reached `Terminated:1 (R:0/1)`.
4. Final metadata delete reported that `mc-gpu-small-test` no longer exists.
5. Shared resources were released with `DELETE /tumblebug/ns/default/sharedResources`.

## Verification

- Infra list: `{"output":null}`
- Infra detail: `The infra mc-gpu-small-test does not exist.`
- SecurityGroup list: `{"output":null}`
- SSHKey list: `{"output":null}`
- VNet list: `{"output":null}`
- Post-delete SSH check: connection timed out

## Evidence files

- 12_status_after_unseal.json
- 13_delete_poll_status.json
- 14_delete_infra_final_response.json
- 15_release_shared_resources_response.json
- 16_verify_ns_default_infra_option_id.json
- 17_post_delete_ssh_check.txt