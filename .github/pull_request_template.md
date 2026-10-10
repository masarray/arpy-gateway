## Workstream and contract
- Ownership lane (ARCH / STORE / GUI / MAPPING / MODBUS / IEC104 / IPC / SIM / QA):
- Linked issue:
- Contract version and ADR:
- Touched paths:

## Engineering evidence
- What is implemented, and what is explicitly not implemented:
- Thread safety, queue bounds, cancellation and failure cases:
- Control / simulation safety review:
- [ ] Contract + unit CI green
- [ ] Relevant GUI/integration tests
- [ ] Measured performance with environment/size, or justified N/A
- [ ] Documentation and fixtures updated for changes
- [ ] No device communication claimed without actual interop test
- [ ] No GUI-thread blocking I/O or auto-activation of physical controls
