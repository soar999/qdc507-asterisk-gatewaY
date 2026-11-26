# Publication review

- Original private review ZIP SHA256: `18d072513726fc1befa413804986c20f00cc896de68119ab795283cd16bc3a47`.
- ZIP CRC verification passed; all 35/35 source records match their original SHA256 and size, with no unlisted files.
- Literal device serial in the active AGI wrapper replaced with a placeholder. Private prestage instance directory suffix replaced in the runtime and activation record. Original pins are retained; public digests are listed separately in the manifest. All Python source parsed and shell syntax checked after redaction; these are syntax checks, not hardware/runtime tests.
- Reviewed source for device identifiers, credentials, IP literals, private keys, access tokens, and private state. No credential values intentionally included. Automated scans and manual review cannot guarantee that every possible private value has been removed; review before public merge.
- The archive intentionally excludes original ZIP, private prestage directory contents, actual SIM identity, full dialplan/config, runtime state, pending/call/SMS contents, built drivers, and Docker layers. Original production dependencies are not fully reproducible from this archive.
- The inherited notes field of the production manifest still describes the previous release. Current status is recorded by the acceptance object and this checkpoint's evidence. Historical hashes are preserved, not recomputed as production pins.
- Historical 2026-11-21 and earlier checkpoints remain unchanged. Publish on a review branch before merging to main.
