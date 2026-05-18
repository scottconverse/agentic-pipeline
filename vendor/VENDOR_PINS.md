# Vendor pins

Third-party source cloned on demand by `scripts/mem0_bootstrap.py` to
`vendor/<name>/`. We do not vendor the source verbatim into this repo
to avoid bloating the plugin payload; instead we pin a specific
commit and the CLI clones it on first use.

## Pins

| Name  | Repo                          | Pinned commit | Purpose                                  |
|-------|-------------------------------|---------------|------------------------------------------|
| mem0  | https://github.com/mem0ai/mem0 | latest `main` | OSS Mem0 server (Qdrant + Postgres stack) |

To refresh a pin: edit `MEM0_VENDOR_PIN` in `scripts/mem0_bootstrap.py`,
test against the new commit, commit the pin change.

## How `mem0 up` resolves the vendor

1. Read `pin` from `MEM0_VENDOR_PIN` constant in mem0_bootstrap.py
2. If `vendor/mem0/` does not exist, `git clone` mem0ai/mem0 into it
3. If it exists, `git fetch` and `git checkout <pin>`
4. `cd vendor/mem0/server && docker compose up -d`

The user can override the vendor location via the `oss.compose_dir`
field in `.mem0/config.json`. By default it points at `./vendor/mem0/server`.
